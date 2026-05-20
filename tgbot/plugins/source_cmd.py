"""
plugins/source_cmd.py — /source / /repo — Show bot source code info.

Commands:
  /source  — Show bot version and GitHub link.
  /repo    — Alias.
"""

from __future__ import annotations

import platform

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes


async def source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_info = await context.bot.get_me()
    text = (
        f"<b>{bot_info.first_name}</b>\n\n"
        f"🐍 <b>Python:</b> <code>{platform.python_version()}</code>\n"
        f"📦 <b>python-telegram-bot:</b> <code>{telegram.__version__}</code>\n"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⭐ Source Code",
            url="https://github.com/AnonymousX1025/FallenRobot",
        )
    ]])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["source", "repo"], source))
