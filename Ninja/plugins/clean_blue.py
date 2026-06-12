"""
plugins/clean_blue.py — Auto-delete unknown bot command messages (bluetext cleaner).

When enabled, deletes messages that start with / but don't match any known command.

Commands:
  /cleanblue  [on|off]   — Toggle bluetext cleaning for this chat.
  /bluestate             — Show current state.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import user_admin
from db.repositories import settings as settings_repo

logger = logging.getLogger(__name__)


@user_admin
async def cleanblue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args
    if not args:
        settings = await settings_repo.get_or_create(chat.id)
        state = "on" if settings.clean_blue_enabled else "off"
        await update.effective_message.reply_text(
            f"Bluetext cleaning is currently <b>{state}</b>.", parse_mode="HTML"
        )
        return
    val = args[0].lower()
    if val in ("on", "yes"):
        await settings_repo.update(chat.id, clean_blue_enabled=True)
        await update.effective_message.reply_text("✅ Bluetext cleaning enabled.")
    elif val in ("off", "no"):
        await settings_repo.update(chat.id, clean_blue_enabled=False)
        await update.effective_message.reply_text("❌ Bluetext cleaning disabled.")
    else:
        await update.effective_message.reply_text("Use: /cleanblue on|off")


async def check_blue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    
    settings = await settings_repo.get(chat.id)
    if not settings or not settings.clean_blue_enabled:
        return
    if not message.text:
        return
    text = message.text.strip()
    if not text.startswith("/"):
        return
    user = message.from_user
    member = await chat.get_member(user.id)
    if member.status in ("creator", "administrator"):
        return
    try:
        await message.delete()
    except Exception as e:
        logger.debug("cleanblue delete failed: %s", e)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["cleanblue", "bluestate"], cleanblue))
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(r"^/"), check_blue),
        group=25,
    )
