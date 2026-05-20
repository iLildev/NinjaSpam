"""
plugins/setlang.py — Set the bot's response language per group.

Commands:
  /setlang              — Show current language + interactive picker
  /setlang <code>       — Set language (en · ar · fa · tr · ru · id · fr · zh)

Supported codes mirror SUPPORTED_LANGUAGES in core/i18n.py.
Admins only; falls back to English when no preference is set.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.i18n import SUPPORTED_LANGUAGES, get_chat_lang, set_chat_lang, t

logger = logging.getLogger(__name__)

_LANG_NAMES: dict[str, str] = {
    "en": "🇬🇧 English",
    "ar": "🇸🇦 العربية",
    "fa": "🇮🇷 فارسی",
    "tr": "🇹🇷 Türkçe",
    "ru": "🇷🇺 Русский",
    "id": "🇮🇩 Bahasa Indonesia",
    "fr": "🇫🇷 Français",
    "zh": "🇨🇳 中文",
}


def _lang_keyboard() -> InlineKeyboardMarkup:
    """Build a two-column language picker keyboard."""
    items = list(_LANG_NAMES.items())
    rows = []
    for i in range(0, len(items), 2):
        row = []
        for code, name in items[i : i + 2]:
            row.append(InlineKeyboardButton(name, callback_data=f"sl:{code}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@user_admin
async def cmd_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)

    if not context.args:
        current_name = _LANG_NAMES.get(lang, lang)
        await update.message.reply_text(
            t("lang_select", lang) + f"\n\n<b>{t('current_value', lang, value=current_name)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=_lang_keyboard(),
        )
        return

    code = context.args[0].lower()
    if code not in SUPPORTED_LANGUAGES:
        codes = " · ".join(SUPPORTED_LANGUAGES)
        await update.message.reply_text(
            t("setlang_invalid", lang, codes=codes),
            parse_mode=ParseMode.HTML,
        )
        return

    await set_chat_lang(chat.id, code)
    lang_name = _LANG_NAMES.get(code, code)
    await update.message.reply_text(
        t("lang_changed", code),
        parse_mode=ParseMode.HTML,
    )
    logger.info("setlang: chat %d language set to %s", chat.id, code)


async def callback_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    user = query.from_user

    code = query.data.split(":", 1)[1]

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await query.answer(t("admin_only", "en"), show_alert=True)
        return

    if code not in SUPPORTED_LANGUAGES:
        await query.answer("Unknown language.", show_alert=True)
        return

    await set_chat_lang(chat.id, code)
    lang_name = _LANG_NAMES.get(code, code)
    await query.answer(f"✅ {lang_name}")
    await query.edit_message_text(
        t("lang_changed", code),
        parse_mode=ParseMode.HTML,
    )
    logger.info("setlang cb: chat %d language set to %s by user %d", chat.id, code, user.id)


async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("setlang", cmd_setlang, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(CallbackQueryHandler(callback_setlang, pattern=r"^sl:"))
    logger.info("Plugin loaded: setlang")
