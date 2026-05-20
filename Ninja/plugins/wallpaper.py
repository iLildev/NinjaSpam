"""
plugins/wallpaper.py — Wallpaper search using the Safone API.

Commands:
  /wall  <query>      — Search and send a random wallpaper.
  /wallpaper <query>  — Alias.
"""

from __future__ import annotations

import logging
import random

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


async def wall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    query = " ".join(context.args) if context.args else ""
    if not query:
        await message.reply_text("Usage: /wall <query>")
        return

    sending = await message.reply_text("🔍 Searching for wallpapers…")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.safone.me/wall?query={query}")
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except Exception as e:
        logger.warning("Wallpaper API error: %s", e)
        await sending.edit_text("Could not fetch wallpapers right now.")
        return

    if not results:
        await sending.edit_text(f"No wallpapers found for: {query}")
        return

    choice = random.choice(results[:min(len(results), 5)])
    img_url = choice.get("imageUrl") or choice.get("url", "")
    if not img_url:
        await sending.edit_text("No image URL in result.")
        return

    await sending.delete()
    await message.reply_photo(
        photo=img_url,
        caption=f"🖼 <b>{query}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Link", url=img_url)]]),
    )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["wall", "wallpaper"], wall))
