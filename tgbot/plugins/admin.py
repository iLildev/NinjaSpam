"""
plugins/admin.py — Administrative privilege and group management commands.

Commands:
  /promote [user]        — Grant admin rights matching the bot's own permissions.
  /demote  [user]        — Remove all admin rights from a user.
  /pin     [notify]      — Pin the replied-to message (silent by default).
  /unpin                 — Unpin the currently pinned message.
  /invitelink            — Generate or retrieve the group invite link.
  /adminlist             — List all current administrators.
  /reload                — Refresh the cached admin list from Telegram (GroupHelp).

All mutating actions are logged via @loggable.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Chat, ChatMember, ChatMemberAdministrator, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import (
    bot_admin,
    can_pin,
    can_promote,
    is_user_admin,
    user_admin,
)
from core.helpers.extraction import extract_user_and_text
from core.log_channel import loggable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /promote
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_promote
@loggable
async def promote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Promote a user to administrator with the same rights the bot currently holds.

    The bot cannot grant permissions it doesn't have itself — Telegram enforces
    this at the API level.

    Usage:
        /promote @username
        /promote <reply>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify a user to promote.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("I'm already an admin — no need to promote me.")
        return None

    # Read the bot's own permission set to copy.
    try:
        bot_member: ChatMember = await chat.get_member(context.bot.id)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't read my own permissions: {exc.message}")
        return None

    # Build the permission flags to grant, capped by what the bot holds.
    can_change_info: bool = False
    can_delete_messages: bool = False
    can_restrict_members: bool = False
    can_invite_users: bool = False
    can_pin_messages: bool = False
    can_manage_chat: bool = False

    if isinstance(bot_member, ChatMemberAdministrator):
        can_change_info = bool(getattr(bot_member, "can_change_info", False))
        can_delete_messages = bool(getattr(bot_member, "can_delete_messages", False))
        can_restrict_members = bool(getattr(bot_member, "can_restrict_members", False))
        can_invite_users = bool(getattr(bot_member, "can_invite_users", False))
        can_pin_messages = bool(getattr(bot_member, "can_pin_messages", False))
        can_manage_chat = bool(getattr(bot_member, "can_manage_chat", False))

    try:
        await context.bot.promote_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            can_change_info=can_change_info,
            can_delete_messages=can_delete_messages,
            can_restrict_members=can_restrict_members,
            can_invite_users=can_invite_users,
            can_pin_messages=can_pin_messages,
            can_manage_chat=can_manage_chat,
        )
    except BadRequest as exc:
        await message.reply_text(f"Couldn't promote user: {exc.message}")
        return None

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#PROMOTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
    )

    # Invalidate the admin cache so next permission check reflects the new role.
    from core.helpers.chat_status import invalidate_admin_cache
    invalidate_admin_cache(chat.id, user_id)

    await message.reply_text(f"Promoted user {user_id} to administrator.")
    return log_msg


# ---------------------------------------------------------------------------
# /demote
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_promote
@loggable
async def demote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Strip all administrator permissions from a user.

    The group creator (owner) cannot be demoted — Telegram rejects it.

    Usage:
        /demote @username
        /demote <reply>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify a user to demote.")
        return None

    # Guard: cannot demote the group creator.
    try:
        target: ChatMember = await chat.get_member(user_id)
        if target.status == ChatMember.OWNER:
            await message.reply_text("I can't demote the group creator.")
            return None
        if target.status != ChatMember.ADMINISTRATOR:
            await message.reply_text("That user isn't an administrator.")
            return None
    except BadRequest as exc:
        await message.reply_text(f"Couldn't look up that user: {exc.message}")
        return None

    try:
        await context.bot.promote_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            can_change_info=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_chat=False,
            can_manage_video_chats=False,
        )
    except BadRequest as exc:
        if exc.message == "Chat_admin_required":
            await message.reply_text(
                "I can only demote admins that I promoted myself."
            )
        else:
            await message.reply_text(f"Failed to demote: {exc.message}")
        return None

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#DEMOTE\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>"
    )

    # Invalidate the admin cache so next permission check reflects the demotion.
    from core.helpers.chat_status import invalidate_admin_cache
    invalidate_admin_cache(chat.id, user_id)

    await message.reply_text(f"Demoted user {user_id}.")
    return log_msg


# ---------------------------------------------------------------------------
# /pin
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_pin
@loggable
async def pin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Pin the replied-to message.

    By default the pin is silent (no notification).  Pass ``notify``, ``loud``,
    or ``violent`` as an argument to send a notification to all members.

    Usage:
        /pin             — Silent pin (no notification).
        /pin notify      — Pin with notification.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not message.reply_to_message:
        await message.reply_text("Reply to the message you want to pin.")
        return None

    is_loud: bool = bool(
        context.args
        and context.args[0].lower() in ("notify", "loud", "violent")
    )
    target_message_id: int = message.reply_to_message.message_id

    try:
        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=target_message_id,
            disable_notification=not is_loud,
        )
    except BadRequest as exc:
        if exc.message == "Chat_not_modified":
            await message.reply_text("That message is already pinned.")
        else:
            await message.reply_text(f"Failed to pin: {exc.message}")
        return None

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#PIN\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<b>Loud:</b> {is_loud}"
    )

    return log_msg


# ---------------------------------------------------------------------------
# /unpin
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@can_pin
@loggable
async def unpin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Unpin the currently pinned message."""
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    try:
        await context.bot.unpin_chat_message(chat_id=chat.id)
    except BadRequest as exc:
        if exc.message == "Chat_not_modified":
            await message.reply_text("No message is currently pinned.")
        else:
            await message.reply_text(f"Failed to unpin: {exc.message}")
        return None

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#UNPIN\n"
        f"<b>Admin:</b> {user.mention_html()}"
    )

    await message.reply_text("Unpinned the current message.")
    return log_msg


