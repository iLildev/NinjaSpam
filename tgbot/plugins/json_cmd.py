"""
plugins/json_cmd.py — Dump the raw JSON representation of a message.

Commands (admin only in groups, anyone in PM):
  /json  — Reply to a message to see its JSON structure.
"""

from __future__ import annotations

import io
import json

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes


async def json_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if chat.type != "private":
        member = await chat.get_member(message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply_text("Admins only in groups. You can use this in my PM.")
            return

    target = message.reply_to_message if message.reply_to_message else message
    data = target.to_dict()
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)

    if len(text) > 3800:
        with io.BytesIO(text.encode()) as f:
            f.name = "message.json"
            await context.bot.send_document(
                chat_id=chat.id,
                document=f,
                filename="message.json",
            )
    else:
        await message.reply_text(
            f"<code>{text}</code>",
            parse_mode=ParseMode.HTML,
        )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("json", json_cmd))
