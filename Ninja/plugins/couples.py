"""
plugins/couples.py — Couple of the Day.

Picks two random non-bot members from the group each day and
announces them as the "couple of the day". Results reset at midnight.

Commands:
  /couple  — Show (or pick) today's couple.

Storage: in-memory per bot run (sufficient for daily rotation).
"""

from __future__ import annotations

import random
from datetime import date
from typing import Optional, Tuple

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

_couples: dict[int, Tuple[int, int, str]] = {}


async def couple(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat

    if chat.type == "private":
        await message.reply_text("This command only works in groups.")
        return

    today = str(date.today())

    cached = _couples.get(chat.id)
    if cached and cached[2] == today:
        c1_id, c2_id, _ = cached
        try:
            c1 = await context.bot.get_chat_member(chat.id, c1_id)
            c2 = await context.bot.get_chat_member(chat.id, c2_id)
            c1_name = c1.user.mention_html()
            c2_name = c2.user.mention_html()
        except BadRequest:
            del _couples[chat.id]
            await couple(update, context)
            return
        await message.reply_text(
            f"💑 <b>Couple of the day:</b>\n\n{c1_name} ❤️ {c2_name}\n\n"
            f"<i>Resets tomorrow.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    members = []
    try:
        async for member in chat.get_members():
            if not member.user.is_bot:
                members.append(member.user.id)
    except Exception:
        await message.reply_text("Couldn't fetch members. Make sure I have the right permissions.")
        return

    if len(members) < 2:
        await message.reply_text("Not enough non-bot members to pick a couple!")
        return

    c1_id, c2_id = random.sample(members, 2)
    _couples[chat.id] = (c1_id, c2_id, today)

    try:
        c1 = await context.bot.get_chat_member(chat.id, c1_id)
        c2 = await context.bot.get_chat_member(chat.id, c2_id)
        c1_name = c1.user.mention_html()
        c2_name = c2.user.mention_html()
    except BadRequest as e:
        await message.reply_text(f"Error fetching users: {e}")
        return

    await message.reply_text(
        f"💑 <b>Couple of the day:</b>\n\n{c1_name} ❤️ {c2_name}\n\n"
        f"<i>Resets tomorrow.</i>",
        parse_mode=ParseMode.HTML,
    )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["couple", "couples"], couple))
