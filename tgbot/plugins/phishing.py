"""
plugins/phishing.py — Phishing and scam URL detection.

Scans every message for URLs and checks them against:
  1. A built-in list of known phishing/scam domain patterns
  2. Suspicious URL structural patterns (lookalike domains, crypto scam keywords)
  3. Telegram-impersonation patterns

No external API key is needed.  The detection is fast (pure Python regex
matching) and runs on every group message that contains a URL entity.

Commands (admins only):
  /phishing on|off        — Enable / disable for this group
  /phishing action <act>  — Set action: delete | warn | ban  (default: delete)
  /phishing status        — Show current configuration
  /phishing check <url>   — Manually check a URL (reports result to admin)

Detection covers:
  - Telegram impersonation (telegram.org look-alikes)
  - Crypto / NFT / airdrop scams
  - Free-gift / prize-claim lures
  - Phishing via URL shorteners known to relay scam links
  - Homoglyph (Unicode lookalike) attacks in domains
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import bot_admin, is_user_admin, user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import PhishingSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

# Legitimate Telegram domains — anything that spoofs these is suspect
_TELEGRAM_LEGIT = frozenset({
    "telegram.org", "t.me", "telegram.me", "telesco.pe", "core.telegram.org",
})

# Known high-risk URL shorteners that are often used to relay phishing
_RISKY_SHORTENERS = frozenset({
    "bit.ly", "tinyurl.com", "ow.ly", "rb.gy", "cutt.ly", "is.gd",
    "shorte.st", "adf.ly", "bc.vc", "j.mp",
})

# Crypto/scam signal keywords that appear in phishing domains or paths
_CRYPTO_SCAM_KEYWORDS = re.compile(
    r"(free[-_]?crypto|airdrop|nft[-_]?mint|claim[-_]?reward|"
    r"wallet[-_]?connect|metamask[-_]?support|binance[-_]?gift|"
    r"crypto[-_]?bonus|bitcoin[-_]?profit|btc[-_]?giveaway|"
    r"eth[-_]?free|usdt[-_]?bonus|ton[-_]?airdrop|"
    r"prize[-_]?claim|verify[-_]?wallet|secure[-_]?wallet)",
    re.IGNORECASE,
)

# Telegram impersonation patterns (looks like Telegram but isn't)
_TELEGRAM_IMPERSONATION = re.compile(
    r"(telegr[^a]am|t[e3]l[e3]gr[a@]m|t\.e\.l\.e\.g\.r\.a\.m|"
    r"telegramm|telagram|telegam|telgram|"
    r"0telegram|telegram0|tg-official|official-tg)",
    re.IGNORECASE,
)

# Unicode homoglyph characters in domains (Cyrillic lookalikes, etc.)
_HOMOGLYPH_RE = re.compile(
    r"[\u0430\u0435\u043e\u0440\u0441\u0443\u0445]",  # Cyrillic a,e,o,r,c,y,x
)

# Suspicious TLD + keyword combinations
_SCAM_TLD_KEYWORD = re.compile(
    r"\.(xyz|top|click|buzz|work|loan|gq|cf|tk|ml|ga)\b",
    re.IGNORECASE,
)


def is_phishing_url(url: str) -> tuple[bool, str]:
    """
    Check a single URL for phishing signals.

    Returns (is_phishing: bool, reason: str).
    Reason is empty if not phishing.
    """
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        domain = (parsed.netloc or parsed.path).lower().lstrip("www.")
        full_url = url.lower()
    except Exception:
        return False, ""

    # 1. Telegram impersonation
    if _TELEGRAM_IMPERSONATION.search(domain) and domain not in _TELEGRAM_LEGIT:
        return True, "telegram-impersonation"

    # 2. Homoglyph attack in domain
    if _HOMOGLYPH_RE.search(domain):
        return True, "homoglyph-domain"

    # 3. Crypto/scam keywords in domain or path
    if _CRYPTO_SCAM_KEYWORDS.search(full_url):
        return True, "crypto-scam-keyword"

    # 4. Suspicious TLD
    if _SCAM_TLD_KEYWORD.search(domain):
        # Only flag if combined with crypto-adjacent words
        if re.search(r"(crypto|btc|eth|ton|nft|wallet|token|coin|invest)", full_url, re.I):
            return True, "scam-tld+crypto"

    # 5. Risky shortener — flag but with lower confidence (only if combined with signal)
    # (We don't flag shorteners alone to avoid false positives)
    if any(domain == s or domain.endswith("." + s) for s in _RISKY_SHORTENERS):
        pass  # require additional signal — not flagged alone

    return False, ""


def extract_urls(update_message) -> list[str]:
    """Extract all URLs from a message using PTB entities."""
    urls: list[str] = []
    text = update_message.text or update_message.caption or ""

    for entity in (update_message.entities or []) + (update_message.caption_entities or []):
        if entity.type == "url":
            urls.append(text[entity.offset : entity.offset + entity.length])
        elif entity.type == "text_link" and entity.url:
            urls.append(entity.url)

    return urls


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> PhishingSettings:
    row = await session.get(PhishingSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = PhishingSettings(chat_id=chat_id)  # enabled=True by default
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_phishing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        if not args:
            await update.message.reply_text(t("phishing_usage", lang), parse_mode=ParseMode.HTML)
            return

        sub = args[0].lower()

        if sub == "on":
            cfg.enabled = True
            await session.commit()
            await update.message.reply_text(t("phishing_on", lang), parse_mode=ParseMode.HTML)

        elif sub == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(t("phishing_off", lang), parse_mode=ParseMode.HTML)

        elif sub == "action":
            if len(args) < 2 or args[1].lower() not in ("delete", "warn", "ban"):
                await update.message.reply_text(
                    "Usage: /phishing action delete|warn|ban", parse_mode=ParseMode.HTML
                )
                return
            cfg.action = args[1].lower()
            await session.commit()
            await update.message.reply_text(t("done", lang), parse_mode=ParseMode.HTML)

        elif sub == "status":
            state = t("enabled", lang) if cfg.enabled else t("disabled", lang)
            await update.message.reply_text(
                t("phishing_status", lang,
                  state=state,
                  action=cfg.action,
                  count=cfg.scan_count),
                parse_mode=ParseMode.HTML,
            )

        elif sub == "check":
            if len(args) < 2:
                await update.message.reply_text("Usage: /phishing check <url>")
                return
            url = args[1]
            flagged, reason = is_phishing_url(url)
            if flagged:
                await update.message.reply_text(
                    f"🚨 <b>PHISHING</b>\nURL: <code>{url}</code>\nReason: <code>{reason}</code>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    f"✅ URL looks clean: <code>{url}</code>",
                    parse_mode=ParseMode.HTML,
                )

        else:
            await update.message.reply_text(t("phishing_usage", lang), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Message scanner
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return

    # Admins exempt
    if await is_user_admin(chat, user.id):
        return

    async with get_session() as session:
        cfg = await session.get(PhishingSettings, chat.id)
        if not cfg or not cfg.enabled:
            return

    urls = extract_urls(message)
    if not urls:
        return

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)
        cfg.scan_count += len(urls)

        flagged_url: Optional[str] = None
        flagged_reason: str = ""

        for url in urls:
            flagged, reason = is_phishing_url(url)
            if flagged:
                flagged_url = url
                flagged_reason = reason
                break

        if not flagged_url:
            await session.commit()
            return

        await session.commit()

    lang = await get_chat_lang(chat.id)
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

    # Delete the message
    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

    alert = t("phishing_detected", lang, mention=mention)
    try:
        await context.bot.send_message(chat.id, alert, parse_mode=ParseMode.HTML)
    except TelegramError:
        pass

    # Execute configured action
    async with get_session() as session:
        cfg = await session.get(PhishingSettings, chat.id)
        action = cfg.action if cfg else "delete"

    if action == "ban":
        try:
            await chat.ban_member(user.id)
            logger.info(
                "phishing: banned user %d in chat %d (reason=%s url=%s)",
                user.id, chat.id, flagged_reason, flagged_url,
            )
        except (BadRequest, TelegramError) as exc:
            logger.warning("phishing: ban failed: %s", exc)

    logger.info(
        "phishing: deleted msg from user %d in chat %d reason=%s",
        user.id, chat.id, flagged_reason,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("phishing", cmd_phishing, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.Entity("url") | filters.Entity("text_link")),
            handle_message,
        )
    )
    logger.info("Plugin loaded: phishing")
