"""
plugins/antiflood.py — Consecutive message flood detection and enforcement.

Commands:
  /setflood <n|off>  — Set the max consecutive messages before action (min 3).
  /flood             — Show the current flood limit.

Enforcement:
  A counter tracks consecutive messages per (chat_id, user_id).  When the
  counter exceeds the configured limit the offending user is permanently
  banned and notified.  Admin messages reset the chat counter to zero.

Design:
  The counter is kept entirely in memory (a nested dict) — it resets on bot
  restart which is acceptable because flood detection is a real-time control.
  The DB columns used are ChatFeatureSettings.flood_control_enabled and
  ChatFeatureSettings.flood_messages_limit.

Handler group: FLOOD_GROUP=3 (runs before warn/filter/blacklist).
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

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

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings

logger = logging.getLogger(__name__)

FLOOD_GROUP: int = 3

# ---------------------------------------------------------------------------
# In-memory flood state: {chat_id: {user_id: consecutive_count}}
# ---------------------------------------------------------------------------
_flood_counters: Dict[int, Dict[int, int]] = {}


def _increment(chat_id: int, user_id: int) -> int:
    """Increment and return the consecutive message count for a user in a chat."""
    if chat_id not in _flood_counters:
        _flood_counters[chat_id] = {}
    _flood_counters[chat_id][user_id] = _flood_counters[chat_id].get(user_id, 0) + 1
    return _flood_counters[chat_id][user_id]


def _reset_chat(chat_id: int) -> None:
    """Reset ALL user counters for a chat (called when any admin sends a message)."""
    _flood_counters[chat_id] = {}


def _reset_user(chat_id: int, user_id: int) -> None:
    """Reset the counter for a single user in a chat after action."""
    if chat_id in _flood_counters:
        _flood_counters[chat_id].pop(user_id, None)


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------

async def _get_flood_settings(chat_id: int) -> tuple[bool, int]:
    """Return (enabled, messages_limit) for a chat. Returns (False, 0) if not configured."""
    async with get_session() as session:
        settings: Optional[ChatFeatureSettings] = await session.get(
            ChatFeatureSettings, chat_id
        )
    if not settings:
        return False, 0
    return settings.flood_control_enabled, settings.flood_messages_limit


async def _get_or_create_settings(session, chat_id: int) -> ChatFeatureSettings:
    settings: Optional[ChatFeatureSettings] = await session.get(
        ChatFeatureSettings, chat_id
    )
    if settings is None:
        chat_row = await session.get(ChatModel, chat_id)
        if chat_row is None:
            chat_row = ChatModel(id=chat_id, title="")
            session.add(chat_row)
            await session.flush()
        settings = ChatFeatureSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


# ---------------------------------------------------------------------------
# /setflood
# ---------------------------------------------------------------------------

@user_admin
async def set_flood(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Configure the maximum number of consecutive messages a user can send
    before being banned.

    Usage:
        /setflood 5      — Trigger after 5 consecutive messages.
        /setflood off    — Disable flood control entirely.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text(
            "Provide a number (min 3) or 'off':\n"
            "/setflood 5  or  /setflood off"
        )
        return

    raw: str = context.args[0].strip().lower()

    if raw in ("off", "no", "0"):
        async with get_session() as session:
            settings = await _get_or_create_settings(session, chat.id)
            settings.flood_control_enabled = False
            settings.flood_messages_limit = 0
        await message.reply_text("Flood control <b>disabled</b>.", parse_mode=ParseMode.HTML)
        return

    if not raw.isdigit():
        await message.reply_text(
            f"'{raw}' is not valid. Use a number ≥ 3 or 'off'."
        )
        return

    limit: int = int(raw)
    if limit < 3:
        await message.reply_text("Flood limit must be at least 3.")
        return

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id)
        settings.flood_control_enabled = True
        settings.flood_messages_limit = limit

    await message.reply_text(
        f"Flood limit set to <b>{limit}</b> consecutive messages.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /flood
# ---------------------------------------------------------------------------

async def flood(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Report the current flood limit setting for this group."""
    chat = update.effective_chat
    message = update.effective_message

    enabled, limit = await _get_flood_settings(chat.id)

    if not enabled or limit == 0:
        await message.reply_text(
            "Flood control is currently <b>disabled</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"Users are banned after <b>{limit}</b> consecutive messages.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Enforcement MessageHandler
# ---------------------------------------------------------------------------

async def check_flood(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Track consecutive messages per user and ban when the limit is exceeded.

    Admin messages reset the counter for the entire chat so that moderator
    activity cannot accidentally trigger flood bans.
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not user or not chat:
        return  # Channel post or malformed update.

    enabled, limit = await _get_flood_settings(chat.id)
    if not enabled or limit == 0:
        return

    if await is_user_admin(chat, user.id):
        _reset_chat(chat.id)
        return

    count: int = _increment(chat.id, user.id)

    if count <= limit:
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        _reset_user(chat.id, user.id)
        await message.reply_text(
            f"<a href='tg://user?id={user.id}'>{user.first_name}</a> "
            f"was banned for flooding ({count} messages in a row).",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as exc:
        logger.warning(
            "Flood ban failed in chat %s: %s — auto-disabling flood control.",
            chat.id,
            exc.message,
        )
        async with get_session() as session:
            settings = await session.get(ChatFeatureSettings, chat.id)
            if settings:
                settings.flood_control_enabled = False
        _reset_chat(chat.id)
        await message.reply_text(
            "I don't have permission to ban users, so flood control has been "
            "automatically disabled. Grant me 'Ban Members' rights and re-enable "
            "with /setflood."
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register flood-control command and enforcement handlers."""
    application.add_handler(
        CommandHandler("setflood", set_flood, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("flood", flood, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND,
            check_flood,
        ),
        group=FLOOD_GROUP,
    )
    logger.info("Plugin loaded: antiflood")
