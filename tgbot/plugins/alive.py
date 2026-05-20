"""
plugins/alive.py — /alive command — show bot status and version info.

Commands:
  /alive  — Show bot name, library version, and uptime.
"""

from __future__ import annotations

import platform
import time

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

_START_TIME = time.time()


def _uptime() -> str:
    secs = int(time.time() - _START_TIME)
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


async def alive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot
    bot_info = await bot.get_me()
    text = (
        f"🤖 <b>{bot_info.first_name}</b> is alive!\n\n"
        f"🐍 <b>Python:</b> <code>{platform.python_version()}</code>\n"
        f"📦 <b>PTB:</b> <code>{telegram.__version__}</code>\n"
        f"⏱ <b>Uptime:</b> <code>{_uptime()}</code>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Help", url=f"https://t.me/{bot_info.username}?start=help"),
    ]])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("alive", alive))
