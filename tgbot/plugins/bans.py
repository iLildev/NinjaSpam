"""
plugins/bans.py — Ban, kick, temporary-ban, and unban command handlers.

Commands:
  /ban   [user] [reason]  — Permanently ban a user from the group.
  /tban  [user] <time> [reason] — Temporarily ban (10m / 2h / 3d).
  /kick  [user] [reason]  — Remove the user (can rejoin; no ban record).
  /kickme                 — Allow a regular member to kick themselves.
  /unban [user]           — Lift an active ban (only if user is absent).

All actions are logged to the configured log channel via @loggable.
"""

from __future__ import annotations

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
from core.log_channel import loggable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /ban — permanent ban
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Permanently ban a user from the group.

    Usage:
        /ban @username [reason]
        /ban <reply> [reason]
        /ban <user_id> [reason]
    """
    chat: Chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "I can't figure out who you want to ban.\n"
            "Reply to their message, or pass @username / user_id."
        )
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text("I can't ban an administrator.")
        return None

    # Prevent banning the bot itself.
    if user_id == context.bot.id:
        await message.reply_text("I won't ban myself.")
        return None

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
    except BadRequest as exc:
        await message.reply_text(f"Failed to ban: {exc.message}")
        return None

    # Record the ban for the appeals system
    try:
        from database.engine import get_session
        from database.models_extra import BanRecord
        from sqlalchemy import select as sa_select
        async with get_session() as session:
            res = await session.execute(
                sa_select(BanRecord).where(
                    BanRecord.chat_id == chat.id,
                    BanRecord.user_id == user_id,
                )
            )
            br = res.scalar_one_or_none()
            if br is None:
                session.add(BanRecord(
                    chat_id=chat.id, user_id=user_id,
                    reason=reason or None, banned_by=user.id, unbanned=False,
                ))
            else:
                br.unbanned = False
                br.reason = reason or br.reason
                br.banned_by = user.id
    except Exception as _e:
        logger.debug("Could not write BanRecord: %s", _e)

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#BAN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
        f"{reason_line}"
    )

    await message.reply_text(
        f"Banned user {user_id}." + (f"\n<b>Reason:</b> {reason}" if reason else ""),
        parse_mode=ParseMode.HTML,
    )
    return log_msg


# ---------------------------------------------------------------------------
# /tban — temporary ban
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def temp_ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Temporarily ban a user.

    The first token after the username/ID is consumed as the time string.
    The remainder (if any) is the reason.

    Usage:
        /tban @username 2h
        /tban <reply> 30m Too many spam messages
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, args_text = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Specify a user and a duration, e.g.:\n"
            "/tban @username 2h [optional reason]"
        )
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text("I can't ban an administrator.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("I won't ban myself.")
        return None

    if not args_text:
        await message.reply_text(
            "Provide a duration after the username, e.g.:\n"
            "/tban @username 2h [optional reason]"
        )
        return None

    parts = args_text.split(None, 1)
    time_str: str = parts[0]
    reason: str = parts[1] if len(parts) > 1 else ""

    until: Optional[datetime] = extract_time(time_str)
    if until is None:
        await message.reply_text(
            f"Invalid duration '{time_str}'. Use formats like 10m, 2h, or 3d."
        )
        return None

    try:
        await context.bot.ban_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            until_date=until,
        )
    except BadRequest as exc:
        await message.reply_text(f"Failed to temporarily ban: {exc.message}")
        return None

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#TEMP_BAN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"<b>Duration:</b> {time_str}"
        f"{reason_line}"
    )

    await message.reply_text(
        f"Banned user {user_id} for {time_str}."
        + (f"\n<b>Reason:</b> {reason}" if reason else ""),
        parse_mode=ParseMode.HTML,
    )
    return log_msg


# ---------------------------------------------------------------------------
# /kick — remove user (can rejoin)
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def kick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Kick a user from the group (they can rejoin via invite link).

    Implemented as ban + immediate unban, which is Telegram's kick semantics.

    Usage:
        /kick @username [reason]
        /kick <reply> [reason]
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Reply to the user's message or pass @username / user_id."
        )
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text("I can't kick an administrator.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("Nice try.")
        return None

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id)
    except BadRequest as exc:
        await message.reply_text(f"Failed to kick: {exc.message}")
        return None

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#KICK\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
        f"{reason_line}"
    )

    await message.reply_text(
        f"Kicked user {user_id}."
        + (f"\n<b>Reason:</b> {reason}" if reason else ""),
        parse_mode=ParseMode.HTML,
    )
    return log_msg


# ---------------------------------------------------------------------------
# /kickme — self-kick for regular members
# ---------------------------------------------------------------------------

async def kickme(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Allow a non-admin member to voluntarily leave by having the bot kick them."""
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user:
        return

    if await is_user_admin(chat, user.id):
        await message.reply_text("Admins can leave on their own — /kickme won't work for you.")
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
        await message.reply_text("Done — you've been kicked. You can rejoin with an invite link.")
    except BadRequest as exc:
        await message.reply_text(f"Couldn't kick you: {exc.message}")


# ---------------------------------------------------------------------------
# /unban — lift an active ban
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def unban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Remove a ban, but ONLY if the user is NOT currently in the chat.

    Unbanning someone who is already in the group would be a no-op, and likely
    an admin mistake — we refuse and explain.

    Usage:
        /unban @username
        /unban <user_id>
        /unban <reply>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Specify who to unban via @username, user_id, or reply."
        )
        return None

    # Guard: don't unban someone already inside the chat.
    if await is_user_in_chat(chat, user_id):
        await message.reply_text(
            "That user is already in this chat — nothing to unban."
        )
        return None

    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id)
    except BadRequest as exc:
        await message.reply_text(f"Failed to unban: {exc.message}")
        return None

    # Update BanRecord on unban
    try:
        from datetime import timezone
        from database.engine import get_session
        from database.models_extra import BanRecord
        from sqlalchemy import select as sa_select
        async with get_session() as session:
            res = await session.execute(
                sa_select(BanRecord).where(
                    BanRecord.chat_id == chat.id,
                    BanRecord.user_id == user_id,
                    BanRecord.unbanned == False,  # noqa: E712
                )
            )
            br = res.scalar_one_or_none()
            if br:
                br.unbanned = True
                br.unbanned_at = datetime.now(tz=timezone.utc)
    except Exception as _e:
        logger.debug("Could not update BanRecord on unban: %s", _e)

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#UNBAN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
    )

    await message.reply_text(f"Unbanned user {user_id}. They can now rejoin.")
    return log_msg


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all ban-related command handlers with the application."""
    application.add_handler(
        CommandHandler("ban", ban, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(["tban", "tempban"], temp_ban, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("kick", kick, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("kickme", kickme, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unban", unban, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: bans")
