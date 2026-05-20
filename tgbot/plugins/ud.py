"""
plugins/ud.py — Urban Dictionary lookup.

Commands:
  /ud <word>  — Look up the word on Urban Dictionary.
"""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


async def ud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = " ".join(context.args) if context.args else ""
    if not text:
        await message.reply_text("Usage: /ud <word>")
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.urbandictionary.com/v0/define",
                params={"term": text},
            )
            resp.raise_for_status()
            data = resp.json()
        entry = data["list"][0]
        definition = entry["definition"]
        example = entry.get("example", "")
        reply = f"<b>{text}</b>\n\n{definition}"
        if example:
            reply += f"\n\n<i>{example}</i>"
    except (IndexError, KeyError):
        reply = f"No results found for <b>{text}</b>."
    except Exception as e:
        logger.warning("UD API error: %s", e)
        reply = "Could not reach Urban Dictionary right now."

    await message.reply_text(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("ud", ud))
