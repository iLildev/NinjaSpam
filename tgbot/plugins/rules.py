"""
plugins/rules.py — Group rules management and retrieval.

Commands:
  /rules        — Send the group rules to the user via private message.
  /setrules     — Set the group rules (admin only).
  /clearrules   — Erase the current rules (admin only).

Rules are stored as plain text in ChatFeatureSettings.rules_text.
When a user requests /rules in a group, the bot sends an inline button
to the user; clicking it triggers a callback that delivers the rules in PM.
If rules are requested directly in PM the bot sends them inline.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat, ChatFeatureSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_or_create_settings(
    session,
    chat_id: int,
) -> ChatFeatureSettings:
    """Return the feature-settings row, creating it on first access."""
    settings: Optional[ChatFeatureSettings] = await session.get(
        ChatFeatureSettings, chat_id
    )
    if settings is None:
        chat_row = await session.get(Chat, chat_id)
        if chat_row is None:
            chat_row = Chat(id=chat_id, title="", chat_type="group")
            session.add(chat_row)
            await session.flush()
        settings = ChatFeatureSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


async def _send_rules_in_pm(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    chat_title: str,
) -> None:
    """Send the rules to a user via private message."""
    async with get_session() as session:
        settings = await session.get(ChatFeatureSettings, chat_id)

    rules_text: str = (settings.rules_text or "").strip() if settings else ""

    if not rules_text:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"The admins of <b>{chat_title}</b> haven't set any rules yet.",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"<b>Rules for {chat_title}:</b>\n\n{rules_text}",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as exc:
        logger.warning("Failed to send rules to user %s: %s", user_id, exc.message)


# ---------------------------------------------------------------------------
# /rules
# ---------------------------------------------------------------------------

async def rules(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    In a group: send the user an inline button that delivers rules in PM.
    In PM: send the rules directly (requires chat_id as argument).
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user:
        return

    if chat.type == "private":
        # Called from PM — need a chat_id argument (from a deep-link or button).
        if not context.args:
            await message.reply_text(
                "Use /rules in a group to retrieve that group's rules."
            )
            return
        try:
            target_chat_id: int = int(context.args[0])
        except ValueError:
            await message.reply_text("Invalid chat ID.")
            return

        try:
            target_chat = await context.bot.get_chat(target_chat_id)
        except BadRequest:
            await message.reply_text("I couldn't find that group.")
            return

        await _send_rules_in_pm(context, user.id, target_chat_id, target_chat.title or "that group")
        return

    # In a group: send a button that opens PM with the bot to see rules.
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 View Rules",
                    url=f"t.me/{context.bot.username}?start=rules_{chat.id}",
                )
            ]
        ]
    )
    await message.reply_text(
        "Click below to read the group rules in our private chat.",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Callback: deep-link start for rules
# ---------------------------------------------------------------------------

async def rules_pm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle the /start rules_<chat_id> deep-link sent when the user clicks
    the "View Rules" button.
    """
    message = update.effective_message
    user = update.effective_user

    if not user or not message:
        return

    text: str = message.text or ""
    if not text.startswith("/start rules_"):
        return

    try:
        chat_id: int = int(text.split("rules_")[1])
    except (IndexError, ValueError):
        await message.reply_text("Malformed rules link.")
        return

    try:
        target_chat = await context.bot.get_chat(chat_id)
    except BadRequest:
        await message.reply_text("I couldn't find that group.")
        return

    await _send_rules_in_pm(context, user.id, chat_id, target_chat.title or "that group")


# ---------------------------------------------------------------------------
# /setrules
# ---------------------------------------------------------------------------

@user_admin
async def set_rules(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Set or replace the group rules.

    The full text after the command (including newlines) becomes the rules.

    Usage:
        /setrules <rules text>
    """
    message = update.effective_message
    chat = update.effective_chat

    # Grab everything after the command.
    raw: str = message.text or ""
    parts = raw.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "Provide the rules text after the command:\n/setrules <rules>"
        )
        return

    rules_text: str = parts[1].strip()

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id)
        settings.rules_text = rules_text

    await message.reply_text("Rules updated successfully.")


# ---------------------------------------------------------------------------
# /clearrules
# ---------------------------------------------------------------------------

@user_admin
async def clear_rules(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Erase the group rules."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        settings = await session.get(ChatFeatureSettings, chat.id)
        if settings:
            settings.rules_text = ""

    await message.reply_text("Rules cleared.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register rules command and deep-link handlers."""
    # Group commands
    application.add_handler(
        CommandHandler("rules", rules, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("setrules", set_rules, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("clearrules", clear_rules, filters=filters.ChatType.GROUPS)
    )

    # PM deep-link for rules delivery
    application.add_handler(
        CommandHandler(
            "start",
            rules_pm_callback,
            filters=filters.ChatType.PRIVATE & filters.Regex(r"^/start rules_-?\d+"),
        )
    )

    logger.info("Plugin loaded: rules")
