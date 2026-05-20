"""
plugins/muting.py — Mute, temporary-mute, and unmute handlers.

Commands:
  /mute  [user] [reason]       — Permanently restrict all messages.
  /tmute [user] <time> [reason] — Temporary mute (10m / 2h / 3d).
  /unmute [user]               — Restore all four Telegram permissions.

Muting uses Telegram's restrict_chat_member API with ``can_send_messages=False``.
Unmuting restores the four core permissions simultaneously to match Marie's
original behaviour: messages, media_messages, other_messages, web_page_previews.

All actions are logged via @loggable.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import (
    bot_admin,
    can_restrict,
    is_user_admin,
    is_user_ban_protected,
    user_admin,
)
from core.helpers.extraction import extract_user_and_text
from core.helpers.string_handling import extract_time
from core.log_channel import loggable

logger = logging.getLogger(__name__)

# Permissions that constitute a fully restored (unmuted) member.
_FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)

# Permissions for a fully muted member (no messages of any kind).
_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


# ---------------------------------------------------------------------------
# Internal: check whether a member is currently muted
# ---------------------------------------------------------------------------

async def _is_muted(chat: Chat, user_id: int) -> bool:
    """
    Return True if the member cannot send messages.

    ``can_send_messages`` being either False or None signals a restricted
    member.  ``None`` is returned by Telegram when a restriction is inherited
    from group defaults rather than explicitly set.
    """
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest:
        return False

    if member.status == ChatMember.RESTRICTED:
        return not getattr(member, "can_send_messages", True)
    return False


async def _is_fully_unmuted(chat: Chat, user_id: int) -> bool:
    """Return True only when all four core permissions are currently granted."""
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest:
        return True

    if member.status != ChatMember.RESTRICTED:
        return True

    return bool(
        getattr(member, "can_send_messages", True)
        and getattr(member, "can_send_other_messages", True)
        and getattr(member, "can_add_web_page_previews", True)
    )


# ---------------------------------------------------------------------------
# /mute — permanent mute
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def mute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Permanently restrict a user so they cannot send any messages.

    Usage:
        /mute @username [reason]
        /mute <reply> [reason]
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
        await message.reply_text("I can't mute an administrator.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("I won't mute myself.")
        return None

    if await _is_muted(chat, user_id):
        await message.reply_text("That user is already muted.")
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_MUTE_PERMISSIONS,
        )
    except BadRequest as exc:
        await message.reply_text(f"Failed to mute: {exc.message}")
        return None

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#MUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
        f"{reason_line}"
    )

    await message.reply_text(
        f"Muted user {user_id}."
        + (f"\n<b>Reason:</b> {reason}" if reason else ""),
        parse_mode=ParseMode.HTML,
    )
    return log_msg


# ---------------------------------------------------------------------------
# /tmute — temporary mute
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def temp_mute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Temporarily restrict a user.

    The first token of the argument text is consumed as the duration.  The
    remainder (if any) is used as the reason.

    Usage:
        /tmute @username 30m [reason]
        /tmute <reply> 2h Too many off-topic messages
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, args_text = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Provide a user and a duration:\n"
            "/tmute @username 2h [optional reason]"
        )
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text("I can't mute an administrator.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("I won't mute myself.")
        return None

    if not args_text:
        await message.reply_text(
            "Provide a duration after the username:\n"
            "/tmute @username 2h [optional reason]"
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
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_MUTE_PERMISSIONS,
            until_date=until,
        )
    except BadRequest as exc:
        await message.reply_text(f"Failed to temporarily mute: {exc.message}")
        return None

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#TEMP_MUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"<b>Duration:</b> {time_str}"
        f"{reason_line}"
    )

    await message.reply_text(
        f"Muted user {user_id} for {time_str}."
        + (f"\n<b>Reason:</b> {reason}" if reason else ""),
        parse_mode=ParseMode.HTML,
    )
    return log_msg


# ---------------------------------------------------------------------------
# /unmute — restore all permissions
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_restrict
@loggable
async def unmute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Restore all four core Telegram messaging permissions to a restricted user.

    This deliberately restores ALL permissions (not just can_send_messages)
    because a previous /tmute may have restricted other message types as well.

    Usage:
        /unmute @username
        /unmute <reply>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Reply to the user's message or pass @username / user_id."
        )
        return None

    # Guard: refuse to unmute someone who is banned or has left.
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't find that user: {exc.message}")
        return None

    if member.status in (ChatMember.LEFT, ChatMember.BANNED):
        await message.reply_text(
            "That user is no longer in this chat — can't unmute them."
        )
        return None

    if await _is_fully_unmuted(chat, user_id):
        await message.reply_text("That user already has full messaging rights.")
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_FULL_PERMISSIONS,
        )
    except BadRequest as exc:
        await message.reply_text(f"Failed to unmute: {exc.message}")
        return None

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#UNMUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
    )

    await message.reply_text(f"Unmuted user {user_id}.")
    return log_msg


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all muting command handlers."""
    application.add_handler(
        CommandHandler("mute", mute, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(["tmute", "tempmute"], temp_mute, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unmute", unmute, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: muting")
