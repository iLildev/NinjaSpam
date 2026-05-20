"""
plugins/setlang.py — ضبط لغة البوت.

اللغة الوحيدة المدعومة هي العربية (ar).
يُبلَّغ المستخدم بذلك عند استخدام الأمر /setlang.
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
        "🇸🇦 لغة البوت مثبّتة على <b>العربية</b> وهي اللغة الوحيدة المدعومة.",
        parse_mode=ParseMode.HTML,
    )
    logger.info("setlang: طلب تغيير اللغة من chat %d — اللغة ثابتة على ar", update.effective_chat.id)


async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("setlang", cmd_setlang, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: setlang")
