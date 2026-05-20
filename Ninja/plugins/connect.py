"""
plugins/connect.py — PM connection system.

Allows a group admin to manage their group(s) from a private chat with the
bot, without having to be physically in the group at the time.

Commands (in PM or group):
  /connect <chat_id|@username>  — Connect your PM session to a group.
  /disconnect                   — Disconnect the current PM session.
  /connected                    — Show the currently connected group.

When connected, the following commands work from PM as if typed in the group:
  /warn, /ban, /mute, /unmute, /kick, /pin, /promote, /demote, /rules,
  /setrules, /welcome, /notes, /filters — forwarded to the connected chat.

Implementation:
  • The connection is purely in-memory per bot session (persisted to a
    lightweight DB table so it survives restarts).
  • Only the user who owns the connection can use it.
  • The connection is scoped to a single chat at a time per user.
  • The bot must already be a member/admin of the target group.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from telegram import Chat, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from database.connect_models import UserConnection
from database.engine import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory cache: user_id → (chat_id, chat_title)
# ---------------------------------------------------------------------------
_cache: Dict[int, Tuple[int, str]] = {}


async def _load_cache() -> None:
    """Populate in-memory cache from DB on startup."""
    async with get_session() as session:
        rows = (await session.execute(select(UserConnection))).scalars().all()
        for row in rows:
            _cache[row.user_id] = (row.chat_id, row.chat_title)


async def _save_connection(user_id: int, chat_id: int, chat_title: str) -> None:
    """Upsert a connection record to the DB and cache."""
    _cache[user_id] = (chat_id, chat_title)
    async with get_session() as session:
        existing = (
            await session.execute(
                select(UserConnection).where(UserConnection.user_id == user_id)
            )
        ).scalar_one_or_none()
        if existing:
            existing.chat_id = chat_id
            existing.chat_title = chat_title
        else:
            session.add(UserConnection(user_id=user_id, chat_id=chat_id, chat_title=chat_title))
        await session.commit()


async def _remove_connection(user_id: int) -> bool:
    """Remove a connection.  Returns True if one existed."""
    existed = user_id in _cache
    _cache.pop(user_id, None)
    async with get_session() as session:
        row = (
            await session.execute(
                select(UserConnection).where(UserConnection.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row:
            await session.delete(row)
            await session.commit()
    return existed


def get_connected_chat(user_id: int) -> Optional[Tuple[int, str]]:
    """Return (chat_id, chat_title) for the user's active connection, or None."""
    return _cache.get(user_id)


# ---------------------------------------------------------------------------
# /connect <chat_id|@username>
# ---------------------------------------------------------------------------

async def cmd_connect(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Connect this PM to a group the user administers."""
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    if not context.args:
        await msg.reply_text(
            "Usage: <code>/connect &lt;chat_id or @username&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target = context.args[0].strip()

    # Resolve the chat
    try:
        chat: Chat = await context.bot.get_chat(target)
    except (BadRequest, TelegramError) as exc:
        await msg.reply_text(f"⚠️ Could not find that group: <code>{exc.message}</code>", parse_mode=ParseMode.HTML)
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await msg.reply_text("❌ You can only connect to groups or supergroups.")
        return

    # Verify the user is an admin in that group
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except (BadRequest, TelegramError) as exc:
        await msg.reply_text(f"⚠️ Could not verify your membership: <code>{exc.message}</code>", parse_mode=ParseMode.HTML)
        return

    if member.status not in ("administrator", "creator"):
        await msg.reply_text("❌ You must be an administrator of that group to connect to it.")
        return

    # Verify bot is in the group
    try:
        await context.bot.get_chat_member(chat.id, context.bot.id)
    except (BadRequest, Forbidden, TelegramError):
        await msg.reply_text("❌ I'm not a member of that group.  Add me first.")
        return

    await _save_connection(user.id, chat.id, chat.title or str(chat.id))

    await msg.reply_text(
        f"✅ Connected to <b>{chat.title}</b> (<code>{chat.id}</code>).\n\n"
        f"Admin commands you send here will be forwarded to that group.\n"
        f"Use /disconnect to end the session.",
        parse_mode=ParseMode.HTML,
    )
    logger.info("connect: user %d connected to chat %d", user.id, chat.id)


# ---------------------------------------------------------------------------
# /disconnect
# ---------------------------------------------------------------------------

async def cmd_disconnect(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Disconnect the current PM session from its group."""
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    existed = await _remove_connection(user.id)

    if existed:
        await msg.reply_text("🔌 Disconnected.  You are no longer managing any group from PM.")
    else:
        await msg.reply_text("You are not connected to any group.")


# ---------------------------------------------------------------------------
# /connected
# ---------------------------------------------------------------------------

async def cmd_connected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the currently active group connection."""
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    conn = get_connected_chat(user.id)
    if conn:
        chat_id, chat_title = conn
        await msg.reply_text(
            f"🔗 Connected to <b>{chat_title}</b> (<code>{chat_id}</code>).",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text(
            "You are not connected to any group.\n"
            "Use /connect &lt;chat_id&gt; to connect.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    # Load persisted connections on startup
    try:
        await _load_cache()
        logger.info("connect: loaded %d cached connections.", len(_cache))
    except Exception as exc:  # noqa: BLE001
        logger.warning("connect: could not load cache: %s", exc)

    pm_filter = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("connect",    cmd_connect,    filters=pm_filter))
    application.add_handler(CommandHandler("disconnect", cmd_disconnect, filters=pm_filter))
    application.add_handler(CommandHandler("connected",  cmd_connected,  filters=pm_filter))
    logger.info("connect plugin registered.")
