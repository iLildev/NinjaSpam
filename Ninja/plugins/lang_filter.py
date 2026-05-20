"""
plugins/lang_filter.py — Script-based language filter for groups.

Detects the dominant Unicode script in each message and deletes messages
that don't belong to the allowed script set.  Uses character-range analysis
(no external library needed) so it works for Arabic, Chinese/CJK, and Latin
(which covers French, English, Spanish, Turkish, etc.).

Short messages, commands, URLs, media-only messages, and admins are exempted.

Commands (admins only):
  /langfilter <script(s)>   — Enable and set allowed scripts
                              Values: arabic, latin, cjk, arabic+latin, all
  /langfilter off            — Disable
  /langfilter action <act>  — Set action: delete | warn | mute
  /langfilter status        — Show current config

Script tokens:
  arabic   — Arabic / Persian / Urdu  (U+0600-U+06FF and related blocks)
  latin    — Latin-based: French, English, Spanish, German, Turkish, Indonesian …
  cjk      — Chinese, Japanese, Korean  (CJK Unified Ideographs + Hiragana/Katakana)
  all      — Disable script restriction (same as /langfilter off)

Combine with '+': /langfilter arabic+latin  (allows both scripts)
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Set

from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import bot_admin, is_user_admin, user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import LangFilterSettings

logger = logging.getLogger(__name__)

# Minimum meaningful letter count before we bother checking
_MIN_LETTERS = 5

# Regex to strip URLs before language analysis
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Script detection
# ---------------------------------------------------------------------------

def _count_arabic(text: str) -> int:
    return sum(
        1 for c in text
        if (
            "\u0600" <= c <= "\u06FF"   # Arabic
            or "\u0750" <= c <= "\u077F"  # Arabic Supplement
            or "\uFB50" <= c <= "\uFDFF"  # Arabic Presentation Forms-A
            or "\uFE70" <= c <= "\uFEFF"  # Arabic Presentation Forms-B
            or "\u0600" <= c <= "\u06FF"  # Arabic
        )
    )


def _count_cjk(text: str) -> int:
    return sum(
        1 for c in text
        if (
            "\u4E00" <= c <= "\u9FFF"   # CJK Unified Ideographs
            or "\u3400" <= c <= "\u4DBF"  # CJK Extension A
            or "\uF900" <= c <= "\uFAFF"  # CJK Compatibility Ideographs
            or "\u3040" <= c <= "\u30FF"  # Hiragana + Katakana
            or "\uAC00" <= c <= "\uD7AF"  # Hangul Syllables (Korean)
        )
    )


def _count_latin(text: str) -> int:
    return sum(
        1 for c in text
        if (
            "A" <= c <= "Z"
            or "a" <= c <= "z"
            or "\u00C0" <= c <= "\u024F"  # Latin Extended (accented chars for French, etc.)
            or "\u1E00" <= c <= "\u1EFF"  # Latin Extended Additional
        )
    )


def detect_dominant_scripts(text: str) -> Set[str]:
    """
    Return the set of scripts that appear significantly in *text*.

    A script is considered 'present' if it accounts for ≥25% of all
    identifiable script characters.  A message can trigger multiple scripts
    (e.g. Arabic text with Latin digits).
    """
    clean = _URL_RE.sub("", text)
    arabic = _count_arabic(clean)
    cjk    = _count_cjk(clean)
    latin  = _count_latin(clean)
    total  = arabic + cjk + latin

    if total < _MIN_LETTERS:
        return {"unknown"}

    found: Set[str] = set()
    threshold = total * 0.25
    if arabic >= threshold:
        found.add("arabic")
    if cjk >= threshold:
        found.add("cjk")
    if latin >= threshold:
        found.add("latin")
    return found if found else {"other"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> LangFilterSettings:
    row = await session.get(LangFilterSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = LangFilterSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


def _parse_scripts(raw: str) -> Optional[Set[str]]:
    """Parse user input like 'arabic+latin' into a frozenset."""
    valid = {"arabic", "latin", "cjk"}
    parts = {p.strip().lower() for p in raw.replace("+", " ").split()}
    if parts == {"all"}:
        return None  # means disabled
    if not parts.issubset(valid):
        return False  # invalid input
    return parts


def _scripts_label(scripts_csv: str, lang: str) -> str:
    """Human-readable label for the allowed-scripts CSV."""
    name_map = {
        "arabic": {"en": "Arabic", "ar": "العربية", "fr": "Arabe", "zh": "阿拉伯语"},
        "latin":  {"en": "Latin (French/English/…)", "ar": "اللاتينية", "fr": "Latin", "zh": "拉丁字母"},
        "cjk":    {"en": "Chinese/Japanese/Korean", "ar": "صيني/ياباني/كوري", "fr": "Chinois/Japonais/Coréen", "zh": "中文/日文/韩文"},
    }
    labels = []
    for script in scripts_csv.split(","):
        script = script.strip()
        labels.append(name_map.get(script, {}).get(lang, script))
    return " + ".join(labels) if labels else "—"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def cmd_langfilter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        if not args:
            await update.message.reply_text(t("langfilter_usage", lang), parse_mode=ParseMode.HTML)
            return

        sub = args[0].lower()

        if sub == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(t("langfilter_off", lang), parse_mode=ParseMode.HTML)
            return

        if sub == "status":
            state = t("enabled", lang) if cfg.enabled else t("disabled", lang)
            allowed_label = _scripts_label(cfg.allowed_scripts, lang) if cfg.allowed_scripts else "—"
            await update.message.reply_text(
                t("langfilter_status", lang,
                  state=state,
                  allowed=allowed_label,
                  action=cfg.action),
                parse_mode=ParseMode.HTML,
            )
            return

        if sub == "action":
            if len(args) < 2 or args[1].lower() not in ("delete", "warn", "mute"):
                await update.message.reply_text(
                    "Usage: /langfilter action delete|warn|mute",
                    parse_mode=ParseMode.HTML,
                )
                return
            cfg.action = args[1].lower()
            await session.commit()
            await update.message.reply_text(t("done", lang), parse_mode=ParseMode.HTML)
            return

        # /langfilter arabic  /  /langfilter arabic+latin  etc.
        parsed = _parse_scripts(sub)
        if parsed is False:
            await update.message.reply_text(t("langfilter_invalid", lang), parse_mode=ParseMode.HTML)
            return
        if parsed is None:
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(t("langfilter_off", lang), parse_mode=ParseMode.HTML)
            return

        cfg.enabled = True
        cfg.allowed_scripts = ",".join(sorted(parsed))
        await session.commit()

        allowed_label = _scripts_label(cfg.allowed_scripts, lang)
        await update.message.reply_text(
            t("langfilter_on", lang, allowed=allowed_label),
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Message filter handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return

    # Skip commands
    if message.text.startswith("/"):
        return

    async with get_session() as session:
        cfg = await session.get(LangFilterSettings, chat.id)
        if not cfg or not cfg.enabled or not cfg.allowed_scripts:
            return

    # Admins are exempt
    if await is_user_admin(chat, user.id):
        return

    allowed: Set[str] = set(cfg.allowed_scripts.split(","))
    detected = detect_dominant_scripts(message.text)

    # "unknown" / "other" (e.g. emoji-only) → skip
    if "unknown" in detected or "other" in detected:
        return

    # Message is fine if at least one detected script is in the allowed set
    if detected & allowed:
        return

    # ── Violation ────────────────────────────────────────────────────────────
    lang = await get_chat_lang(chat.id)
    allowed_label = _scripts_label(cfg.allowed_scripts, lang)
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

    if cfg.action in ("delete", "warn"):
        try:
            notice = await message.reply_text(
                t("langfilter_deleted", lang, mention=mention, allowed=allowed_label),
                parse_mode=ParseMode.HTML,
            )
            # Auto-delete notice after 10 seconds
            context.job_queue.run_once(
                lambda _ctx: _ctx.bot.delete_message(chat.id, notice.message_id),
                when=10,
            )
        except TelegramError:
            pass

    elif cfg.action == "mute":
        try:
            await chat.restrict_member(
                user.id,
                ChatPermissions(can_send_messages=False),
            )
            await message.reply_text(
                t("langfilter_deleted", lang, mention=mention, allowed=allowed_label),
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError) as exc:
            logger.warning("lang_filter: mute failed for %d: %s", user.id, exc)

    logger.info(
        "lang_filter: deleted msg in chat %d user %d (detected=%s allowed=%s)",
        chat.id, user.id, detected, allowed,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("langfilter", cmd_langfilter, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )
    logger.info("Plugin loaded: lang_filter")
