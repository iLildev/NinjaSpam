"""
plugins/country_info.py — Country information lookup.

Commands:
  /country <name>  — Show detailed info about a country.

Uses the countryinfo library.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

try:
    from countryinfo import CountryInfo
    _COUNTRY_AVAILABLE = True
except ImportError:
    _COUNTRY_AVAILABLE = False
    logger.warning("countryinfo not installed; /country disabled.")

try:
    import flag as _flag
    _FLAG_AVAILABLE = True
except ImportError:
    _FLAG_AVAILABLE = False


async def country_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not _COUNTRY_AVAILABLE:
        await message.reply_text("Country info library not available.")
        return

    query = " ".join(context.args) if context.args else ""
    if not query:
        await message.reply_text("Usage: /country <country name>")
        return

    try:
        info = CountryInfo(query).info()
    except Exception:
        await message.reply_text("Country not found.")
        return

    if not info:
        await message.reply_text("No data found for that country.")
        return

    name = info.get("name", "N/A")
    capital = info.get("capital", "N/A")
    population = info.get("population", "N/A")
    area = info.get("area", "N/A")
    region = info.get("region", "N/A")
    subregion = info.get("subregion", "N/A")
    currencies = ", ".join(info.get("currencies", []))
    languages = ", ".join(info.get("languages", []))
    calling_codes = ", ".join(f"+{c}" for c in info.get("callingCodes", []))
    tlds = ", ".join(info.get("tld", []))
    borders = ", ".join(info.get("borders", []))
    native = info.get("nativeName", "N/A")
    wiki = info.get("wiki", "")

    flag_emoji = ""
    if _FLAG_AVAILABLE:
        iso = info.get("ISO", {})
        alpha2 = iso.get("alpha2", "")
        if alpha2:
            try:
                flag_emoji = _flag.flag(alpha2.upper()) + " "
            except Exception:
                pass

    text = (
        f"{flag_emoji}<b>{name}</b> ({native})\n\n"
        f"🏛 <b>Capital:</b> {capital}\n"
        f"👥 <b>Population:</b> {population:,}" if isinstance(population, int) else
        f"👥 <b>Population:</b> {population}\n"
    )
    text = (
        f"{flag_emoji}<b>{name}</b> ({native})\n\n"
        f"🏛 <b>Capital:</b> {capital}\n"
        f"👥 <b>Population:</b> {population}\n"
        f"📐 <b>Area:</b> {area} km²\n"
        f"🌍 <b>Region:</b> {region} › {subregion}\n"
        f"💰 <b>Currencies:</b> {currencies}\n"
        f"🗣 <b>Languages:</b> {languages}\n"
        f"📞 <b>Calling codes:</b> {calling_codes}\n"
        f"🌐 <b>TLD:</b> {tlds}\n"
        f"🗺 <b>Borders:</b> {borders or 'None'}\n"
    )
    if wiki:
        text += f"\n🔗 <a href='{wiki}'>Wikipedia</a>"

    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("country", country_info))