# ---------------------------------------------------------------------------
# /invitelink
# ---------------------------------------------------------------------------

@user_admin
async def invite_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Return the group's invite link.

    For public groups the @username link is returned.  For private groups or
    supergroups the bot creates / retrieves the invite link if it has the
    appropriate permission.

    Usage:
        /invitelink
    """
    chat = update.effective_chat
    message = update.effective_message

    if chat.type == Chat.PRIVATE:
        await message.reply_text("This command is for groups only.")
        return

    if chat.username:
        await message.reply_text(
            f"Invite link: https://t.me/{chat.username}",
            disable_web_page_preview=True,
        )
        return

    try:
        link: str = await context.bot.export_chat_invite_link(chat_id=chat.id)
        await message.reply_text(
            f"Invite link: {link}", disable_web_page_preview=True
        )
    except BadRequest as exc:
        await message.reply_text(
            f"I don't have permission to get the invite link: {exc.message}"
        )


# ---------------------------------------------------------------------------
# /adminlist
# ---------------------------------------------------------------------------

async def adminlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all current administrators in the group."""
    chat = update.effective_chat
    message = update.effective_message

    try:
        admins = await chat.get_administrators()
    except BadRequest as exc:
        await message.reply_text(f"Couldn't retrieve admin list: {exc.message}")
        return

    lines: list[str] = ["<b>Administrators:</b>"]
    for admin in admins:
        admin_user = admin.user
        if admin_user.is_bot:
            continue
        if admin_user.username:
            ref: str = f'<a href="https://t.me/{admin_user.username}">{admin_user.first_name}</a>'
        else:
            ref = f'<a href="tg://user?id={admin_user.id}">{admin_user.first_name}</a>'

        title: str = f" — {admin.custom_title}" if getattr(admin, "custom_title", None) else ""
        status: str = " [creator]" if admin.status == ChatMember.OWNER else ""
        lines.append(f"• {ref}{title}{status}")

    await message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# /reload  (GroupHelp feature — refresh admin cache)
# ---------------------------------------------------------------------------

@user_admin
async def reload_admins(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Refresh the cached admin list for this group from Telegram.

    Telegram caches administrator lists on the bot side; if you recently
    promoted or demoted someone their new status may not be reflected
    immediately.  /reload forces a fresh fetch and clears the local cache.

    Usage:
        /reload
    """
    chat = update.effective_chat
    message = update.effective_message

    # Fetch fresh admin list from Telegram.
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't fetch admin list: {exc.message}")
        return

    # Invalidate per-user admin cache entries for this chat.
    from core.helpers.chat_status import invalidate_admin_cache
    for admin in admins:
        invalidate_admin_cache(chat.id, admin.user.id)

    await message.reply_html(
        f"✅ Admin cache refreshed — <b>{len(admins)}</b> admin(s) found."
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all admin command handlers."""
    application.add_handler(
        CommandHandler("promote", promote, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("demote", demote, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("pin", pin, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unpin", unpin, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("invitelink", invite_link, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("adminlist", adminlist, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("reload", reload_admins, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: admin")
