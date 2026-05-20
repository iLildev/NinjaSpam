"""
plugins/write_tool.py — Render text as a handwritten image.

Commands:
  /write <text>   — Render text as a handwritten image via the sdbots API.
  /write          — Reply to a text message to write it.
"""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

_API = "https://api.sdbots.tk/write"


async def write_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        text = message.reply_to_message.text or message.reply_to_message.caption
    elif context.args:
        text = " ".join(context.args)
    else:
        await message.reply_text("Usage: /write <text> or reply to a message.")
        return

    sending = await message.reply_text("✍️ Writing…")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(_API, params={"text": text})
            img_url = str(resp.url)
        await sending.delete()
        await message.reply_photo(
            photo=img_url,
            caption="✍️ Written text",
        )
    except Exception as e:
        logger.warning("Write tool error: %s", e)
        await sending.edit_text("Could not render text right now.")


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("write", write_text))
