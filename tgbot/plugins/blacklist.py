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
  Users with an active /permit are also exempt.

Unicode obfuscation detection (mlt-melt inspired):
  Before matching, the message text is normalised through _normalise() which:
  - NFKD-decomposes font variants (ⓐ→a, 𝐛→b, ｃ→c, etc.)
  - Strips combining diacritical marks (é→e, ñ→n)
  - Maps Cyrillic lookalikes to Latin (с→c, о→o, р→r, etc.)
  - Replaces common symbol substitutions ($→s, @→a, 0→o, 3→e, …)
  Both the original AND the normalised text are checked, so plain matches
  still work while obfuscated bypasses like "$p@m" or "сrypto" are caught.

Word-boundary regex: r"( |^|[^\w])" + re.escape(trigger) + r"( |$|[^\w])"
Matching is case-insensitive and respects word boundaries.
"""

from __future__ import annotations

import logging
import re
import unicodedata
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

# ---------------------------------------------------------------------------
# Unicode obfuscation normalisation (T001 — mlt-melt inspired)
# ---------------------------------------------------------------------------

# Cyrillic → Latin visual-lookalike substitutions
_CYRILLIC_TO_LATIN: dict = {
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'r', 'с': 'c', 'х': 'x',
    'у': 'y', 'ѕ': 's', 'і': 'i', 'ї': 'i', 'ј': 'j', 'ѵ': 'v',
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H',
    'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X', 'Ѕ': 'S',
    'І': 'I', 'Ј': 'J', 'ё': 'e', 'Ё': 'E',
}

# Common symbol substitutions used to evade keyword filters
_SYMBOL_SUBS: dict = {
    '$': 's', '@': 'a', '0': 'o', '1': 'l',
    '3': 'e', '4': 'a', '5': 's', '6': 'b',
    '7': 't', '8': 'b', '!': 'i', '|': 'l',
    '+': 't', '€': 'e', '£': 'l',
}

_CYR_TABLE = str.maketrans(_CYRILLIC_TO_LATIN)
_SYM_TABLE = str.maketrans(_SYMBOL_SUBS)


def _normalise(text: str) -> str:
    """
    Normalise text to defeat common Unicode obfuscation techniques.

    1. NFKD decomposition — strips font variants (ⓐ→a, ｂ→b, 𝐜→c …)
    2. Strip combining diacritics (Mn category) — é→e, ñ→n
    3. Map Cyrillic lookalikes → Latin
    4. Map common symbol substitutions ($→s, @→a …)
    5. Lowercase
    """
    # Step 1 + 2: decompose + strip diacritics
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    # Step 3: Cyrillic → Latin
    text = text.translate(_CYR_TABLE)
    # Step 4: symbol subs
    text = text.translate(_SYM_TABLE)
    # Step 5: lowercase
    return text.lower()


# ---------------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------------

def _word_boundary_pattern(trigger: str) -> re.Pattern[str]:
    """Compile a word-boundary case-insensitive regex for ``trigger``."""
    return re.compile(
        r"( |^|[^\w])" + re.escape(trigger) + r"( |$|[^\w])",
        re.IGNORECASE,
    )


def _text_matches_trigger(text: str, trigger: str) -> bool:
    """
    Return True if text contains trigger — checks both raw and normalised forms.

    1. Direct word-boundary match (original text)
    2. Word-boundary match on normalised text vs normalised trigger
    """
    if _word_boundary_pattern(trigger).search(text):
        return True
    norm_text = _normalise(text)
    norm_trigger = _normalise(trigger)
    if norm_trigger and _word_boundary_pattern(norm_trigger).search(norm_text):
        return True
    return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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
    """List all blacklisted words for this group."""
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

    header: str = "<b>🚫 Blacklisted Words:</b>\n"
    body: str = "\n".join(lines)
    full_text: str = header + body

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
    """Add one or more words to the blacklist (one per line)."""
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
        parts.append("Added " + str(len(added)) + " word(s): " + ", ".join(f"<code>{w}</code>" for w in added))
    if skipped:
        parts.append("Already blacklisted: " + ", ".join(f"<code>{w}</code>" for w in skipped))

    await message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /unblacklist / /rmblacklist
# ---------------------------------------------------------------------------

@user_admin
async def remove_blacklist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove one or more words from the blacklist (one per line)."""
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
        parts.append("Removed: " + ", ".join(f"<code>{w}</code>" for w in removed))
    if not_found:
        parts.append("Not in blacklist: " + ", ".join(f"<code>{w}</code>" for w in not_found))

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

    Skips messages from admins (@user_not_admin decorator).
    Also checks sticker emoji and captions, not just plain text.
    Checks both raw text AND Unicode-normalised text to catch obfuscation.
    Users with an active /permit are exempt.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    # Check temporary permit
    try:
        from plugins.permit import is_permitted
        if await is_permitted(chat.id, user.id, consume=False):
            return
    except Exception:
        pass

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
        if _text_matches_trigger(text, trigger):
            try:
                await message.delete()
            except BadRequest as exc:
                if exc.message != "Message to delete not found":
                    logger.warning(
                        "Failed to delete blacklisted message in chat %s: %s",
                        chat.id,
                        exc.message,
                    )
            break


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
