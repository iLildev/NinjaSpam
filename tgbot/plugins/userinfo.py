"""
plugins/userinfo.py — User and chat information commands.

Commands:
  /id [@user|reply]    — Show Telegram ID of a user or the current chat.
  /info [@user|reply]  — Detailed user profile (name, username, ID, status).
  /chatinfo            — Detailed group/channel profile.
  /staff               — List all group administrators with their titles.
  /infopvt [@user]     — Send full user info to the requesting admin's PM (GroupHelp).

All commands work in both private and group chats.  User lookups fall back
to the local database when the Telegram API cannot resolve the account.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Chat, ChatMember, Update, User
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.extraction import extract_user_and_text

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _user_mention_html(user: User) -> str:
    """Return a clickable HTML mention for *user*."""
    return f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"


async def _resolve_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[User]:
    """
    Resolve the target user from the command arguments or reply context.

    Returns the User object, or None if no user could be found.
    """
    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None:
        return update.effective_user

    try:
        chat = await context.bot.get_chat(user_id)
        return chat if hasattr(chat, "first_name") else None
    except BadRequest:
        return None


# ---------------------------------------------------------------------------
# /id
# ---------------------------------------------------------------------------

async def get_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show the Telegram ID of a user or the current chat.

    In a group:
        /id              → shows the group's chat ID.
        /id @username    → shows that user's ID.
        /id (as reply)   → shows the replied user's ID.

    In private:
        /id              → shows your own user ID.
    """
    message = update.effective_message
    chat = update.effective_chat

    if not context.args and not message.reply_to_message:
        await message.reply_html(
            f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
            f"<b>Your ID:</b> <code>{update.effective_user.id}</code>"
        )
        return

    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None and message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id

    if user_id:
        await message.reply_html(f"<b>User ID:</b> <code>{user_id}</code>")
    else:
        await message.reply_text("Couldn't resolve that user's ID.")


# ---------------------------------------------------------------------------
# /info
# ---------------------------------------------------------------------------

