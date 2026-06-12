"""
plugins/homoglyph.py — Mixed-script (homoglyph) attack detection (TG-spam inspired).

Detects messages where individual words mix Cyrillic and Latin characters —
e.g. "сom" (Cyrillic с + Latin o,m), "ргоfit" (Cyrillic р,г,о + Latin f,i,t).
This is a classic technique to bypass keyword blacklists while remaining
visually identical to legitimate text.

Detection logic:
  For each word with len >= 3, check if it contains characters from BOTH
  the Cyrillic block AND the Basic Latin/Extended Latin blocks.
  If more than MIN_MIXED_WORDS words in the message are mixed-script → flag.

Commands (admins only):
  /homoglyph on|off
  /homoglyph action <delete|warn|ban>
  /homoglyph status
"""

from __future__ import annotations

import logging
import unicodedata

from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import HomoglyphSettings

logger = logging.getLogger(__name__)

HOMOGLYPH_GROUP: int = 16

_MIN_MIXED_WORDS = 1   # Even 1 mixed word is suspicious
_MIN_WORD_LEN = 3      # Ignore very short words

_MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
)


def _script_of(char: str) -> str:
    """Return the Unicode script block name for a character (simplified)."""
    name = unicodedata.name(char, "")
    if "CYRILLIC" in name:
        return "cyrillic"
    if "LATIN" in name:
        return "latin"
    return "other"


def _has_mixed_scripts(word: str) -> bool:
    """Return True if the word contains both Cyrillic and Latin characters."""
    has_cyr = False
    has_lat = False
    for ch in word:
        if ch.isalpha():
            s = _script_of(ch)
            if s == "cyrillic":
                has_cyr = True
            elif s == "latin":
                has_lat = True
            if has_cyr and has_lat:
                return True
    return False


def _count_mixed_words(text: str) -> int:
    """Count how many words in text mix Cyrillic and Latin characters."""
    words = text.split()
    return sum(
        1 for w in words
        if len(w) >= _MIN_WORD_LEN and _has_mixed_scripts(w)
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_cfg(session, chat_id: int) -> HomoglyphSettings:
    row = await session.get(HomoglyphSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = HomoglyphSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# /homoglyph command
# ---------------------------------------------------------------------------

@user_admin
async def cmd_homoglyph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_cfg(session, chat.id)

        if args and args[0].lower() == "on":
            cfg.enabled = True
            await session.commit()
            await update.message.reply_text(
                f"🔡 <b>Homoglyph Detection</b> — <b>Enabled</b>\n"
                f"Action: <b>{cfg.action}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(
                "🔡 <b>Homoglyph Detection</b> — <b>Disabled</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if args and args[0].lower() == "action" and len(args) > 1:
            act = args[1].lower()
            if act not in ("delete", "warn", "ban"):
                await update.message.reply_text("❌ Actions: delete | warn | ban")
                return
            cfg.action = act
            await session.commit()
            await update.message.reply_text(
                f"✅ New action: <b>{act}</b>", parse_mode=ParseMode.HTML
            )
            return

        state = "✅ Enabled" if cfg.enabled else "❌ Disabled"
        await update.message.reply_text(
            f"🔡 <b>Homoglyph Detection — Status</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Status: {state}\n"
            f"⚡ Action: <b>{cfg.action}</b>\n\n"
            f"<i>Detects words that mix Cyrillic and Latin characters.\n"
            f"Example: с+o+m = 'сom' looks like 'com' but it's different.</i>",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

async def check_homoglyph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return
    text = message.text or message.caption or ""
    if not text or len(text) < 4:
        return
    if await is_user_admin(chat, user.id):
        return

    async with get_session() as session:
        cfg = await session.get(HomoglyphSettings, chat.id)
        if not cfg or not cfg.enabled:
            return
        action = cfg.action

    mixed_count = _count_mixed_words(text)
    if mixed_count < _MIN_MIXED_WORDS:
        return

    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    logger.info(
        "homoglyph: %d mixed-script words in msg from user %d chat %d",
        mixed_count, user.id, chat.id,
    )

    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

    if action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat.id,
                f"🔡 {mention} banned — message contains mixed characters (homoglyph attack).",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError) as e:
            logger.warning("homoglyph: ban failed: %s", e)

    elif action == "warn":
        await context.bot.send_message(
            chat.id,
            f"⚠️ Warning for {mention}: suspicious message (mixed Cyrillic+Latin characters).",
            parse_mode=ParseMode.HTML,
        )

    else:
        await context.bot.send_message(
            chat.id,
            f"🔡 {mention}'s message deleted — contains mixed characters (filter bypass attempt).",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("homoglyph", cmd_homoglyph, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            check_homoglyph,
            block=False,
        ),
        group=HOMOGLYPH_GROUP,
    )
    logger.info("Plugin loaded: homoglyph")
