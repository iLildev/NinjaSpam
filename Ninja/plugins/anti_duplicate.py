"""
plugins/anti_duplicate.py — Duplicate message spam detection (TG-spam inspired).

Tracks per-user message history in memory. If the same message text is sent
N times within a sliding window the user is banned, muted, or just the message
is deleted — depending on the configured action.

Commands (admins only):
  /antiduplicate <N> [window_min]  — Enable; N = threshold (default window 60 min).
  /antiduplicate off               — Disable.
  /antiduplicate status            — Show current config.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

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
from database.models_extra import AntiDuplicateSettings

logger = logging.getLogger(__name__)

DEDUP_GROUP: int = 14

# In-memory store: (chat_id, user_id) -> deque of (text_hash, timestamp)
_history: Dict[Tuple[int, int], Deque[Tuple[str, float]]] = defaultdict(
    lambda: deque(maxlen=50)
)

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


def _msg_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode(), usedforsecurity=False).hexdigest()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> AntiDuplicateSettings:
    row = await session.get(AntiDuplicateSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = AntiDuplicateSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# /antiduplicate command
# ---------------------------------------------------------------------------

@user_admin
async def cmd_antiduplicate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(
                "🔁 <b>Anti-Duplicate</b> — <b>Disabled</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args or args[0].lower() == "status":
            state = "✅ Enabled" if cfg.enabled else "❌ Disabled"
            await update.message.reply_text(
                f"🔁 <b>Anti-Duplicate — Status</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Status: {state}\n"
                f"🔢 Limit: <b>{cfg.threshold}</b> identical messages\n"
                f"⏳ Window: <b>{cfg.window_minutes}</b> minutes\n"
                f"⚡ Action: <b>{cfg.action}</b>\n\n"
                f"<i>/antiduplicate <N> [minutes] — enable</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args[0].isdigit():
            await update.message.reply_text(
                "⚠️ Usage: <code>/antiduplicate <N> [window_min] [action]</code>\n"
                "Example: <code>/antiduplicate 3 60 ban</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        threshold = int(args[0])
        if threshold < 2:
            await update.message.reply_text("❌ Limit must be ≥ 2.")
            return

        window = int(args[1]) if len(args) > 1 and args[1].isdigit() else cfg.window_minutes
        action = args[2].lower() if len(args) > 2 and args[2].lower() in ("ban", "mute", "delete") else cfg.action

        cfg.enabled = True
        cfg.threshold = threshold
        cfg.window_minutes = max(1, window)
        cfg.action = action
        await session.commit()

    await update.message.reply_text(
        f"🔁 <b>Anti-Duplicate</b> — <b>Enabled</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔢 Limit: <b>{threshold}</b> identical messages in <b>{window}</b> minutes\n"
        f"⚡ Action on limit reached: <b>{action}</b>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

async def check_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return
    if not (message.text or message.caption):
        return
    if await is_user_admin(chat, user.id):
        return

    async with get_session() as session:
        cfg = await session.get(AntiDuplicateSettings, chat.id)
        if not cfg or not cfg.enabled:
            return
        threshold = cfg.threshold
        window_sec = cfg.window_minutes * 60
        action = cfg.action

    text = (message.text or message.caption or "").strip()
    if len(text) < 10:
        return  # Too short to be meaningful duplicate detection

    h = _msg_hash(text)
    key = (chat.id, user.id)
    now = time.time()
    dq = _history[key]

    # Expire old entries
    while dq and now - dq[0][1] > window_sec:
        dq.popleft()

    # Count matching hashes in window
    count = sum(1 for (mh, _) in dq if mh == h)
    dq.append((h, now))

    if count < threshold - 1:
        return

    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

    try:
        await message.delete()
    except (BadRequest, TelegramError):
        pass

    if action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat.id,
                f"🔁 <b>Duplicate Spam!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🚫 {mention} banned — sent same message <b>{count + 1}</b> times.",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError) as e:
            logger.warning("anti_duplicate: ban failed for %d: %s", user.id, e)

    elif action == "mute":
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=_MUTE_PERMS,
            )
            await context.bot.send_message(
                chat.id,
                f"🔁 <b>Duplicate Spam!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔇 {mention} muted — sent same message <b>{count + 1}</b> times.",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError) as e:
            logger.warning("anti_duplicate: mute failed for %d: %s", user.id, e)

    else:  # delete only
        await context.bot.send_message(
            chat.id,
            f"🔁 {mention} is sending duplicate messages! Message deleted.",
            parse_mode=ParseMode.HTML,
        )

    _history[key].clear()
    logger.info("anti_duplicate: action=%s user=%d chat=%d", action, user.id, chat.id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("antiduplicate", cmd_antiduplicate, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            check_duplicate,
            block=False,
        ),
        group=DEDUP_GROUP,
    )
    logger.info("Plugin loaded: anti_duplicate")
