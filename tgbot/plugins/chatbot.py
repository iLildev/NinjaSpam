"""
plugins/chatbot.py — AI chatbot toggle per group.

Commands:
  /chatbot          — Show enable/disable panel (admin only).

Auto-responds to messages that:
  - Are direct replies to the bot.
  - Mention the bot's username.

Uses the public FallenRobot chatbot API endpoint.
"""

from __future__ import annotations

import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin

logger = logging.getLogger(__name__)

_CHATBOT_ENABLED: set[int] = set()
_API_URL = "https://fallenxbot.vercel.app/chatbot/message={}"


@user_admin
async def chatbot_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    msg = update.effective_message
    is_on = chat.id in _CHATBOT_ENABLED
    status = "✅ Enabled" if is_on else "❌ Disabled"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Enable", callback_data=f"chatbot_on:{chat.id}"),
        InlineKeyboardButton("Disable", callback_data=f"chatbot_off:{chat.id}"),
    ]])
    await msg.reply_text(
        f"Chatbot for <b>{chat.title}</b>: {status}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def chatbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat

    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        await query.answer("Admins only.", show_alert=True)
        return

    data = query.data
    if data.startswith("chatbot_on:"):
        chat_id = int(data.split(":")[1])
        _CHATBOT_ENABLED.add(chat_id)
        await query.answer("Chatbot enabled.")
        await query.edit_message_text(
            f"Chatbot for <b>{chat.title}</b>: ✅ Enabled",
            parse_mode=ParseMode.HTML,
        )
    elif data.startswith("chatbot_off:"):
        chat_id = int(data.split(":")[1])
        _CHATBOT_ENABLED.discard(chat_id)
        await query.answer("Chatbot disabled.")
        await query.edit_message_text(
            f"Chatbot for <b>{chat.title}</b>: ❌ Disabled",
            parse_mode=ParseMode.HTML,
        )


async def chatbot_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    bot = context.bot

    if not message.text or chat.type == "private":
        return
    if chat.id not in _CHATBOT_ENABLED:
        return

    bot_username = (await bot.get_me()).username
    text_lower = message.text.lower()
    is_reply_to_bot = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot.id
    )
    mentions_bot = f"@{bot_username}".lower() in text_lower

    if not (is_reply_to_bot or mentions_bot):
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_API_URL.format(message.text))
            reply_text = resp.json().get("reply", "...")
    except Exception as e:
        logger.warning("Chatbot API error: %s", e)
        return

    await message.reply_text(reply_text)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("chatbot", chatbot_panel))
    application.add_handler(CallbackQueryHandler(chatbot_callback, pattern=r"^chatbot_(on|off):"))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^[#!]"),
            chatbot_reply,
        ),
        group=20,
    )