async def user_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show detailed information about a Telegram user.

    Displayed fields:
    - Full name, username, user ID.
    - Account type (bot / human).
    - Group status (admin / member / restricted / banned) when called in a group.
    - Whether the user is globally banned by this bot.

    Usage:
        /info              → info about yourself.
        /info @username    → info about that user.
        /info (as reply)   → info about the replied user.
    """
    message = update.effective_message
    chat = update.effective_chat

    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None:
        # Default to the command sender.
        target = update.effective_user
    else:
        try:
            chat_obj = await context.bot.get_chat(user_id)
            # get_chat on a user ID returns a Chat object — extract the User fields.
            target = chat_obj
        except BadRequest as exc:
            await message.reply_text(f"Couldn't find that user: {exc.message}")
            return

    if target is None:
        await message.reply_text("Couldn't resolve that user.")
        return

    uid: int = target.id
    first: str = getattr(target, "first_name", "") or ""
    last: str = getattr(target, "last_name", "") or ""
    full_name: str = (first + " " + last).strip() or "N/A"
    username: str = getattr(target, "username", None) or ""
    is_bot: bool = getattr(target, "is_bot", False)

    lines = [
        f"<b>User Info</b>",
        f"",
        f"<b>Name:</b> {full_name}",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Username:</b> {'@' + username if username else 'None'}",
        f"<b>Type:</b> {'🤖 Bot' if is_bot else '👤 Human'}",
    ]

    # Group status (only meaningful inside a group).
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member: ChatMember = await chat.get_member(uid)
            status_map = {
                ChatMember.OWNER: "👑 Creator",
                ChatMember.ADMINISTRATOR: "⭐ Administrator",
                ChatMember.MEMBER: "👤 Member",
                ChatMember.RESTRICTED: "🔇 Restricted",
                ChatMember.BANNED: "🚫 Banned",
                ChatMember.LEFT: "🚶 Left",
            }
            lines.append(f"<b>Status:</b> {status_map.get(member.status, member.status)}")
        except BadRequest:
            pass

    # Fetch DB data: global ban + city/timezone from user profile.
    from datetime import datetime
    import pytz
    from database.engine import get_session
    from database.models import User as UserModel
    from database.models_extra import UserTimezone

    async with get_session() as session:
        db_user = await session.get(UserModel, uid)
        if db_user and db_user.is_globally_banned:
            lines.append(
                f"<b>Global Ban:</b> 🚫 Yes — {db_user.global_ban_reason or 'No reason given'}"
            )

        tz_row = await session.get(UserTimezone, uid)

    # City & local time (only if the user has set their timezone).
    if tz_row:
        lines.append(f"<b>City:</b> 📍 {tz_row.city_label}")
        try:
            tz = pytz.timezone(tz_row.timezone_name)
            local_now = datetime.now(tz).strftime("%H:%M (%Z)")
            lines.append(f"<b>Local Time:</b> 🕐 {local_now}")
        except pytz.UnknownTimeZoneError:
            pass

    # Add a mention link.
    lines.append(f"\n<b>Mention:</b> <a href='tg://user?id={uid}'>Click here</a>")

    await message.reply_html("\n".join(lines), disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /chatinfo
# ---------------------------------------------------------------------------

async def chat_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show detailed information about the current group or channel.

    Displayed fields:
    - Title, username, chat ID, type.
    - Member count.
    - Description.
    - Slow mode delay (if any).
    - Whether the chat is a supergroup / has a public username.
    """
    message = update.effective_message
    chat = update.effective_chat

    try:
        full_chat = await context.bot.get_chat(chat.id)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't fetch chat info: {exc.message}")
        return

    chat_type_map = {
        ChatType.GROUP: "👥 Group",
        ChatType.SUPERGROUP: "👥 Supergroup",
        ChatType.CHANNEL: "📢 Channel",
        ChatType.PRIVATE: "👤 Private",
    }

    member_count: str = "N/A"
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        member_count = f"{count:,}"
    except BadRequest:
        pass

    lines = [
        f"<b>Chat Info</b>",
        f"",
        f"<b>Title:</b> {full_chat.title or 'N/A'}",
        f"<b>ID:</b> <code>{full_chat.id}</code>",
        f"<b>Type:</b> {chat_type_map.get(full_chat.type, full_chat.type)}",
        f"<b>Members:</b> {member_count}",
    ]

    if full_chat.username:
        lines.append(f"<b>Username:</b> @{full_chat.username}")
        lines.append(f"<b>Link:</b> https://t.me/{full_chat.username}")

    if getattr(full_chat, "description", None):
        desc = full_chat.description[:200] + ("…" if len(full_chat.description) > 200 else "")
        lines.append(f"\n<b>Description:</b>\n{desc}")

    slow_mode: int = getattr(full_chat, "slow_mode_delay", 0) or 0
    if slow_mode:
        lines.append(f"\n<b>Slow Mode:</b> {slow_mode}s")

    await message.reply_html("\n".join(lines), disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /staff
# ---------------------------------------------------------------------------

async def staff(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    List all current group administrators with their titles.

    Sorted: owner first, then alphabetically by name.
    """
    chat = update.effective_chat
    message = update.effective_message

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except BadRequest as exc:
        await message.reply_text(f"Couldn't fetch admin list: {exc.message}")
        return

    owner_lines = []
    admin_lines = []

    for admin in admins:
        user = admin.user
        if user.is_bot:
            continue
        name = user.full_name or str(user.id)
        mention = f"<a href='tg://user?id={user.id}'>{name}</a>"
        title = getattr(admin, "custom_title", None) or ""
        title_suffix = f" — <i>{title}</i>" if title else ""

        if admin.status == ChatMember.OWNER:
            owner_lines.append(f"👑 {mention}{title_suffix}")
        else:
            admin_lines.append(f"⭐ {mention}{title_suffix}")

    lines = [f"<b>Staff — {chat.title}</b>", ""]
    lines += owner_lines
    lines += sorted(admin_lines)
    lines.append(f"\n<i>{len(owner_lines) + len(admin_lines)} admin(s) total</i>")

    await message.reply_html("\n".join(lines))


# ---------------------------------------------------------------------------
# /infopvt  (GroupHelp feature — send user info to admin's PM)
# ---------------------------------------------------------------------------

async def infopvt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Send detailed user info to the requesting admin's private messages.

    Exactly the same data as /info, but delivered silently to the admin's
    DM so the group timeline isn't cluttered with profile lookups.

    Usage (in a group):
        /infopvt @username
        /infopvt (as reply)
    """
    from core.helpers.chat_status import is_user_admin

    message = update.effective_message
    chat = update.effective_chat
    requester = update.effective_user

    if not requester:
        return

    # Only group admins may use this command.
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not await is_user_admin(chat, requester.id):
            await message.reply_text("This command is only for group admins.")
            return

    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None:
        target_user = requester
    else:
        try:
            chat_obj = await context.bot.get_chat(user_id)
            target_user = chat_obj
        except BadRequest as exc:
            await message.reply_text(f"Couldn't find that user: {exc.message}")
            return

    if target_user is None:
        await message.reply_text("Couldn't resolve that user.")
        return

    uid: int = target_user.id
    first: str = getattr(target_user, "first_name", "") or ""
    last: str = getattr(target_user, "last_name", "") or ""
    full_name: str = (first + " " + last).strip() or "N/A"
    username: str = getattr(target_user, "username", None) or ""
    is_bot: bool = getattr(target_user, "is_bot", False)

    lines = [
        f"<b>User Info (via /infopvt)</b>",
        f"",
        f"<b>Name:</b> {full_name}",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Username:</b> {'@' + username if username else 'None'}",
        f"<b>Type:</b> {'🤖 Bot' if is_bot else '👤 Human'}",
        f"<b>Group:</b> {chat.title if chat.type != ChatType.PRIVATE else 'Private chat'}",
    ]

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member: ChatMember = await chat.get_member(uid)
            status_map = {
                ChatMember.OWNER: "👑 Creator",
                ChatMember.ADMINISTRATOR: "⭐ Administrator",
                ChatMember.MEMBER: "👤 Member",
                ChatMember.RESTRICTED: "🔇 Restricted",
                ChatMember.BANNED: "🚫 Banned",
                ChatMember.LEFT: "🚶 Left",
            }
            lines.append(f"<b>Status:</b> {status_map.get(member.status, member.status)}")
        except BadRequest:
            pass

    from datetime import datetime
    from database.engine import get_session
    from database.models import User as UserModel

    async with get_session() as session:
        db_user = await session.get(UserModel, uid)
        if db_user and db_user.is_globally_banned:
            lines.append(
                f"<b>Global Ban:</b> 🚫 Yes — {db_user.global_ban_reason or 'No reason given'}"
            )

    lines.append(f"\n<b>Mention:</b> <a href='tg://user?id={uid}'>Click here</a>")

    info_text = "\n".join(lines)

    # Send to admin's PM.
    try:
        await context.bot.send_message(
            chat_id=requester.id,
            text=info_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        # Acknowledge in the group.
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            notice = await message.reply_text("ℹ️ Info sent to your PM.")
            import asyncio
            await asyncio.sleep(5)
            try:
                await notice.delete()
                await message.delete()
            except BadRequest:
                pass
    except Exception:
        # Bot was never started in PM — tell the admin.
        await message.reply_html(
            f"I couldn't send to your PM — please start a private chat with me first: "
            f"<a href='https://t.me/{context.bot.username}'>@{context.bot.username}</a>"
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register user and chat info commands."""
    application.add_handler(CommandHandler("id", get_id))
    application.add_handler(CommandHandler("info", user_info))
    application.add_handler(
        CommandHandler("chatinfo", chat_info, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("staff", staff, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("infopvt", infopvt, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: userinfo")
