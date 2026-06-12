"""
plugins/bans.py — Ban, Kick, and Unban system.

Commands:
  /ban   [user] [reason]         — Permanent ban.
  /ban   -clean [user] [reason] — Ban + delete recent messages.
  /tban  [user] <duration> [reason]  — Temporary ban (10m / 2h / 3d).
  /kick  [user] [reason]        — Kick (can rejoin).
  /kickme                        — Kick self.
  /unban [user]               — Remove ban.

All actions are logged in the log channel via @loggable.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Optional

from telegram import Chat, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import (
    bot_admin,
    can_restrict,
    is_user_admin,
    is_user_ban_protected,
    is_user_in_chat,
    user_admin,
)
from core.helpers.extraction import extract_user_and_text
from core.helpers.string_handling import extract_time
from core.i18n import t
from core.log_channel import loggable
from db.repositories import bans as bans_repo

logger = logging.getLogger(__name__)


async def _mention(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return HTML mention for the user."""
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        if u.id == user_id:
            return u.mention_html()
    try:
        chat = await context.bot.get_chat(user_id)
        name = html.escape(chat.full_name or chat.title or str(user_id))
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        return f'<a href="tg://user?id={user_id}">{user_id}</a>'


@user_admin
@bot_admin
@can_restrict
@loggable
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat: Chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, reason = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(t("ban_missing_target"))
        return None
    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("ban_admin"))
        return None
    if user_id == context.bot.id:
        await message.reply_text("🙃 No.")
        return None

    clean_mode = bool(
        (reason and reason.strip().startswith("-clean"))
        or (context.args and context.args[0].lower() == "-clean")
    )
    if clean_mode and reason:
        reason = reason.strip()[len("-clean"):].strip()

    try:
        await context.bot.ban_chat_member(
            chat_id=chat.id, user_id=user_id, revoke_messages=clean_mode
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Ban failed: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    await bans_repo.record_ban(chat.id, user_id, user.id, reason or "")

    mention = await _mention(user_id, update, context)
    reason_line = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""
    clean_line = "\n🧹 <i>Recent messages deleted.</i>" if clean_mode else ""

    await message.reply_html(
        f"🔨 <b>User Banned</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"👮 <b>By:</b> {user.mention_html()}"
        f"{reason_line}{clean_line}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"{'#CLEANBAN' if clean_mode else '#BAN'}\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)"
        f"{reason_line}{clean_line}"
    )


@user_admin
@bot_admin
@can_restrict
@loggable
async def temp_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, args_text = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(
            "⚠️ Specify user and duration:\n<code>/tban @username 2h [reason]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None
    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("ban_admin"))
        return None
    if not args_text:
        await message.reply_text(
            "⚠️ Add duration after username:\n<code>/tban @username 2h [reason]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    parts = args_text.split(None, 1)
    time_str = parts[0]
    reason = parts[1] if len(parts) > 1 else ""
    until: Optional[datetime] = extract_time(time_str)

    if until is None:
        await message.reply_text(
            f"⚠️ Invalid duration <code>{html.escape(time_str)}</code>. "
            f"Use: <code>10m</code>, <code>2h</code>, or <code>3d</code>.",
            parse_mode=ParseMode.HTML,
        )
        return None

    try:
        await context.bot.ban_chat_member(
            chat_id=chat.id, user_id=user_id, until_date=until
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Temporary ban failed: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _mention(user_id, update, context)
    reason_line = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"⏳ <b>Temporary Ban</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"⏱ <b>Duration:</b> <code>{html.escape(time_str)}</code>\n"
        f"👮 <b>By:</b> {user.mention_html()}"
        f"{reason_line}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#TEMP_BAN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)\n"
        f"<b>Duration:</b> {html.escape(time_str)}"
        f"{reason_line}"
    )


@user_admin
@bot_admin
@can_restrict
@loggable
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, reason = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(t("ban_missing_target"))
        return None
    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("ban_admin"))
        return None
    if user_id == context.bot.id:
        await message.reply_text("🙃 No.")
        return None

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id)
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Kick failed: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _mention(user_id, update, context)
    reason_line = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"👢 <b>User Kicked</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"👮 <b>By:</b> {user.mention_html()}"
        f"{reason_line}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#KICK\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)"
        f"{reason_line}"
    )


async def kickme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not user:
        return
    if await is_user_admin(chat, user.id):
        await message.reply_text("🛡 Admins leave on their own.")
        return
    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
        await message.reply_text("👋 Done. You can return via invite link.")
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Could not kick: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )


@user_admin
@bot_admin
@can_restrict
@loggable
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, _ = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(t("ban_missing_target"))
        return None
    if await is_user_in_chat(chat, user_id):
        await message.reply_text("ℹ️ User is already in the group.")
        return None

    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id)
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Unban failed: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    await bans_repo.record_unban(chat.id, user_id)
    mention = await _mention(user_id, update, context)

    await message.reply_html(
        f"✅ <b>Unbanned</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"👮 <b>By:</b> {user.mention_html()}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#UNBAN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)"
    )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("ban", ban, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(["tban", "tempban"], temp_ban, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("kick", kick, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("kickme", kickme, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("unban", unban, filters=filters.ChatType.GROUPS))
    logger.info("Plugin loaded: bans")
