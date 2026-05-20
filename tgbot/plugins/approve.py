"""
plugins/approve.py — User approval system (filter bypass whitelist).

Approved users are exempt from all automated filters in a specific chat:
  - Bayesian spam filter
  - Regex / word filters
  - Anti-flood
  - Anti-links
  - Blacklist
  - CAPTCHA

They are NOT exempt from manual moderation (/ban, /warn, /mute, etc.).

Commands:
  /approve [@user|reply]    — Approve a user in this chat.
  /disapprove [@user|reply] — Remove approval.
  /approved                 — List all approved users in this chat.
  /approval [@user|reply]   — Check if a specific user is approved.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models import Chat as ChatModel, User
from database.models_extra import ApprovedUser

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _ensure_user_and_chat(session, chat_id: int, user_id: int, title: str = "") -> None:
    if not await session.get(User, user_id):
        session.add(User(id=user_id, first_name=""))
        await session.flush()
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title))
        await session.flush()


# ---------------------------------------------------------------------------
# /approve
# ---------------------------------------------------------------------------

@user_admin
async def approve(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Approve a user so they bypass all automated filters in this group.

    Usage:
        /approve @username
        /approve <reply>
        /approve <user_id>
    """
    chat = update.effective_chat
    message = update.effective_message
    admin = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Specify who to approve: reply to their message or pass @username / ID."
        )
        return

    if user_id == context.bot.id:
        await message.reply_text("I don't need approval in my own group.")
        return

    async with get_session() as session:
        await _ensure_user_and_chat(session, chat.id, user_id, chat.title or "")

        existing = await session.execute(
            select(ApprovedUser).where(
                ApprovedUser.chat_id == chat.id,
                ApprovedUser.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none():
            await message.reply_html(
                f"<a href='tg://user?id={user_id}'>{user_id}</a> is already approved in this chat."
            )
            return

        session.add(ApprovedUser(
            chat_id=chat.id,
            user_id=user_id,
            approved_by=admin.id if admin else None,
        ))

    await message.reply_html(
        f"✅ <a href='tg://user?id={user_id}'>{user_id}</a> has been approved.\n"
        f"They are now exempt from all automated filters in this group."
    )
    log.info("User %s approved in chat %s by %s", user_id, chat.id, admin.id if admin else "?")


# ---------------------------------------------------------------------------
# /disapprove
# ---------------------------------------------------------------------------

@user_admin
async def disapprove(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Remove a user's approval so they are subject to filters again.

    Usage:
        /disapprove @username
        /disapprove <reply>
        /disapprove <user_id>
    """
    chat = update.effective_chat
    message = update.effective_message

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Specify who to disapprove: reply to their message or pass @username / ID."
        )
        return

    async with get_session() as session:
        result = await session.execute(
            select(ApprovedUser).where(
                ApprovedUser.chat_id == chat.id,
                ApprovedUser.user_id == user_id,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            await message.reply_html(
                f"<a href='tg://user?id={user_id}'>{user_id}</a> was not approved in this chat."
            )
            return
        await session.delete(entry)

    await message.reply_html(
        f"✗ <a href='tg://user?id={user_id}'>{user_id}</a> approval removed. "
        f"They are now subject to all automated filters."
    )


# ---------------------------------------------------------------------------
# /approved
# ---------------------------------------------------------------------------

@user_admin
async def list_approved(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all approved users in this group."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(ApprovedUser).where(ApprovedUser.chat_id == chat.id)
        )
        entries = result.scalars().all()

    if not entries:
        await message.reply_text("No approved users in this group.")
        return

    lines = []
    for entry in entries:
        try:
            member = await context.bot.get_chat_member(chat.id, entry.user_id)
            name = member.user.full_name or str(entry.user_id)
        except BadRequest:
            name = str(entry.user_id)
        lines.append(f"• <a href='tg://user?id={entry.user_id}'>{name}</a>")

    await message.reply_html(
        f"<b>Approved Users — {chat.title}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n<i>{len(entries)} user(s) total</i>"
    )


# ---------------------------------------------------------------------------
# /approval
# ---------------------------------------------------------------------------

async def check_approval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Check if a specific user is approved.

    Usage:
        /approval @username
        /approval <reply>
    """
    chat = update.effective_chat
    message = update.effective_message

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        # Default: check the command sender themselves.
        user = update.effective_user
        if user:
            user_id = user.id
        else:
            await message.reply_text("Specify a user.")
            return

    async with get_session() as session:
        result = await session.execute(
            select(ApprovedUser).where(
                ApprovedUser.chat_id == chat.id,
                ApprovedUser.user_id == user_id,
            )
        )
        entry = result.scalar_one_or_none()

    if entry:
        await message.reply_html(
            f"✅ <a href='tg://user?id={user_id}'>{user_id}</a> is <b>approved</b> in this group."
        )
    else:
        await message.reply_html(
            f"✗ <a href='tg://user?id={user_id}'>{user_id}</a> is <b>not approved</b> in this group."
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register approve/disapprove management commands."""
    application.add_handler(
        CommandHandler("approve", approve, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("disapprove", disapprove, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("approved", list_approved, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("approval", check_approval, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: approve")
