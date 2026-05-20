"""
plugins/translator.py — Text translation using googletrans (async).

Commands:
  /tr  [lang]   — Translate replied message (or text after command) to target lang.
  /tl  [lang]   — Alias for /tr.

Usage:
  /tr en          — auto-detect source, translate to English.
  /tr hi//en      — translate from Hindi to English.

Language codes: https://te.legra.ph/LANGUAGE-CODES-05-23-2
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

try:
    from deep_translator import GoogleTranslator
    _TRANSLATOR_AVAILABLE = True
except ImportError:
    _TRANSLATOR_AVAILABLE = False
    logger.warning("deep-translator not installed; /tr disabled.")


async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    if not _TRANSLATOR_AVAILABLE:
        await message.reply_text("Translation library not installed on this instance.")
        return

    reply = message.reply_to_message
    if reply:
        to_translate = reply.text or reply.caption or ""
    else:
        if not context.args:
            await message.reply_text(
                "Reply to a message or provide text.\n"
                "Example: <code>/tr en</code> or <code>/tr hi//en</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        to_translate = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    if not to_translate.strip():
        await message.reply_text("No text to translate.")
        return

    lang_arg = (context.args[0] if context.args else "en").lower()
    if "//" in lang_arg:
        source_lang, dest_lang = lang_arg.split("//", 1)
    else:
        source_lang = "auto"
        dest_lang = lang_arg

    try:
        translator = GoogleTranslator(source=source_lang, target=dest_lang)
        translated = translator.translate(to_translate)
        result = (
            f"<b>Translated → {dest_lang}</b>\n"
            f"<code>{translated}</code>"
        )
    except Exception as e:
        logger.warning("Translation error: %s", e)
        result = f"Translation failed: {e}"

    await message.reply_text(result, parse_mode=ParseMode.HTML)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["tr", "tl"], translate_cmd))
