"""
plugins/clean_blue.py — Auto-delete unknown bot command messages (bluetext cleaner).

When enabled, deletes messages that start with / but don't match any known command.

Commands:
  /cleanblue  [on|off]   — Toggle bluetext cleaning for this chat.
  /bluestate             — Show current state.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import user_admin

logger = logging.getLogger(__name__)

_ENABLED_CHATS: set[int] = set()


@user_admin
async def cleanblue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args
    if not args:
        state = "on" if chat.id in _ENABLED_CHATS else "off"
        await update.effective_message.reply_text(
            f"Bluetext cleaning is currently <b>{state}</b>.", parse_mode="HTML"
        )
        return
    val = args[0].lower()
    if val in ("on", "yes"):
        _ENABLED_CHATS.add(chat.id)
        await update.effective_message.reply_text("✅ Bluetext cleaning enabled.")
    elif val in ("off", "no"):
        _ENABLED_CHATS.discard(chat.id)
        await update.effective_message.reply_text("❌ Bluetext cleaning disabled.")
    else:
        await update.effective_message.reply_text("Use: /cleanblue on|off")


async def check_blue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if chat.id not in _ENABLED_CHATS:
        return
    if not message.text:
        return
    text = message.text.strip()
    if not text.startswith("/"):
        return
    user = message.from_user
    member = await chat.get_member(user.id)
    if member.status in ("creator", "administrator"):
        return
    try:
        await message.delete()
    except Exception as e:
        logger.debug("cleanblue delete failed: %s", e)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["cleanblue", "bluestate"], cleanblue))
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(r"^/"), check_blue),
        group=25,
    )
