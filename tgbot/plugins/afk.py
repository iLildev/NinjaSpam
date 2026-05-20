"""
plugins/afk.py — Away-From-Keyboard status system.

One of the most-requested features in group management bots.  When a user
marks themselves as AFK, the bot will politely inform anyone who mentions
or replies to them that the user is currently away — including the reason
if one was given.

Behaviour:
  • /afk [reason]   — Set yourself as AFK.  Optional reason (e.g. "sleeping").
  • Sending any message while AFK → status is automatically cleared.
  • When someone @mentions or replies to an AFK user → bot notifies once per
    5 minutes per (mentioner, afk_user) pair to avoid spam.
  • /afk off         — Manually clear AFK status.

AFK statuses persist across bot restarts (stored in PostgreSQL).
The in-memory notification cooldown resets on restart (intentional — avoids
stale cooldown entries).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.engine import get_session
from database.models_extra import UserAFK

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification cooldown — (mentioner_id, afk_user_id) → last notified time
# ---------------------------------------------------------------------------
_COOLDOWN_SECONDS = 300  # 5 minutes between repeat notifications
_notified: Dict[Tuple[int, int], float] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_afk(user_id: int) -> Optional[UserAFK]:
    async with get_session() as session:
        result = await session.execute(
            select(UserAFK).where(UserAFK.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def _set_afk(user_id: int, reason: Optional[str]) -> None:
    async with get_session() as session:
        existing = await session.get(UserAFK, user_id)
        if existing:
            existing.reason = reason
            existing.since = _utcnow()
        else:
            session.add(UserAFK(user_id=user_id, reason=reason, since=_utcnow()))


async def _clear_afk(user_id: int) -> bool:
    """Remove AFK record. Returns True if it existed."""
    async with get_session() as session:
        result = await session.execute(
            delete(UserAFK).where(UserAFK.user_id == user_id)
        )
        return (result.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# /afk command
# ---------------------------------------------------------------------------

async def afk_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set or clear AFK status."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    args = context.args or []
    raw = " ".join(args).strip()

    # /afk off — manual clear
    if raw.lower() == "off":
        cleared = await _clear_afk(user.id)
        if cleared:
            await msg.reply_text(f"✅ Welcome back, {user.first_name}! AFK status cleared.")
        else:
            await msg.reply_text("You were not AFK.")
        return

    reason = raw if raw else None
    await _set_afk(user.id, reason)

    if reason:
        await msg.reply_text(
            f"😴 <b>{user.first_name}</b> is now AFK\n"
            f"<i>Reason: {reason}</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text(
            f"😴 <b>{user.first_name}</b> is now AFK.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Message watcher — auto-clear AFK + notify on mention
# ---------------------------------------------------------------------------

async def _message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Two responsibilities:
    1. Clear the sender's AFK if they send a message while AFK.
    2. Notify if the message mentions or replies to an AFK user.
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or user.is_bot:
        return

    import time
    now = time.monotonic()

    # --- 1. Auto-clear sender's AFK ---
    afk_record = await _get_afk(user.id)
    if afk_record:
        # Skip if this is the /afk command itself
        if msg.text and msg.text.lower().startswith("/afk"):
            return
        await _clear_afk(user.id)
        since = afk_record.since
        delta = _utcnow() - since.replace(tzinfo=timezone.utc) if since.tzinfo is None else _utcnow() - since
        minutes = int(delta.total_seconds() // 60)
        duration_str = f"{minutes} minute(s)" if minutes > 0 else "just now"
        try:
            await msg.reply_text(
                f"👋 Welcome back, <b>{user.first_name}</b>!\n"
                f"You were AFK for {duration_str}.",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass
        return  # Don't double-process

    # --- 2. Notify if replying to an AFK user ---
    afk_users_to_check: list[int] = []

    if msg.reply_to_message and msg.reply_to_message.from_user:
        replied_to = msg.reply_to_message.from_user
        if not replied_to.is_bot and replied_to.id != user.id:
            afk_users_to_check.append(replied_to.id)

    # Also check @mentions in text
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention" and msg.text:
                mention_text = msg.text[entity.offset: entity.offset + entity.length]
                username = mention_text.lstrip("@")
                try:
                    chat_member = await context.bot.get_chat_member(
                        update.effective_chat.id, f"@{username}"
                    )
                    uid = chat_member.user.id
                    if uid != user.id:
                        afk_users_to_check.append(uid)
                except Exception:
                    pass
            elif entity.type == "text_mention" and entity.user:
                uid = entity.user.id
                if uid != user.id:
                    afk_users_to_check.append(uid)

    for afk_uid in set(afk_users_to_check):
        # Cooldown check
        key = (user.id, afk_uid)
        if now - _notified.get(key, 0) < _COOLDOWN_SECONDS:
            continue

        afk_rec = await _get_afk(afk_uid)
        if not afk_rec:
            continue

        _notified[key] = now

        since = afk_rec.since
        delta = _utcnow() - since.replace(tzinfo=timezone.utc) if since.tzinfo is None else _utcnow() - since
        minutes = int(delta.total_seconds() // 60)
        duration_str = f"{minutes}m ago" if minutes > 0 else "just now"

        if afk_rec.reason:
            note = f"<i>Reason: {afk_rec.reason}</i>\n"
        else:
            note = ""

        try:
            await msg.reply_text(
                f"😴 <a href='tg://user?id={afk_uid}'>This user</a> is currently AFK "
                f"(since {duration_str}).\n{note}",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("afk", afk_cmd))
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            _message_handler,
        ),
        group=50,
    )
    log.info("Plugin loaded: afk")
