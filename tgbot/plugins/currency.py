"""
plugins/currency.py — Currency conversion using exchangerate.host (free, no key).

Commands:
  /cash <amount> <from> <to>  — Convert currency.
  Example: /cash 100 USD EUR
"""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

_API = "https://api.exchangerate.host/convert"


async def cash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    args = context.args

    if len(args) != 3:
        await message.reply_text(
            "<b>Usage:</b> /cash &lt;amount&gt; &lt;FROM&gt; &lt;TO&gt;\n"
            "<b>Example:</b> /cash 100 USD EUR",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        amount = float(args[0])
    except ValueError:
        await message.reply_text("Invalid amount. Please provide a number.")
        return

    from_cur = args[1].upper()
    to_cur = args[2].upper()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _API,
                params={"from": from_cur, "to": to_cur, "amount": amount},
            )
            resp.raise_for_status()
            data = resp.json()

        if not data.get("success"):
            await message.reply_text("Currency conversion failed. Check the currency codes.")
            return

        result = data.get("result")
        if result is None:
            await message.reply_text("Could not get conversion result.")
            return

        await message.reply_text(
            f"<b>{amount:,.4f} {from_cur}</b> = <b>{result:,.4f} {to_cur}</b>",
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.warning("Currency API error: %s", e)
        await message.reply_text("Could not fetch exchange rate right now.")


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("cash", cash))
