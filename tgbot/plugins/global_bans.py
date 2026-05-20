"""
plugins/global_bans.py — Global ban system for bot owner / sudo users.

Commands (sudo/owner only):
  /gban  [user] [reason]  — Globally ban a user across all managed groups.
  /ungban [user]           — Remove a global ban.
  /gbanlist                — Download a .txt file listing all gbanned users.

Per-chat admin commands:
  /gbanstat <on|off>       — Toggle global ban enforcement for this chat.

Enforcement:
  When STRICT_GBAN is True in config, a MessageHandler (group=6) checks
  every message sender and new_chat_members events against the gban list.

Notes:
  - Banning propagates to every Chat row where the bot is active.
  - Un-banning only removes from chats where the user's status is 'kicked'.
  - Already-gbanned user with a new reason → updates reason without re-banning.
  - Known Telegram error codes during propagation are silently ignored.
  - Sudo / Support users are immune to gbanning.
"""

from __future__ import annotations

import io
import logging
from typing import List, Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings as cfg
from database.engine import get_session
from database.models import Chat as ChatModel, User
from database.models_extra import ChatGbanToggle, GlobalBannedUser

logger = logging.getLogger(__name__)

GBAN_GROUP: int = 6

# Telegram error messages that are safe to ignore during batch gban propagation.
_GBAN_ERRORS: frozenset[str] = frozenset([
    "User is an administrator of the chat",
    "Method is available for supergroup and channel chats only",
    "Not enough rights to restrict/unrestrict chat member",
    "User_not_participant",
    "Peer_id_invalid",
    "Group chat was deactivated",
    "Need to be inviter of a user to kick it from a basic group",
    "Chat_admin_required",
    "Only the creator of a basic group can kick group administrators",
    "Channel_private",
    "Not in the chat",
    "Have no rights to send a message",
])


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _is_sudo(user_id: int) -> bool:
    """Return True if user_id is the bot owner or a sudo user."""
    return user_id == cfg.OWNER_ID or user_id in (cfg.SUDO_USERS or [])


def _is_protected(user_id: int) -> bool:
    """Return True if user_id cannot be globally banned."""
    return _is_sudo(user_id) or user_id in (cfg.SUPPORT_USERS or [])


# ---------------------------------------------------------------------------
# /gban
# ---------------------------------------------------------------------------

async def gban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Globally ban a user from all groups managed by the bot.

    Usage:
        /gban @username [reason]
        /gban <user_id> [reason]
    """
    from core.helpers.extraction import extract_user_and_text

    message = update.effective_message
    user = update.effective_user

    if not user or not _is_sudo(user.id):
        await message.reply_text("Only sudo users can use this command.")
        return

    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify a user to globally ban.")
        return

    if user_id == context.bot.id:
        await message.reply_text("I won't ban myself globally.")
        return

    if _is_protected(user_id):
        await message.reply_text("Sudo and support users cannot be globally banned.")
        return

    # Verify this is an actual user (not a channel/group chat).
    try:
        target = await context.bot.get_chat(user_id)
        if target.type != "private":
            await message.reply_text("Only user accounts can be globally banned.")
            return
        target_name: str = target.first_name or str(user_id)
    except BadRequest:
        target_name = str(user_id)

    async with get_session() as session:
        existing = await session.get(GlobalBannedUser, user_id)
        if existing:
            if not reason:
                await message.reply_text(
                    f"{target_name} is already globally banned. "
                    "Provide a new reason to update it."
                )
                return
            existing.reason = reason
            await message.reply_text(
                f"Updated gban reason for {target_name}.", parse_mode=ParseMode.HTML
            )
            # Also update User table flag.
            user_row = await session.get(User, user_id)
            if user_row:
                user_row.global_ban_reason = reason
            return

        # Create gban record.
        session.add(GlobalBannedUser(
            user_id=user_id,
            name=target_name,
            reason=reason or None,
        ))
        # Mark on User row for fast lookup.
        user_row = await session.get(User, user_id)
        if user_row:
            user_row.is_globally_banned = True
            user_row.global_ban_reason = reason or None

    # Propagate ban to all active chats.
    await message.reply_text(
        f"Globally banning <b>{target_name}</b>. This may take a moment…",
        parse_mode=ParseMode.HTML,
    )
    async with get_session() as session:
        result = await session.execute(
            select(ChatModel.id).where(ChatModel.is_active == True)
        )
        chat_ids: List[int] = [row[0] for row in result.all()]

    banned_count: int = 0
    for cid in chat_ids:
        try:
            await context.bot.ban_chat_member(chat_id=cid, user_id=user_id)
            banned_count += 1
        except BadRequest as exc:
            if exc.message not in _GBAN_ERRORS:
                logger.warning("gban propagation error in %s: %s", cid, exc.message)
        except Forbidden:
            pass

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""
    await message.reply_text(
        f"Globally banned <b>{target_name}</b> from {banned_count} group(s).{reason_line}",
        parse_mode=ParseMode.HTML,
    )

    # Notify all sudo / support users.
    notify_ids: List[int] = list({cfg.OWNER_ID} | set(cfg.SUDO_USERS or []) | set(cfg.SUPPORT_USERS or []))
    for notify_id in notify_ids:
        if notify_id == user.id:
            continue
        try:
            await context.bot.send_message(
                chat_id=notify_id,
                text=(
                    f"#GBAN\n"
                    f"<b>By:</b> {user.mention_html()}\n"
                    f"<b>User:</b> <a href='tg://user?id={user_id}'>{target_name}</a>\n"
                    f"<b>Groups:</b> {banned_count}"
                    f"{reason_line}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, Forbidden):
            pass


# ---------------------------------------------------------------------------
# /ungban
# ---------------------------------------------------------------------------

async def ungban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Remove a global ban and unban the user in all groups where they were kicked.

    Usage:
        /ungban @username
        /ungban <user_id>
    """
    from core.helpers.extraction import extract_user_and_text

    message = update.effective_message
    user = update.effective_user

    if not user or not _is_sudo(user.id):
        await message.reply_text("Only sudo users can use this command.")
        return

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify a user to un-gban.")
        return

    async with get_session() as session:
        record = await session.get(GlobalBannedUser, user_id)
        if not record:
            await message.reply_text("That user is not globally banned.")
            return
        await session.delete(record)
        user_row = await session.get(User, user_id)
        if user_row:
            user_row.is_globally_banned = False
            user_row.global_ban_reason = None

    await message.reply_text("Lifting global ban. This may take a moment…")

    async with get_session() as session:
        result = await session.execute(
            select(ChatModel.id).where(ChatModel.is_active == True)
        )
        chat_ids: List[int] = [row[0] for row in result.all()]

    unbanned_count: int = 0
    for cid in chat_ids:
        try:
            member = await context.bot.get_chat_member(chat_id=cid, user_id=user_id)
            from telegram import ChatMember
            if member.status == ChatMember.BANNED:
                await context.bot.unban_chat_member(chat_id=cid, user_id=user_id)
                unbanned_count += 1
        except (BadRequest, Forbidden):
            pass

    await message.reply_text(
        f"Global ban lifted. Unbanned from {unbanned_count} group(s)."
    )


