"""
plugins/setlang.py — Bot language setting.

The bot language is fixed to English (en).
Users are informed of this when using the /setlang command.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.i18n import t

logger = logging.getLogger(__name__)


@user_admin
async def cmd_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🌐 Bot language is fixed to <b>English</b>. No other languages are supported.",
        parse_mode=ParseMode.HTML,
    )
    logger.info("setlang: Language change request from chat %d — language is fixed to en", update.effective_chat.id)


async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("setlang", cmd_setlang, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: setlang")
