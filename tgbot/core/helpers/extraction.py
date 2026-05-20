"""
core/helpers/extraction.py — Async user and text extraction from messages.

These helpers are called by every moderation command handler to resolve the
target user from whatever form the invoking admin provided:
  1. Text mention entity (linked @name in message body)
  2. @username argument
  3. Numeric user_id argument
  4. Reply-to message (fallback)

Username → user_id lookups use the local database so the bot can resolve
handles for users it has previously seen, without requiring a live API call.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from sqlalchemy import select
from telegram import Message, MessageEntity, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from database.engine import get_session
from database.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _user_id_from_reply(
    message: Message,
) -> Tuple[Optional[int], str]:
    """
    Extract user_id and trailing text from a reply-to message.

    Returns ``(None, "")`` when the message has no reply.
    """
    prev = message.reply_to_message
    if not prev or not prev.from_user:
        return None, ""
    parts = (message.text or "").split(None, 1)
    text: str = parts[1].strip() if len(parts) > 1 else ""
    return prev.from_user.id, text


async def _resolve_username(username: str) -> Optional[int]:
    """
    Resolve a Telegram @username to a user_id using the local User table.

    Returns None when the username is not found in the database — the caller
    should then ask the admin to reply to the user's message instead.
    """
    clean = username.lstrip("@").lower()
    async with get_session() as session:
        result = await session.execute(
            select(User.id).where(User.username.ilike(clean)).limit(1)
        )
        row = result.scalar_one_or_none()
    return row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_user_and_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Tuple[Optional[int], str]:
    """
    Return ``(user_id, remaining_text)`` from the current message.

    Resolution priority (mirrors Marie's original behaviour):
    1. Text-mention entity at the start of the argument section.
    2. First argument is an @username  → DB lookup.
    3. First argument is a numeric ID  → use directly.
    4. No argument but message is a reply → use reply sender.

    ``remaining_text`` is the command text after the user reference has been
    consumed (used as the reason or parameter for the action being performed).

    Returns ``(None, "")`` when no user can be resolved.
    """
    message: Message = update.effective_message
    if not message:
        return None, ""

    raw_text: str = message.text or message.caption or ""
    # Split off the command prefix and grab everything after it.
    split_text = raw_text.split(None, 1)
    text_to_parse: str = split_text[1].strip() if len(split_text) > 1 else ""

    # --- Priority 1: text_mention entity (linked mention with no @) ---
    entities = list(message.parse_entities([MessageEntity.TEXT_MENTION]))
    if entities:
        ent = entities[0]
        # The entity must begin at the same offset as our text_to_parse starts
        # inside the full message text to be the intended target argument.
        arg_offset: int = len(raw_text) - len(text_to_parse)
        if ent.offset == arg_offset:
            user_id: int = ent.user.id
            remaining: str = raw_text[ent.offset + ent.length:].strip()
            return user_id, remaining

    args = context.args or []

    # --- Priority 2: @username argument ---
    if args and args[0].startswith("@"):
        resolved = await _resolve_username(args[0])
        if resolved is None:
            await message.reply_text(
                "I don't have that user in my database. "
                "Reply to one of their messages so I can identify them."
            )
            return None, ""
        remaining_args = args[1:]
        return resolved, " ".join(remaining_args)

    # --- Priority 3: numeric user_id argument ---
    if args and args[0].isdigit():
        user_id = int(args[0])
        # Verify the ID is reachable via Telegram API.
        try:
            await context.bot.get_chat(user_id)
        except BadRequest as exc:
            if exc.message in ("User_id_invalid", "Chat not found"):
                await message.reply_text(
                    "I haven't interacted with that user before. "
                    "Forward a message from them and try again."
                )
            else:
                logger.exception("Unexpected error resolving user_id %s: %s", user_id, exc)
            return None, ""
        remaining_args = args[1:]
        return user_id, " ".join(remaining_args)

    # --- Priority 4: reply-to message ---
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
        # Any text after the command counts as the reason.
        return user_id, text_to_parse

    return None, ""


async def extract_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[int]:
    """
    Convenience wrapper that returns only the user_id, discarding the text.

    Use this for commands that need a target user but don't accept a reason
    (e.g. /unmute, /unban).
    """
    user_id, _ = await extract_user_and_text(update, context)
    return user_id


def extract_text(message: Message) -> Optional[str]:
    """
    Return the most appropriate text representation of a message for keyword
    matching (used by blacklist, filter, and warn-filter handlers).

    Checks message text, caption, and sticker emoji in that order.
    """
    return (
        message.text
        or message.caption
        or (message.sticker.emoji if message.sticker else None)
    )
