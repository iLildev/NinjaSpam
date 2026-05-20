"""
plugins/settings_redirect.py — توجيه كلمات الإعداد النصية نحو /settings.

يستجيب للكلمات: "الاعدادات"، "الإعدادات"، "اعدادات"، "إعدادات"
ويُوجّه المستخدم مباشرةً نحو /settings بدلاً من الأوامر النصية.
"""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import is_user_admin

logger = logging.getLogger(__name__)

_SETTINGS_PATTERN = re.compile(
    r"^(ال?إعدادات|ال?اعدادات|settings)$",
    re.IGNORECASE | re.UNICODE,
)


async def _handle_settings_keyword(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not user or not chat or not message:
        return

    if not await is_user_admin(chat, user.id):
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚙️ فتح الإعدادات", switch_inline_query_current_chat="/settings")
    ]])
    await message.reply_html(
        "⚙️ استخدم الأمر /settings لإدارة إعدادات المجموعة.",
        reply_markup=keyboard,
    )


async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.Regex(_SETTINGS_PATTERN),
            _handle_settings_keyword,
        ),
        group=20,
    )
    logger.info("Plugin loaded: settings_redirect")
