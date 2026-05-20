"""
plugins/speed_test.py — Server speed test (owner only).

Commands (OWNER only):
  /speedtest  — Run a speed test and report results.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from config import OWNER_IDS

logger = logging.getLogger(__name__)


async def speedtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Text", callback_data="speedtest_text"),
        InlineKeyboardButton("🖼 Image", callback_data="speedtest_image"),
    ]])
    await update.effective_message.reply_text("Choose speed test mode:", reply_markup=kb)


async def speedtest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id not in OWNER_IDS:
        await query.answer("Owner only.", show_alert=True)
        return

    await query.answer()
    msg = await query.edit_message_text("⏳ Running speed test…")

    try:
        import speedtest as st
        loop = asyncio.get_event_loop()

        def _run() -> dict:
            s = st.Speedtest()
            s.get_best_server()
            s.download()
            s.upload()
            return s.results.dict()

        result = await loop.run_in_executor(None, _run)

        down = round(result["download"] / 1_048_576, 2)
        up = round(result["upload"] / 1_048_576, 2)
        ping = result["ping"]
        server = result.get("server", {}).get("name", "N/A")

        text = (
            f"🚀 <b>Speed Test Results</b>\n\n"
            f"📥 <b>Download:</b> {down} Mb/s\n"
            f"📤 <b>Upload:</b> {up} Mb/s\n"
            f"🏓 <b>Ping:</b> {ping} ms\n"
            f"🌐 <b>Server:</b> {server}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except ImportError:
        await msg.edit_text("speedtest-cli is not installed on this instance.")
    except Exception as e:
        logger.warning("Speedtest error: %s", e)
        await msg.edit_text(f"Speed test failed: {e}")


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler("speedtest", speedtest, filters=owner_filter))
    application.add_handler(
        CallbackQueryHandler(speedtest_callback, pattern=r"^speedtest_")
    )
