"""
plugins/muting.py — Mute, temporary-mute, and unmute handlers.

Commands:
  /mute  [user] [reason]        — Permanently restrict all messages.
  /tmute [user] <time> [reason] — Temporary mute (10m / 2h / 3d).
  /unmute [user]                — Restore all Telegram permissions.

Muting uses Telegram's restrict_chat_member API with all send permissions
disabled.  Unmuting restores all permissions simultaneously.

All actions are logged via @loggable.
"""

from __future__ import annotations

import html
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
    is_user_ban_protected,
    user_admin,
)
from core.helpers.extraction import extract_user_and_text
from core.helpers.string_handling import extract_time
from core.i18n import get_chat_lang, t
from core.log_channel import loggable

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Helper — resolve a user display name for response messages
# ---------------------------------------------------------------------------

async def _user_mention(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return an HTML mention for the target user."""
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


# ---------------------------------------------------------------------------
# Internal: check member restriction status
# ---------------------------------------------------------------------------

async def _is_muted(chat: Chat, user_id: int) -> bool:
    """Return True if the member cannot send messages."""
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest:
        return False
    if member.status == ChatMember.RESTRICTED:
        return not getattr(member, "can_send_messages", True)
    return False


async def _is_fully_unmuted(chat: Chat, user_id: int) -> bool:
    """Return True only when all core permissions are currently granted."""
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
    lang = await get_chat_lang(chat.id)

    if not user_id:
        await message.reply_text(t("ban_missing_target", lang))
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("mute_admin", lang))
        return None

    if user_id == context.bot.id:
        await message.reply_text("🙃 I won't mute myself.")
        return None

    if await _is_muted(chat, user_id):
        await message.reply_text(t("mute_already", lang))
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_MUTE_PERMISSIONS,
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Failed to mute: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _user_mention(user_id, update, context)
    reason_line: str = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"🔇 <b>User Muted!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"👮 <b>By:</b> {user.mention_html()}"
        f"{reason_line}"
    )

    log_msg: str = (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#MUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)"
        f"{reason_line}"
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

    Usage:
        /tmute @username 30m [reason]
        /tmute <reply> 2h Too many off-topic messages
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, args_text = await extract_user_and_text(update, context)
    lang = await get_chat_lang(chat.id)

    if not user_id:
        await message.reply_text(
            "⚠️ Provide a user and a duration:\n"
            "<code>/tmute @username 2h [reason]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("mute_admin", lang))
        return None

    if user_id == context.bot.id:
        await message.reply_text("🙃 I won't mute myself.")
        return None

    if not args_text:
        await message.reply_text(
            "⚠️ Provide a duration after the username:\n"
            "<code>/tmute @username 2h [reason]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    parts = args_text.split(None, 1)
    time_str: str = parts[0]
    reason: str = parts[1] if len(parts) > 1 else ""

    until: Optional[datetime] = extract_time(time_str)
    if until is None:
        await message.reply_text(
            f"⚠️ Invalid duration <code>{html.escape(time_str)}</code>. "
            f"Use formats like <code>10m</code>, <code>2h</code>, or <code>3d</code>.",
            parse_mode=ParseMode.HTML,
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
        await message.reply_text(
            f"⚠️ Failed to mute: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _user_mention(user_id, update, context)
    reason_line: str = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"⏱ <b>Temp Mute!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"⏳ <b>Duration:</b> <code>{html.escape(time_str)}</code>\n"
        f"👮 <b>By:</b> {user.mention_html()}"
        f"{reason_line}"
    )

    log_msg: str = (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#TEMP_MUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)\n"
        f"<b>Duration:</b> {html.escape(time_str)}"
        f"{reason_line}"
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
    Restore all Telegram messaging permissions to a restricted user.

    Usage:
        /unmute @username
        /unmute <reply>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    lang = await get_chat_lang(chat.id)

    if not user_id:
        await message.reply_text(t("ban_missing_target", lang))
        return None

    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Couldn't find that user: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    if member.status in (ChatMember.LEFT, ChatMember.BANNED):
        await message.reply_text(
            "ℹ️ That user is no longer in this chat — nothing to unmute."
        )
        return None

    if await _is_fully_unmuted(chat, user_id):
        await message.reply_text(t("unmute_already", lang))
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_FULL_PERMISSIONS,
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ Failed to unmute: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _user_mention(user_id, update, context)

    await message.reply_html(
        f"🔊 <b>User Unmuted!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"👮 <b>By:</b> {user.mention_html()}"
    )

    log_msg: str = (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#UNMUTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)"
    )
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
