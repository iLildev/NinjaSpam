"""
plugins/log_channel.py — Log channel configuration commands.

Commands:
  /setlog      — Link a channel as this group's log channel.
  /unsetlog    — Remove the log channel link.
  /logchannel  — Show the currently configured log channel.

Setup procedure (two-step, mirrors Marie's original):
  1. Forward a message from the target channel to your group.
     OR send /setlog in the channel itself, then forward that to the group.
  2. The bot reads the forward_from_chat to identify the channel.

The actual log-sending logic lives in core/log_channel.py (@loggable decorator).
This plugin manages only the admin commands that configure the link.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import LogChannelSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /setlog
# ---------------------------------------------------------------------------

@user_admin
async def set_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Link a channel as the log channel for this group.

    The command must be called while replying to a forwarded message from the
    target channel.  The bot verifies it can post to that channel before saving.

    Usage (in the group):
        Reply to a forwarded channel message with /setlog
    """
    message = update.effective_message
    chat = update.effective_chat

    if not message.reply_to_message or not message.reply_to_message.forward_from_chat:
        await message.reply_text(
            "To link a log channel:\n"
            "1. Forward any message from the target channel into this group.\n"
            "2. Reply to that forwarded message with /setlog."
        )
        return

    channel = message.reply_to_message.forward_from_chat
    channel_id: int = channel.id

    # Verify the bot can post to the channel.
    try:
        test = await context.bot.send_message(
            chat_id=channel_id,
            text=f"✅ Log channel linked to <b>{chat.title}</b>.",
            parse_mode="HTML",
        )
        # Clean up the test message.
        await context.bot.delete_message(chat_id=channel_id, message_id=test.message_id)
    except (BadRequest, Forbidden) as exc:
        await message.reply_text(
            f"I couldn't post to that channel: {exc.message}\n"
            "Make sure I'm an administrator there with 'Post Messages' permission."
        )
        return

    async with get_session() as session:
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()

        existing = await session.get(LogChannelSettings, chat.id)
        if existing:
            existing.log_channel_id = channel_id
        else:
            session.add(LogChannelSettings(chat_id=chat.id, log_channel_id=channel_id))

    channel_name: str = channel.title or str(channel_id)
    await message.reply_text(
        f"Log channel set to <b>{channel_name}</b> ({channel_id}).\n"
        "All moderation actions will now be logged there.",
        parse_mode="HTML",
    )
    logger.info("Log channel %s linked to chat %s", channel_id, chat.id)


# ---------------------------------------------------------------------------
# /unsetlog
# ---------------------------------------------------------------------------

@user_admin
async def unset_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove the log channel configuration for this group."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        record: Optional[LogChannelSettings] = await session.get(
            LogChannelSettings, chat.id
        )
        if not record:
            await message.reply_text("No log channel is configured for this group.")
            return

        channel_id: int = record.log_channel_id
        await session.delete(record)

    # Notify the channel that it was unlinked.
    try:
        await context.bot.send_message(
            chat_id=channel_id,
            text=f"ℹ️ This channel has been unlinked as the log channel for <b>{chat.title}</b>.",
            parse_mode="HTML",
        )
    except (BadRequest, Forbidden):
        pass

    await message.reply_text("Log channel unlinked.")
    logger.info("Log channel %s unlinked from chat %s", channel_id, chat.id)


# ---------------------------------------------------------------------------
# /logchannel
# ---------------------------------------------------------------------------

async def log_channel_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the currently configured log channel."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        record: Optional[LogChannelSettings] = await session.get(
            LogChannelSettings, chat.id
        )

    if not record:
        await message.reply_text("No log channel is configured for this group.")
        return

    try:
        channel = await context.bot.get_chat(record.log_channel_id)
        name: str = channel.title or str(record.log_channel_id)
    except BadRequest:
        name = str(record.log_channel_id)

    await message.reply_text(
        f"Log channel: <b>{name}</b> (<code>{record.log_channel_id}</code>)",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register log channel management commands."""
    application.add_handler(
        CommandHandler("setlog", set_log, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unsetlog", unset_log, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("logchannel", log_channel_info, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: log_channel")
