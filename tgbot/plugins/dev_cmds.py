"""
plugins/dev_cmds.py — Owner-only developer/maintenance commands.

Commands (OWNER only):
  /leave  <chat_id>  — Make the bot leave a specific chat.
  /logs              — Send the bot log file to the owner in PM.
  /debug [on|off]    — Toggle debug logging for commands.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress

from telegram import Update
from telegram.error import BadRequest, TelegramError, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import OWNER_IDS

logger = logging.getLogger(__name__)
_DEBUG_MODE = False


async def leave_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.effective_message.reply_text("Provide a chat ID.")
        return
    try:
        chat_id = int(args[0])
        await context.bot.leave_chat(chat_id)
        with suppress(TelegramError):
            await update.effective_message.reply_text(f"Left chat {chat_id}.")
    except (TelegramError, ValueError) as e:
        await update.effective_message.reply_text(f"Failed: {e}")


async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log_path = "tgbot.log"
    if not os.path.exists(log_path):
        await context.bot.send_message(user.id, "Log file not found.")
        return
    try:
        with open(log_path, "rb") as f:
            await context.bot.send_document(chat_id=user.id, document=f, filename="tgbot.log")
    except (TelegramError, Forbidden) as e:
        await update.effective_message.reply_text(f"Could not send logs: {e}")


async def debug_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _DEBUG_MODE
    args = context.args
    if args and args[0].lower() in ("on", "yes"):
        _DEBUG_MODE = True
        await update.effective_message.reply_text("Debug mode ON.")
    elif args and args[0].lower() in ("off", "no"):
        _DEBUG_MODE = False
        await update.effective_message.reply_text("Debug mode OFF.")
    else:
        state = "ON" if _DEBUG_MODE else "OFF"
        await update.effective_message.reply_text(f"Debug mode is currently {state}.")


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler("leave", leave_chat, filters=owner_filter))
    application.add_handler(CommandHandler("logs", send_logs, filters=owner_filter))
    application.add_handler(CommandHandler("debug", debug_mode, filters=owner_filter))
