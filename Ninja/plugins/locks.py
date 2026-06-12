"""
plugins/locks.py — Per-chat content type locking and restriction system.

Commands:
  /lock <type>      — Lock a specific message type (non-admins deleted).
  /unlock <type>    — Unlock a message type.
  /locks            — Show the lock status of all types.
  /locktypes        — List all lockable type names.

Lock types (14): sticker, audio, voice, document, video, videonote, contact,
                  photo, gif, url, bots, forward, game, location.

Restriction types (4): messages (all text+cmds), media, other, all.

Special cases:
- 'bots' lock: kicks newly added bots on join instead of deleting messages.
- 'url' lock: scans both text entities and caption entities.
- 'gif' lock: covers animations (Filters.ANIMATION).

Enforcement runs in handler groups 1 (locks) and 2 (restrictions).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select
from telegram import Chat, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import LockSettings, RestrictionSettings

logger = logging.getLogger(__name__)

LOCK_GROUP: int = 1
RESTRICTION_GROUP: int = 2

# ---------------------------------------------------------------------------
# Allowed type names
# ---------------------------------------------------------------------------

LOCK_TYPES: List[str] = [
    "sticker", "audio", "voice", "document", "video", "videonote",
    "contact", "photo", "gif", "url", "bots", "forward", "game", "location",
]

RESTRICTION_TYPES: List[str] = ["messages", "media", "other", "all"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_or_create_lock_settings(session, chat_id: int) -> LockSettings:
    settings = await session.get(LockSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        settings = LockSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


async def _get_or_create_restr_settings(session, chat_id: int) -> RestrictionSettings:
    settings = await session.get(RestrictionSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        settings = RestrictionSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


# ---------------------------------------------------------------------------
# /lock
# ---------------------------------------------------------------------------

@user_admin
async def lock(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Lock a content type so non-admins cannot send it.

    Usage:
        /lock sticker
        /lock messages
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text(
            "Specify a type to lock. Use /locktypes to see the list."
        )
        return

    lock_type: str = context.args[0].lower()

    if lock_type in LOCK_TYPES:
        async with get_session() as session:
            settings = await _get_or_create_lock_settings(session, chat.id)
            if not getattr(settings, lock_type, None):
                setattr(settings, lock_type, True)
        await message.reply_text(
            f"Locked <b>{lock_type}</b>. I will delete matching messages from non-admins.",
            parse_mode=ParseMode.HTML,
        )
    elif lock_type in RESTRICTION_TYPES:
        async with get_session() as session:
            settings = await _get_or_create_restr_settings(session, chat.id)
            if lock_type == "all":
                settings.messages = True
                settings.media = True
                settings.other = True
                settings.preview = True
            else:
                setattr(settings, lock_type if lock_type != "previews" else "preview", True)
        await message.reply_text(
            f"Locked restriction <b>{lock_type}</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"Unknown type <code>{lock_type}</code>. Use /locktypes.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# /unlock
# ---------------------------------------------------------------------------

@user_admin
async def unlock(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Remove a content lock.

    Usage:
        /unlock sticker
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Specify a type to unlock.")
        return

    lock_type: str = context.args[0].lower()

    if lock_type in LOCK_TYPES:
        async with get_session() as session:
            settings = await session.get(LockSettings, chat.id)
            if settings and getattr(settings, lock_type, False):
                setattr(settings, lock_type, False)
        await message.reply_text(
            f"Unlocked <b>{lock_type}</b>.", parse_mode=ParseMode.HTML
        )
    elif lock_type in RESTRICTION_TYPES:
        async with get_session() as session:
            settings = await session.get(RestrictionSettings, chat.id)
            if settings:
                if lock_type == "all":
                    settings.messages = False
                    settings.media = False
                    settings.other = False
                    settings.preview = False
                else:
                    setattr(settings, lock_type if lock_type != "previews" else "preview", False)
        await message.reply_text(
            f"Unlocked restriction <b>{lock_type}</b>.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"Unknown type <code>{lock_type}</code>.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# /locks
# ---------------------------------------------------------------------------

async def show_locks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Display the current lock and restriction state for this group."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        lock_settings = await session.get(LockSettings, chat.id)
        restr_settings = await session.get(RestrictionSettings, chat.id)

    def _icon(val: bool) -> str:
        return "🔒" if val else "🔓"

    lines: List[str] = [
        f"<b>Lock Status — {chat.title}</b>\n"
        f"━━━━━━━━━━━━━━━\n",
        "<b>Content Locks:</b>"
    ]
    for lt in LOCK_TYPES:
        val: bool = getattr(lock_settings, lt, False) if lock_settings else False
        lines.append(f"  {_icon(val)} {lt}")

    lines.append("\n<b>Restrictions:</b>")
    for rt in ["messages", "media", "other"]:
        val = getattr(restr_settings, rt, False) if restr_settings else False
        lines.append(f"  {_icon(val)} {rt}")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /locktypes
# ---------------------------------------------------------------------------

async def lock_types(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all available lock type names."""
    message = update.effective_message
    all_types = LOCK_TYPES + RESTRICTION_TYPES
    await message.reply_text(
        "<b>Lockable types:</b>\n" + ", ".join(f"<code>{t}</code>" for t in all_types),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Lock enforcement handler
# ---------------------------------------------------------------------------

async def enforce_locks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete messages that violate an active content lock.

    Runs in group 1, checks every message type.  Admins are exempt.
    The 'bots' lock is handled separately (joins handler below).
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user or not chat or not message:
        return

    if await is_user_admin(chat, user.id):
        return  # Admins are always exempt.

    async with get_session() as session:
        settings = await session.get(LockSettings, chat.id)

    if not settings:
        return

    should_delete: bool = False

    if settings.sticker and message.sticker:
        should_delete = True
    elif settings.photo and message.photo:
        should_delete = True
    elif settings.audio and message.audio:
        should_delete = True
    elif settings.voice and message.voice:
        should_delete = True
    elif settings.document and message.document and not message.animation:
        should_delete = True
    elif settings.video and message.video:
        should_delete = True
    elif settings.videonote and message.video_note:
        should_delete = True
    elif settings.contact and message.contact:
        should_delete = True
    elif settings.gif and message.animation:
        should_delete = True
    elif settings.game and message.game:
        should_delete = True
    elif settings.location and message.location:
        should_delete = True
    elif settings.forward and (message.forward_from or message.forward_from_chat):
        should_delete = True
    elif settings.url:
        from telegram import MessageEntity
        entities = message.entities or message.caption_entities or []
        if any(e.type == MessageEntity.URL for e in entities):
            should_delete = True

    if should_delete:
        try:
            await message.delete()
        except BadRequest as exc:
            if exc.message != "Message to delete not found":
                logger.warning("Lock delete failed: %s", exc.message)


# ---------------------------------------------------------------------------
# Bots lock: kick newly added bots on join
# ---------------------------------------------------------------------------

async def enforce_bots_lock(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    When the 'bots' lock is active, kick any bot that joins the group.

    This is implemented as a StatusUpdate handler separate from enforce_locks
    because it operates on a chat_member event rather than a message event.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not message or not message.new_chat_members:
        return

    async with get_session() as session:
        settings = await session.get(LockSettings, chat.id)

    if not settings or not settings.bots:
        return

    for new_member in message.new_chat_members:
        if new_member.is_bot and new_member.id != context.bot.id:
            try:
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=new_member.id)
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=new_member.id)
            except BadRequest as exc:
                logger.warning(
                    "Bots lock: could not kick bot %s from chat %s: %s",
                    new_member.id,
                    chat.id,
                    exc.message,
                )


