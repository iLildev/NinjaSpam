"""
plugins/cleanservice.py — Auto-delete Telegram service messages.

Telegram generates "service messages" that clutter the chat:
  • "User joined the group"
  • "User left the group"
  • "User was removed by admin"
  • "Message was pinned"
  • "Group name changed"
  • "Group photo changed"
  • "Video chat started/ended"

When enabled, this plugin silently deletes these messages within 1 second
of them being sent, keeping the group chat clean and professional.

Each service type can be toggled independently.

Commands:
  /cleanservice            — Show current settings (inline keyboard).
  /cleanservice <on|off>   — Master toggle for all service message cleanup.
  /cleanservice joins      — Toggle cleanup of join messages.
  /cleanservice leaves     — Toggle cleanup of leave/kick messages.
  /cleanservice pins       — Toggle cleanup of "pinned message" notifications.
  /cleanservice chatname   — Toggle cleanup of name/photo change messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models_extra import CleanServiceSettings

log = logging.getLogger(__name__)
_CB = "cleansvc"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> CleanServiceSettings:
    async with get_session() as session:
        result = await session.execute(
            select(CleanServiceSettings).where(CleanServiceSettings.chat_id == chat_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            s = CleanServiceSettings(chat_id=chat_id)
            session.add(s)
            await session.flush()
        return s


async def _toggle(chat_id: int, field: str) -> bool:
    """Toggle a boolean field. Returns the new value."""
    async with get_session() as session:
        result = await session.execute(
            select(CleanServiceSettings).where(CleanServiceSettings.chat_id == chat_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            s = CleanServiceSettings(chat_id=chat_id)
            session.add(s)
        current = getattr(s, field, False)
        setattr(s, field, not current)
        return not current


# ---------------------------------------------------------------------------
# Service message deleter
# ---------------------------------------------------------------------------

async def _try_delete(chat_id: int, message_id: int, bot) -> None:
    await asyncio.sleep(0.5)
    try:
        await bot.delete_message(chat_id, message_id)
    except BadRequest:
        pass


async def service_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Silently delete configured service messages."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    settings = await _get_settings(chat.id)
    if not settings.enabled:
        return

    should_delete = False

    if settings.clean_joins and msg.new_chat_members:
        should_delete = True
    elif settings.clean_leaves and (msg.left_chat_member is not None):
        should_delete = True
    elif settings.clean_pins and msg.pinned_message is not None:
        should_delete = True
    elif settings.clean_chatname and (
        msg.new_chat_title is not None or msg.new_chat_photo is not None
    ):
        should_delete = True

    if should_delete:
        asyncio.create_task(_try_delete(chat.id, msg.message_id, context.bot))


# ---------------------------------------------------------------------------
# Keyboard UI
# ---------------------------------------------------------------------------

def _icon(val: bool) -> str:
    return "✅" if val else "❌"


def _keyboard(s: CleanServiceSettings) -> InlineKeyboardMarkup:
    chat_id = s.chat_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"Master: {_icon(s.enabled)}",
                callback_data=f"{_CB}:{chat_id}:enabled",
            )
        ],
        [
            InlineKeyboardButton(
                f"Joins {_icon(s.clean_joins)}",
                callback_data=f"{_CB}:{chat_id}:clean_joins",
            ),
            InlineKeyboardButton(
                f"Leaves {_icon(s.clean_leaves)}",
                callback_data=f"{_CB}:{chat_id}:clean_leaves",
            ),
        ],
        [
            InlineKeyboardButton(
                f"Pins {_icon(s.clean_pins)}",
                callback_data=f"{_CB}:{chat_id}:clean_pins",
            ),
            InlineKeyboardButton(
                f"Chat Name/Photo {_icon(s.clean_chatname)}",
                callback_data=f"{_CB}:{chat_id}:clean_chatname",
            ),
        ],
        [InlineKeyboardButton("✖ Close", callback_data=f"{_CB}:{chat_id}:close")],
    ])


def _status_text(s: CleanServiceSettings) -> str:
    return (
        f"<b>🧹 Clean Service Messages</b>\n\n"
        f"Master: {_icon(s.enabled)}\n"
        f"Joins: {_icon(s.clean_joins)} | Leaves: {_icon(s.clean_leaves)}\n"
        f"Pins: {_icon(s.clean_pins)} | Name/Photo: {_icon(s.clean_chatname)}\n\n"
        f"<i>Tap any button to toggle.</i>"
    )


# ---------------------------------------------------------------------------
# /cleanservice command
# ---------------------------------------------------------------------------

@user_admin
async def cleanservice_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    msg = update.effective_message
    args = context.args or []

    if args:
        arg = args[0].lower()
        field_map = {
            "on": "enabled",
            "off": "enabled",
            "joins": "clean_joins",
            "leaves": "clean_leaves",
            "pins": "clean_pins",
            "chatname": "clean_chatname",
        }
        if arg in ("on", "off"):
            async with get_session() as session:
                result = await session.execute(
                    select(CleanServiceSettings).where(CleanServiceSettings.chat_id == chat.id)
                )
                s = result.scalar_one_or_none()
                if s is None:
                    s = CleanServiceSettings(chat_id=chat.id)
                    session.add(s)
                s.enabled = arg == "on"
            icon = "✅" if arg == "on" else "❌"
            await msg.reply_text(
                f"{icon} Clean service messages <b>{'enabled' if arg == 'on' else 'disabled'}</b>.",
                parse_mode="HTML",
            )
            return
        if arg in field_map:
            new_val = await _toggle(chat.id, field_map[arg])
            await msg.reply_text(
                f"{'✅' if new_val else '❌'} <b>{arg}</b> cleanup {'enabled' if new_val else 'disabled'}.",
                parse_mode="HTML",
            )
            return
        await msg.reply_text("Unknown option. Use: on/off/joins/leaves/pins/chatname")
        return

    # Show panel
    settings = await _get_settings(chat.id)
    await msg.reply_html(
        _status_text(settings),
        reply_markup=_keyboard(settings),
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def cleanservice_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return

    _, chat_id_str, field = parts[0], parts[1], parts[2]
    chat_id = int(chat_id_str)

    if field == "close":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        return

    valid_fields = {"enabled", "clean_joins", "clean_leaves", "clean_pins", "clean_chatname"}
    if field not in valid_fields:
        return

    await _toggle(chat_id, field)
    settings = await _get_settings(chat_id)
    try:
        await query.edit_message_text(
            _status_text(settings),
            parse_mode="HTML",
            reply_markup=_keyboard(settings),
        )
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (
                filters.StatusUpdate.NEW_CHAT_MEMBERS
                | filters.StatusUpdate.LEFT_CHAT_MEMBER
                | filters.StatusUpdate.PINNED_MESSAGE
                | filters.StatusUpdate.NEW_CHAT_TITLE
                | filters.StatusUpdate.NEW_CHAT_PHOTO
            ),
            service_handler,
        ),
        group=4,
    )
    application.add_handler(
        CommandHandler("cleanservice", cleanservice_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CallbackQueryHandler(cleanservice_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: cleanservice")
