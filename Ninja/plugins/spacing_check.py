"""
plugins/spacing_check.py — Abnormal spacing / evasion detection (TG-spam inspired).

Spammers insert spaces between characters ("h e l l o w o r l d") to bypass
keyword filters while remaining human-readable. This plugin detects messages
where the ratio of spaces is abnormally high relative to content length.

Detection logic (both conditions must hold):
  1. space_ratio  = spaces / total_chars  > 0.35
  2. short_ratio  = words_len<=2 / total_words > 0.6
  3. total text   >= MIN_LEN characters (avoids false positives on short greetings)

Commands (admins only):
  /spacingcheck on|off
  /spacingcheck action <delete|warn|ban>
  /spacingcheck status
"""

from __future__ import annotations

import logging

from sqlalchemy import select
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
from database.models_extra import SpacingCheckSettings

logger = logging.getLogger(__name__)

SPACING_GROUP: int = 15

_MIN_LEN = 20
_SPACE_RATIO_THRESHOLD = 0.35
_SHORT_WORD_RATIO_THRESHOLD = 0.60
_SHORT_WORD_MAX_LEN = 2

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


def _is_abnormal_spacing(text: str) -> bool:
    """Return True if the text looks like spaced-out evasion."""
    text = text.strip()
    if len(text) < _MIN_LEN:
        return False

    total_chars = len(text)
    spaces = text.count(" ")
    space_ratio = spaces / total_chars

    words = text.split()
    if len(words) < 5:
        return False
    short_words = sum(1 for w in words if len(w) <= _SHORT_WORD_MAX_LEN)
    short_ratio = short_words / len(words)

    return space_ratio > _SPACE_RATIO_THRESHOLD and short_ratio > _SHORT_WORD_RATIO_THRESHOLD


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_cfg(session, chat_id: int) -> SpacingCheckSettings:
    row = await session.get(SpacingCheckSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = SpacingCheckSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# /spacingcheck
# ---------------------------------------------------------------------------

@user_admin
async def cmd_spacingcheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_cfg(session, chat.id)

        if args and args[0].lower() == "on":
            cfg.enabled = True
            await session.commit()
            await update.message.reply_text(
                f"🔤 <b>Spacing Check</b> — <b>Enabled</b>\n"
                f"Action: <b>{cfg.action}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(
                "🔤 <b>Spacing Check</b> — <b>Disabled</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if args and args[0].lower() == "action" and len(args) > 1:
            act = args[1].lower()
            if act not in ("delete", "warn", "ban"):
                await update.message.reply_text("❌ Available actions: delete | warn | ban")
                return
            cfg.action = act
            await session.commit()
            await update.message.reply_text(
                f"✅ New action: <b>{act}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        # status
        state = "✅ Enabled" if cfg.enabled else "❌ Disabled"
        await update.message.reply_text(
            f"🔤 <b>Spacing Check — Status</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Status: {state}\n"
            f"⚡ Action: <b>{cfg.action}</b>\n\n"
            f"<i>Detects messages with excessive spaces between characters (h e l l o) to bypass filters.</i>",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

async def check_spacing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return
    text = message.text or message.caption or ""
    if not text:
        return
    if await is_user_admin(chat, user.id):
        return

    async with get_session() as session:
        cfg = await session.get(SpacingCheckSettings, chat.id)
        if not cfg or not cfg.enabled:
            return
        action = cfg.action

    if not _is_abnormal_spacing(text):
        return

    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    logger.info("spacing_check: abnormal spacing detected from user %d in chat %d", user.id, chat.id)

    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

    if action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat.id,
                f"🔤 {mention} banned — suspicious message (abnormal spacing).",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError) as e:
            logger.warning("spacing_check: ban failed: %s", e)

    elif action == "warn":
        try:
            from plugins.warns import warn_user
            await warn_user(chat.id, user.id, context.bot, "Message with abnormal spacing (evasion attempt)")
        except Exception:
            await context.bot.send_message(
                chat.id,
                f"⚠️ Warning for {mention}: suspicious message (abnormal spacing).",
                parse_mode=ParseMode.HTML,
            )

    else:
        await context.bot.send_message(
            chat.id,
            f"🔤 Message from {mention} deleted — abnormal spacing (evasion attempt).",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("spacingcheck", cmd_spacingcheck, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            check_spacing,
            block=False,
        ),
        group=SPACING_GROUP,
    )
    logger.info("Plugin loaded: spacing_check")