# ---------------------------------------------------------------------------
# Restriction enforcement handler
# ---------------------------------------------------------------------------

async def enforce_restrictions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete messages that violate broad restriction categories.

    Runs in group 2.  Admins are exempt.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user or not chat or not message:
        return

    if await is_user_admin(chat, user.id):
        return

    async with get_session() as session:
        settings = await session.get(RestrictionSettings, chat.id)

    if not settings:
        return

    should_delete: bool = False

    if settings.messages and (message.text or message.caption):
        should_delete = True
    elif settings.media and (
        message.audio
        or message.document
        or message.video
        or message.video_note
        or message.voice
        or message.photo
    ):
        should_delete = True
    elif settings.other and (
        message.game or message.sticker or message.animation
    ):
        should_delete = True

    if should_delete:
        try:
            await message.delete()
        except BadRequest as exc:
            if exc.message != "Message to delete not found":
                logger.warning("Restriction delete failed: %s", exc.message)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register lock/restriction management commands and enforcement handlers."""
    application.add_handler(
        CommandHandler("lock", lock, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unlock", unlock, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("locks", show_locks, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("locktypes", lock_types, filters=filters.ChatType.GROUPS)
    )

    # Content lock enforcement — group 1.
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS, enforce_locks),
        group=LOCK_GROUP,
    )

    # Bot-join lock enforcement.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            enforce_bots_lock,
        ),
        group=LOCK_GROUP,
    )

    # Restriction enforcement — group 2.
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS, enforce_restrictions),
        group=RESTRICTION_GROUP,
    )

    logger.info("Plugin loaded: locks")
