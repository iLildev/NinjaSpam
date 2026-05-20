"""
plugins/users_tracking.py — Passive user + chat tracker.

Every message sent in a group is silently checked and the sender's
username and the chat title are kept up-to-date in the database.
This powers username lookups (``/info @username`` without needing to
have received a message from that user in the current chat) and the
``/chatlist`` owner command.

Commands:
  /chatlist — (sudo/owner only) Send a text file listing every known chat.
  /forget   — (admin) Erase all stored data about a user from this group (GroupHelp).
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from sqlalchemy import select
from telegram import Chat, Message, Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from sqlalchemy import delete as sa_delete
from telegram.constants import ParseMode

from config import settings as cfg
from core.helpers.chat_status import user_admin
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models import Chat as ChatModel, User as UserModel

log = logging.getLogger(__name__)

_TRACKING_GROUP = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _upsert_user(session, user_id: int, username: Optional[str]) -> None:
    """Insert or update a User row."""
    row = await session.get(UserModel, user_id)
    if row is None:
        session.add(UserModel(
            id=user_id,
            first_name="",
            username=username,
        ))
    else:
        if username is not None and row.username != username:
            row.username = username


async def _upsert_chat(session, chat_id: int, title: str) -> None:
    """Insert or update a Chat row."""
    row = await session.get(ChatModel, chat_id)
    if row is None:
        session.add(ChatModel(id=chat_id, title=title))
    else:
        if row.title != title:
            row.title = title


# ---------------------------------------------------------------------------
# Passive tracking handler
# ---------------------------------------------------------------------------

async def log_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Keep the DB up-to-date with each message sender and the current chat.

    Runs silently in handler group 4 — never sends any reply.
    """
    chat: Optional[Chat] = update.effective_chat
    msg: Optional[Message] = update.effective_message
    if not chat or not msg:
        return

    try:
        async with get_session() as session:
            # Track the sender.
            if msg.from_user:
                await _upsert_user(
                    session,
                    msg.from_user.id,
                    msg.from_user.username,
                )

            # Track the chat.
            if chat.type in (Chat.GROUP, Chat.SUPERGROUP):
                await _upsert_chat(session, chat.id, chat.title or "")

            # Also track forward origin if present.
            if msg.forward_from:
                await _upsert_user(
                    session,
                    msg.forward_from.id,
                    msg.forward_from.username,
                )

            # Track reply target.
            if msg.reply_to_message and msg.reply_to_message.from_user:
                ru = msg.reply_to_message.from_user
                await _upsert_user(session, ru.id, ru.username)
    except Exception as exc:
        log.debug("users_tracking: error in log_user: %s", exc)


# ---------------------------------------------------------------------------
# /chatlist command
# ---------------------------------------------------------------------------

async def chatlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send a text file listing all known chats (owner/sudo only)."""
    msg = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(ChatModel).order_by(ChatModel.title)
        )
        chats = result.scalars().all()

    if not chats:
        await msg.reply_text("No chats in the database yet.")
        return

    lines = ["Chat List\n", "=" * 40 + "\n"]
    for chat in chats:
        lines.append(f"{chat.title or 'Unknown'} — {chat.id}\n")
    lines.append(f"\nTotal: {len(chats)} chats\n")

    output = BytesIO("".join(lines).encode("utf-8"))
    output.name = "chatlist.txt"
    await msg.reply_document(document=output, caption=f"📋 {len(chats)} known chats.")


# ---------------------------------------------------------------------------
# /forget  (GroupHelp feature — erase a user's data from this group)
# ---------------------------------------------------------------------------

@user_admin
async def forget(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Erase all bot-stored data about a user within this group.

    Removes the user's activity statistics, warnings, notes, and any
    other group-scoped records from the database.  The global User row
    (username/ID mapping) is kept so other groups' data remains intact.

    Usage:
        /forget @username
        /forget (as reply)
    """
    message = update.effective_message
    chat = update.effective_chat

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Reply to a message or provide a username/ID to forget.\n"
            "Usage: /forget @username"
        )
        return

    # Protect against forgetting the bot itself.
    if user_id == context.bot.id:
        await message.reply_text("I can't forget myself.")
        return

    deleted_tables: list[str] = []

    async with get_session() as session:
        # Import all group-scoped models that hold per-user data.
        try:
            from database.models_extra import UserDailyStat, UserWarning
            r = await session.execute(
                sa_delete(UserDailyStat).where(
                    UserDailyStat.chat_id == chat.id,
                    UserDailyStat.user_id == user_id,
                )
            )
            if r.rowcount:
                deleted_tables.append(f"activity stats ({r.rowcount} rows)")
        except Exception:
            pass

        try:
            from database.models_extra import UserWarning
            r = await session.execute(
                sa_delete(UserWarning).where(
                    UserWarning.chat_id == chat.id,
                    UserWarning.user_id == user_id,
                )
            )
            if r.rowcount:
                deleted_tables.append(f"warnings ({r.rowcount})")
        except Exception:
            pass

        try:
            from database.models_extra import UserNote
            r = await session.execute(
                sa_delete(UserNote).where(
                    UserNote.chat_id == chat.id,
                    UserNote.user_id == user_id,
                )
            )
            if r.rowcount:
                deleted_tables.append(f"notes ({r.rowcount})")
        except Exception:
            pass

        try:
            from database.models_extra import MutedUser
            r = await session.execute(
                sa_delete(MutedUser).where(
                    MutedUser.chat_id == chat.id,
                    MutedUser.user_id == user_id,
                )
            )
            if r.rowcount:
                deleted_tables.append(f"mute records ({r.rowcount})")
        except Exception:
            pass

        await session.commit()

    if deleted_tables:
        summary = ", ".join(deleted_tables)
        await message.reply_html(
            f"🗑 Forgotten <a href='tg://user?id={user_id}'>{user_id}</a>'s data "
            f"from this group.\n<i>Removed: {summary}</i>"
        )
    else:
        await message.reply_html(
            f"ℹ️ No stored data found for <a href='tg://user?id={user_id}'>{user_id}</a> "
            f"in this group."
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register tracking handler and chatlist command."""
    # Silent tracker — runs in group 4 on ALL group messages.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL,
            log_user,
        ),
        group=_TRACKING_GROUP,
    )

    # /chatlist — restricted to owners.
    application.add_handler(
        CommandHandler(
            "chatlist",
            chatlist,
            filters=filters.User(cfg.OWNER_IDS),
        )
    )

    # /forget — group admins only.
    application.add_handler(
        CommandHandler("forget", forget, filters=filters.ChatType.GROUPS)
    )

    log.info("Plugin loaded: users_tracking")