# ---------------------------------------------------------------------------
# /gbanlist
# ---------------------------------------------------------------------------

async def gbanlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send a downloadable .txt file listing all globally banned users."""
    message = update.effective_message
    user = update.effective_user

    if not user or not _is_sudo(user.id):
        await message.reply_text("Only sudo users can use this command.")
        return

    async with get_session() as session:
        result = await session.execute(
            select(GlobalBannedUser).order_by(GlobalBannedUser.banned_at)
        )
        records = result.scalars().all()

    if not records:
        await message.reply_text("No users are globally banned.")
        return

    lines: List[str] = [
        f"{r.user_id} | {r.name} | {r.reason or 'No reason'}" for r in records
    ]
    content: str = "\n".join(lines)
    buf = io.BytesIO(content.encode())
    buf.name = "gban_list.txt"

    await message.reply_document(
        document=buf,
        caption=f"Global ban list — {len(records)} user(s).",
    )


# ---------------------------------------------------------------------------
# /gbanstat
# ---------------------------------------------------------------------------

async def gban_stat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Toggle global ban enforcement for this specific group.

    Usage:
        /gbanstat on   — Enable gban enforcement (default).
        /gbanstat off  — Disable gban enforcement for this group.
    """
    from core.helpers.chat_status import is_user_admin

    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user or not await is_user_admin(chat, user.id):
        await message.reply_text("This command is for group administrators.")
        return

    if not context.args:
        async with get_session() as session:
            toggle = await session.get(ChatGbanToggle, chat.id)
        enabled: bool = toggle.gban_enabled if toggle else True
        status: str = "enabled" if enabled else "disabled"
        await message.reply_text(
            f"Global ban enforcement is currently <b>{status}</b> in this group.",
            parse_mode=ParseMode.HTML,
        )
        return

    choice: str = context.args[0].lower()
    if choice == "on":
        enabled = True
    elif choice == "off":
        enabled = False
    else:
        await message.reply_text("Use /gbanstat on or /gbanstat off.")
        return

    async with get_session() as session:
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()
        toggle = await session.get(ChatGbanToggle, chat.id)
        if toggle:
            toggle.gban_enabled = enabled
        else:
            session.add(ChatGbanToggle(chat_id=chat.id, gban_enabled=enabled))

    await message.reply_text(
        f"Global ban enforcement <b>{'enabled' if enabled else 'disabled'}</b>.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Enforcement handler (STRICT_GBAN mode)
# ---------------------------------------------------------------------------

async def gban_enforce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Check each message sender and new members against the global ban list.

    Only active when config.STRICT_GBAN is True.
    """
    chat = update.effective_chat
    user = update.effective_user

    if not user or not chat:
        return

    if _is_protected(user.id):
        return

    # Check if this chat has gban disabled.
    async with get_session() as session:
        toggle = await session.get(ChatGbanToggle, chat.id)
        if toggle and not toggle.gban_enabled:
            return
        gban_record = await session.get(GlobalBannedUser, user.id)

    if not gban_record:
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        reason_line: str = f" Reason: {gban_record.reason}" if gban_record.reason else ""
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"Globally banned user <a href='tg://user?id={user.id}'>{user.first_name}</a> "
                f"detected and removed.{reason_line}"
            ),
            parse_mode=ParseMode.HTML,
        )
    except (BadRequest, Forbidden):
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register global ban commands and optional strict-enforcement handler."""
    application.add_handler(
        CommandHandler("gban", gban)
    )
    application.add_handler(
        CommandHandler("ungban", ungban)
    )
    application.add_handler(
        CommandHandler("gbanlist", gbanlist)
    )
    application.add_handler(
        CommandHandler("gbanstat", gban_stat, filters=filters.ChatType.GROUPS)
    )

    if getattr(cfg, "STRICT_GBAN", False):
        application.add_handler(
            MessageHandler(filters.ChatType.GROUPS, gban_enforce),
            group=GBAN_GROUP,
        )
        application.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
                gban_enforce,
            ),
            group=GBAN_GROUP,
        )
        logger.info("STRICT_GBAN enabled — enforcement active.")

    logger.info("Plugin loaded: global_bans")
