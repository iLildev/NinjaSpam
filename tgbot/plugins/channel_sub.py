"""
plugins/channel_sub.py — Channel Subscription Gate (GroupHelp feature).

Forces group members to be subscribed to a specific Telegram channel
before they are allowed to send messages. Non-subscribed users have
their messages deleted and receive a private prompt to join first.

Commands (admin only):
  /setchannel @username   — Set the required subscription channel.
  /setchannel <id>        — Same, using numeric channel ID.
  /delchannel             — Remove the subscription requirement.
  /channelinfo            — Show the current subscription channel.

Behaviour:
  - Messages from non-subscribed non-admins are silently deleted.
  - A one-time inline-button notification is sent to the user in the
    group prompting them to subscribe and click "✅ I joined".
  - When the user clicks the button the bot verifies membership; if
    confirmed the bot removes the prompt and lets future messages through.
  - Admins and the bot itself are always exempt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import ChannelSubSettings

log = logging.getLogger(__name__)

_CB_PREFIX = "chsub"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[ChannelSubSettings]:
    async with get_session() as session:
        return await session.get(ChannelSubSettings, chat_id)


async def _is_subscribed(bot, channel_id: int, user_id: int) -> bool:
    """Return True if *user_id* is a member of *channel_id*."""
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except (BadRequest, Forbidden):
        return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Set the required subscription channel for this group.

    Usage:
        /setchannel @channelname
        /setchannel -100123456789
    """
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args:
        await message.reply_text(
            "Usage: /setchannel @channelname  or  /setchannel <channel_id>"
        )
        return

    channel_ref = args[0].strip()

    # Resolve channel — allow @username or numeric ID.
    try:
        channel_chat: Chat = await context.bot.get_chat(channel_ref)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't find that channel: {exc.message}")
        return

    if channel_chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP):
        await message.reply_text("That doesn't look like a channel or group.")
        return

    # Ensure the bot can check membership in that channel.
    try:
        await context.bot.get_chat_member(channel_chat.id, context.bot.id)
    except (BadRequest, Forbidden):
        await message.reply_text(
            "I need to be a member of that channel to verify subscriptions.\n"
            "Add me to the channel first, then try again."
        )
        return

    # Persist.
    async with get_session() as session:
        row = await session.get(ChannelSubSettings, chat.id)
        if row is None:
            row = ChannelSubSettings(
                chat_id=chat.id,
                channel_id=channel_chat.id,
                channel_username=channel_chat.username or "",
                channel_title=channel_chat.title or str(channel_chat.id),
            )
            session.add(row)
        else:
            row.channel_id = channel_chat.id
            row.channel_username = channel_chat.username or ""
            row.channel_title = channel_chat.title or str(channel_chat.id)
            row.enabled = True
        await session.commit()

    link = f"https://t.me/{channel_chat.username}" if channel_chat.username else str(channel_chat.id)
    await message.reply_html(
        f"✅ Channel subscription gate activated.\n"
        f"Required channel: <b>{channel_chat.title}</b> ({link})\n\n"
        f"Members who are not subscribed will have their messages deleted "
        f"until they join the channel."
    )


@user_admin
async def del_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the channel subscription requirement."""
    message = update.effective_message
    chat = update.effective_chat

    async with get_session() as session:
        row = await session.get(ChannelSubSettings, chat.id)
        if row is None or not row.enabled:
            await message.reply_text("No channel subscription gate is set for this group.")
            return
        row.enabled = False
        await session.commit()

    await message.reply_text("✅ Channel subscription gate disabled.")


@user_admin
async def channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current subscription channel configuration."""
    message = update.effective_message
    chat = update.effective_chat

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        await message.reply_text("No channel subscription gate is configured for this group.")
        return

    link = (
        f"https://t.me/{settings.channel_username}"
        if settings.channel_username
        else str(settings.channel_id)
    )
    await message.reply_html(
        f"<b>Channel Subscription Gate</b>\n\n"
        f"<b>Status:</b> ✅ Active\n"
        f"<b>Channel:</b> <a href='{link}'>{settings.channel_title}</a>\n"
        f"<b>Channel ID:</b> <code>{settings.channel_id}</code>"
    )


# ---------------------------------------------------------------------------
# Enforcement handler
# ---------------------------------------------------------------------------

async def enforce_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Delete messages from non-subscribed users and prompt them to join.

    Runs on every group message (low priority group so enforcement runs first).
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not user or not message or not chat:
        return
    if user.is_bot:
        return

    # Admins are exempt.
    if await is_user_admin(chat, user.id):
        return

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        return

    # Check subscription.
    if await _is_subscribed(context.bot, settings.channel_id, user.id):
        return

    # Not subscribed — delete the message silently.
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Build the join prompt with a verification button.
    channel_link = (
        f"https://t.me/{settings.channel_username}"
        if settings.channel_username
        else f"tg://chat?id={settings.channel_id}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=channel_link)],
        [InlineKeyboardButton("✅ I Joined — Verify Me", callback_data=f"{_CB_PREFIX}:{chat.id}:{user.id}")],
    ])

    try:
        sent = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"👋 {user.mention_html()}, you must subscribe to "
                f"<a href='{channel_link}'>{settings.channel_title}</a> "
                f"before you can chat here.\n\n"
                f"Join the channel then tap <b>✅ I Joined</b> to verify."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        # Store the prompt message id so we can clean it up on verification.
        context.bot_data.setdefault("chsub_prompts", {})[f"{chat.id}:{user.id}"] = sent.message_id
    except (BadRequest, Forbidden):
        pass


# ---------------------------------------------------------------------------
# Callback query — "✅ I Joined — Verify Me"
# ---------------------------------------------------------------------------

async def verify_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline button press to verify channel membership."""
    query: CallbackQuery = update.callback_query
    await query.answer()

    data_parts = query.data.split(":")
    if len(data_parts) != 3:
        return

    _, chat_id_str, target_user_id_str = data_parts
    chat_id = int(chat_id_str)
    target_user_id = int(target_user_id_str)

    # Only the user who was prompted can verify themselves.
    if query.from_user.id != target_user_id:
        await query.answer("This verification button is not for you.", show_alert=True)
        return

    settings = await _get_settings(chat_id)
    if not settings or not settings.enabled:
        await query.message.delete()
        return

    subscribed = await _is_subscribed(context.bot, settings.channel_id, target_user_id)
    if subscribed:
        try:
            await query.message.delete()
        except (BadRequest, Forbidden):
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ <a href='tg://user?id={target_user_id}'>{query.from_user.first_name}</a> is now verified — welcome!",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, Forbidden):
            pass
    else:
        await query.answer(
            "You don't appear to be subscribed yet. Join the channel and try again.",
            show_alert=True,
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("setchannel", set_channel, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("delchannel", del_channel, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("channelinfo", channel_info, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL & ~filters.COMMAND,
            enforce_subscription,
        ),
        group=5,
    )
    application.add_handler(
        CallbackQueryHandler(verify_subscription, pattern=rf"^{_CB_PREFIX}:")
    )
    log.info("Plugin loaded: channel_sub")
