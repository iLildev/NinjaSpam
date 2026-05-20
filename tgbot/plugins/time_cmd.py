"""
plugins/time_cmd.py — Timezone lookup by country name, code, or timezone name.

Commands:
  /time <query>  — Show current time and date for the given timezone/country.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

_API = "http://worldtimeapi.org/api/timezone"


async def gettime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await message.reply_text(
            "Usage: /time &lt;country/timezone&gt;\n"
            "Example: /time Europe/London",
            parse_mode=ParseMode.HTML,
        )
        return

    sending = await message.reply_text(f"⏰ Looking up time for <b>{query}</b>…", parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_API}/{query}")
            if resp.status_code == 404:
                resp2 = await client.get(f"{_API}")
                zones = resp2.json()
                matches = [z for z in zones if query.lower() in z.lower()]
                if not matches:
                    await sending.edit_text(f"No timezone found for: <b>{query}</b>", parse_mode=ParseMode.HTML)
                    return
                resp = await client.get(f"{_API}/{matches[0]}")
            resp.raise_for_status()
            data = resp.json()

        tz_name = data.get("timezone", "N/A")
        dt_str = data.get("datetime", "")
        utc_offset = data.get("utc_offset", "")
        dst = data.get("dst", False)

        dt = datetime.fromisoformat(dt_str[:19]) if dt_str else datetime.now()
        date_fmt = dt.strftime("%d %B %Y")
        time_fmt = dt.strftime("%H:%M:%S")
        day_fmt = dt.strftime("%A")

        text = (
            f"🌍 <b>Timezone:</b> <code>{tz_name}</code>\n"
            f"📅 <b>Date:</b> <code>{date_fmt}</code>\n"
            f"🕐 <b>Time:</b> <code>{time_fmt}</code>\n"
            f"📆 <b>Day:</b> <code>{day_fmt}</code>\n"
            f"⏩ <b>UTC Offset:</b> <code>{utc_offset}</code>\n"
            f"☀️ <b>DST active:</b> {'Yes' if dst else 'No'}"
        )
        await sending.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.warning("Time API error: %s", e)
        await sending.edit_text("Could not fetch time info right now.")


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("time", gettime))
