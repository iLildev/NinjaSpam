"""
plugins/global_ignore.py — Owner-level global user ignore/blacklist.

These are users the bot will silently ignore across all chats
(not to be confused with per-chat blacklists).

Commands (OWNER only):
  /ignore   <user>  [reason]  — Add user to global ignore list.
  /notice   <user>            — Remove user from global ignore list.
  /ignoredlist                — List globally ignored users.
"""

from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import OWNER_IDS
from core.helpers.extraction import extract_user_and_text
from db.repositories import global_ignore as ignore_repo

logger = logging.getLogger(__name__)

IGNORE_GROUP = 5  # runs before global_bans (group 6) and all other handlers


async def _is_ignored(user_id: int) -> bool:
    return await ignore_repo.is_ignored(user_id)


async def enforce_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently delete messages from globally ignored users (owners are exempt)."""
    user = update.effective_user
    if not user or user.id in OWNER_IDS:
        return
    if not await ignore_repo.is_ignored(user.id):
        return
    try:
        if update.effective_message:
            await update.effective_message.delete()
    except Exception:
        pass  # no delete permission — silently skip


async def ignore_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id, reason = await extract_user_and_text(message, context.args)
    if not user_id:
        await message.reply_text("Provide a valid user.")
        return
    if user_id in OWNER_IDS:
        await message.reply_text("Cannot ignore an owner.")
        return
    
    success = await ignore_repo.add(user_id, reason or "")
    if not success:
        await message.reply_text("User is already globally ignored.")
        return

    try:
        u = await context.bot.get_chat(user_id)
        name = html.escape(u.first_name or str(user_id))
    except Exception:
        name = str(user_id)
    await message.reply_text(
        f"🚫 <b>{name}</b> is now globally ignored.",
        parse_mode=ParseMode.HTML,
    )


async def notice_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = await extract_user_and_text(message, context.args)
    if isinstance(user_id, tuple):
        user_id = user_id[0]
    if not user_id:
        await message.reply_text("Provide a valid user.")
        return
    
    success = await ignore_repo.remove(user_id)
    if not success:
        await message.reply_text("User is not globally ignored.")
        return

    await message.reply_text("✅ User is no longer globally ignored.")


async def ignored_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    users = await ignore_repo.get_all()
    
    if not users:
        await message.reply_text("No globally ignored users.")
        return
    lines = ["<b>Globally Ignored Users:</b>"]
    for u in users:
        try:
            chat = await context.bot.get_chat(u.user_id)
            entry = chat.mention_html()
        except Exception:
            entry = f"<code>{u.user_id}</code>"
        if u.reason:
            entry += f" — {html.escape(u.reason)}"
        lines.append(f"• {entry}")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler("ignore", ignore_user, filters=owner_filter))
    application.add_handler(CommandHandler("notice", notice_user, filters=owner_filter))
    application.add_handler(CommandHandler("ignoredlist", ignored_list, filters=owner_filter))
    # Enforce ignore: runs before all other message handlers (group 5)
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.User(user_id=list(OWNER_IDS)), enforce_ignore),
        group=IGNORE_GROUP,
    )
