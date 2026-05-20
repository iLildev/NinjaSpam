"""
plugins/ping_cmd.py — /ping and /uptime commands.

Commands:
  /ping   — Measure Telegram API round-trip latency and show uptime.
  /uptime — Show bot uptime (alias).
"""

from __future__ import annotations

import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

_START_TIME = time.time()


def _readable_time(seconds: float) -> str:
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    start = time.time()
    sent = await msg.reply_text("🏓 Pinging…")
    elapsed_ms = round((time.time() - start) * 1000, 2)
    uptime = _readable_time(time.time() - _START_TIME)
    await sent.edit_text(
        f"🏓 <b>Pong!</b>\n"
        f"<b>Latency:</b> <code>{elapsed_ms} ms</code>\n"
        f"<b>Uptime:</b> <code>{uptime}</code>",
        parse_mode=ParseMode.HTML,
    )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["ping", "uptime"], ping))
