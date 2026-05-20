"""
plugins/group_tools.py — Group management utility commands.

Commands (admin only):
  /setgtitle <title>     — Set the group title.
  /setgpic               — Reply to photo/sticker to set as group profile picture.
  /delgpic               — Delete the group profile picture.
  /setdescription <text> — Set the group description.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from core.helpers.chat_status import user_admin

logger = logging.getLogger(__name__)


@user_admin
async def set_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    title = " ".join(context.args) if context.args else ""
    if not title:
        await message.reply_text("Usage: /setgtitle <new title>")
        return
    try:
        await context.bot.set_chat_title(chat.id, title)
        await message.reply_text(f"✅ Group title updated to: <b>{title}</b>", parse_mode=ParseMode.HTML)
    except BadRequest as e:
        await message.reply_text(f"Failed: {e}")


@user_admin
async def set_pic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    reply = message.reply_to_message

    if not reply:
        await message.reply_text("Reply to a photo or sticker to set as group picture.")
        return

    photo = None
    if reply.photo:
        photo = reply.photo[-1].file_id
    elif reply.sticker and not reply.sticker.is_animated and not reply.sticker.is_video:
        photo = reply.sticker.file_id
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("image"):
        photo = reply.document.file_id

    if not photo:
        await message.reply_text("Please reply to a photo or static sticker.")
        return

    try:
        file = await context.bot.get_file(photo)
        await context.bot.set_chat_photo(chat.id, file.file_id)
        await message.reply_text("✅ Group photo updated.")
    except BadRequest as e:
        await message.reply_text(f"Failed: {e}")


@user_admin
async def del_pic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    try:
        await context.bot.delete_chat_photo(chat.id)
        await update.effective_message.reply_text("✅ Group photo deleted.")
    except BadRequest as e:
        await update.effective_message.reply_text(f"Failed: {e}")


@user_admin
async def set_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    desc = " ".join(context.args) if context.args else ""
    try:
        await context.bot.set_chat_description(chat.id, desc)
        await message.reply_text("✅ Group description updated.")
    except BadRequest as e:
        await message.reply_text(f"Failed: {e}")


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("setgtitle", set_title))
    application.add_handler(CommandHandler("setgpic", set_pic))
    application.add_handler(CommandHandler("delgpic", del_pic))
    application.add_handler(CommandHandler("setdescription", set_description))
