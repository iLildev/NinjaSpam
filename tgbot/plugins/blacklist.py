"""
plugins/blacklist.py — Automatic word/phrase deletion for blacklisted triggers.

Commands:
  /blacklist              — List all blacklisted words in this group.
  /addblacklist <words>   — Add one or more words (one per line).
  /unblacklist <words>    — Remove one or more words (one per line).
  /rmblacklist <words>    — Alias for /unblacklist.

Enforcement:
  A MessageHandler (group=11, edited_updates=True) checks every incoming and
  edited message (including sticker emoji and captions) against all blacklisted
  words for the chat.  Matching messages are deleted.  Admins are exempt.

Word-boundary regex: r"( |^|[^\w])" + re.escape(trigger) + r"( |$|[^\w])"
Matching is case-insensitive and respects word boundaries.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin, user_not_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import BlacklistEntry

logger = logging.getLogger(__name__)

BLACKLIST_GROUP: int = 11


def _word_boundary_pattern(trigger: str) -> re.Pattern[str]:
    """Compile a word-boundary case-insensitive regex for ``trigger``."""
    return re.compile(
        r"( |^|[^\w])" + re.escape(trigger) + r"( |$|[^\w])",
        re.IGNORECASE,
    )


async def _ensure_chat(session, chat_id: int, title: str = "") -> None:
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title))
        await session.flush()


# ---------------------------------------------------------------------------
# /blacklist
# ---------------------------------------------------------------------------

@user_admin
async def blacklist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    List all blacklisted words for this group.

    Pass ``copy`` as an argument to get the list in ``<code>`` format,
    making it easy to copy and paste.
    """
    chat = update.effective_chat
    message = update.effective_message
    copy_mode: bool = bool(context.args and context.args[0].lower() == "copy")

    async with get_session() as session:
        result = await session.execute(
            select(BlacklistEntry)
            .where(BlacklistEntry.chat_id == chat.id)
            .order_by(BlacklistEntry.trigger)
        )
        entries = result.scalars().all()

    if not entries:
        await message.reply_text("No words are blacklisted in this group.")
        return

    if copy_mode:
        lines: List[str] = [f"<code>{e.trigger}</code>" for e in entries]
    else:
        lines = [f"• {e.trigger}" for e in entries]

    header: str = "<b>Blacklisted Words:</b>\n"
    body: str = "\n".join(lines)
    full_text: str = header + body

    # Telegram max message length guard.
    if len(full_text) > 4096:
        full_text = full_text[:4090] + "\n…"

    await message.reply_text(full_text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /addblacklist
# ---------------------------------------------------------------------------

@user_admin
async def add_blacklist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Add one or more words to the blacklist.

    Each line in the message (after the command) is treated as a separate
    trigger.  Words are stored in lowercase.

    Usage:
        /addblacklist badword1
        badword2
        bad phrase
    """
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw:
        await message.reply_text(
            "Provide one or more words to blacklist (one per line)."
        )
        return

    new_triggers: List[str] = list(
        {t.strip().lower() for t in raw.splitlines() if t.strip()}
    )

    added: List[str] = []
    skipped: List[str] = []

    async with get_session() as session:
        await _ensure_chat(session, chat.id, chat.title or "")
        for trigger in new_triggers:
            existing = await session.execute(
                select(BlacklistEntry).where(
                    BlacklistEntry.chat_id == chat.id,
                    BlacklistEntry.trigger == trigger,
                )
            )
            if existing.scalar_one_or_none():
                skipped.append(trigger)
            else:
                session.add(BlacklistEntry(chat_id=chat.id, trigger=trigger))
                added.append(trigger)

    parts: List[str] = []
    if added:
        parts.append(f"Added {len(added)} word(s): " + ", ".join(f"<code>{w}</code>" for w in added))
    if skipped:
        parts.append(f"Already blacklisted: " + ", ".join(f"<code>{w}</code>" for w in skipped))

    await message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /unblacklist / /rmblacklist
# ---------------------------------------------------------------------------

@user_admin
async def remove_blacklist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Remove one or more words from the blacklist.

    Multi-line input removes each line as a separate trigger.

    Usage:
        /unblacklist badword1
        badword2
    """
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw:
        await message.reply_text(
            "Provide one or more words to unblacklist (one per line)."
        )
        return

    triggers: List[str] = list(
        {t.strip().lower() for t in raw.splitlines() if t.strip()}
    )

    removed: List[str] = []
    not_found: List[str] = []

    async with get_session() as session:
        for trigger in triggers:
            result = await session.execute(
                select(BlacklistEntry).where(
                    BlacklistEntry.chat_id == chat.id,
                    BlacklistEntry.trigger == trigger,
                )
            )
            entry = result.scalar_one_or_none()
            if entry:
                await session.delete(entry)
                removed.append(trigger)
            else:
                not_found.append(trigger)

    parts: List[str] = []
    if removed:
        parts.append(f"Removed: " + ", ".join(f"<code>{w}</code>" for w in removed))
    if not_found:
        parts.append(f"Not in blacklist: " + ", ".join(f"<code>{w}</code>" for w in not_found))

    await message.reply_text("\n".join(parts) or "Nothing changed.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Enforcement handler
# ---------------------------------------------------------------------------

@user_not_admin
async def delete_blacklisted(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete any message (including edits) that contains a blacklisted word.

    Skips messages from admins (enforced by @user_not_admin decorator).
    Also scans sticker emoji and captions, not just plain text.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not message:
        return

    text: Optional[str] = (
        message.text
        or message.caption
        or (message.sticker.emoji if message.sticker else None)
    )
    if not text:
        return

    async with get_session() as session:
        result = await session.execute(
            select(BlacklistEntry.trigger).where(BlacklistEntry.chat_id == chat.id)
        )
        triggers = [row[0] for row in result.all()]

    for trigger in triggers:
        if _word_boundary_pattern(trigger).search(text):
            try:
                await message.delete()
            except BadRequest as exc:
                if exc.message != "Message to delete not found":
                    logger.warning(
                        "Failed to delete blacklisted message in chat %s: %s",
                        chat.id,
                        exc.message,
                    )
            break  # Delete on first match; no need to check further.


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register blacklist management commands and enforcement handler."""
    application.add_handler(
        CommandHandler("blacklist", blacklist, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("addblacklist", add_blacklist, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(
            ["unblacklist", "rmblacklist"],
            remove_blacklist,
            filters=filters.ChatType.GROUPS,
        )
    )
    # Enforcement: group 11, also catches message edits.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & (filters.TEXT | filters.CAPTION | filters.Sticker.ALL | filters.PHOTO),
            delete_blacklisted,
        ),
        group=BLACKLIST_GROUP,
    )
    logger.info("Plugin loaded: blacklist")
