"""
plugins/filters.py — Custom keyword-to-reply filter system.

Commands:
  /filter <keyword> <reply>  — Add a keyword → reply mapping.
  /stop <keyword>            — Remove a filter.
  /filters                   — List all active filters for this group.

Enforcement:
  A MessageHandler (group=10) checks every message for keyword matches
  (word-boundary, case-insensitive) and sends the associated reply.
  Admins are NOT exempt from triggering filters (only warn/blacklist exempt them).

The in-memory filter cache (CHAT_FILTERS) maps chat_id → sorted list of
keywords for O(1) chat access and O(n_keywords) scan per message.
Keywords are stored lowercase.

Inline button syntax in filter replies:
  [Label](buttonurl:https://example.com)
  [Same Row](buttonurl:https://example.com:same)
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from core.helpers.string_handling import button_markdown_parser
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import CustomFilter, FilterButton, NoteType

logger = logging.getLogger(__name__)

FILTER_GROUP: int = 10

# ---------------------------------------------------------------------------
# In-memory cache: {chat_id: [keyword, ...]} (sorted longest-first)
# ---------------------------------------------------------------------------
CHAT_FILTERS: Dict[int, List[str]] = {}


def _word_boundary_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(
        r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _ensure_chat(session, chat_id: int, title: str = "") -> None:
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title))
        await session.flush()


def _get_note_type_from_message(
    message: Message,
    args_text: str,
) -> Tuple[NoteType, str, Optional[str], List[Tuple[str, str, bool]]]:
    """Determine content type for a filter reply from the save message."""
    reply = message.reply_to_message

    if args_text:
        content, btns = button_markdown_parser(args_text)
        msg_type = NoteType.BUTTON_TEXT if btns else NoteType.TEXT
        return msg_type, content, None, btns

    if not reply:
        return NoteType.TEXT, "", None, []

    if reply.sticker:
        return NoteType.STICKER, "", reply.sticker.file_id, []
    if reply.document:
        return NoteType.DOCUMENT, reply.caption or "", reply.document.file_id, []
    if reply.photo:
        return NoteType.PHOTO, reply.caption or "", reply.photo[-1].file_id, []
    if reply.audio:
        return NoteType.AUDIO, reply.caption or "", reply.audio.file_id, []
    if reply.voice:
        return NoteType.VOICE, reply.caption or "", reply.voice.file_id, []
    if reply.video:
        return NoteType.VIDEO, reply.caption or "", reply.video.file_id, []

    text: str = reply.text or reply.caption or ""
    content, btns = button_markdown_parser(text)
    msg_type = NoteType.BUTTON_TEXT if btns else NoteType.TEXT
    return msg_type, content, None, btns


# ---------------------------------------------------------------------------
# Send filter reply
# ---------------------------------------------------------------------------

async def _send_filter_reply(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reply_to_id: Optional[int],
    filt: CustomFilter,
) -> None:
    """Send the filter reply content (text or media) to the chat."""
    keyboard: Optional[InlineKeyboardMarkup] = None
    if filt.has_buttons:
        rows: List[List[InlineKeyboardButton]] = []
        current_row: List[InlineKeyboardButton] = []
        for btn in filt.buttons:
            ib = InlineKeyboardButton(text=btn.button_name, url=btn.url)
            if btn.same_line and current_row:
                current_row.append(ib)
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [ib]
        if current_row:
            rows.append(current_row)
        keyboard = InlineKeyboardMarkup(rows)

    try:
        msg_type = NoteType(filt.msg_type)
    except ValueError:
        msg_type = NoteType.TEXT

    parse_mode = ParseMode.MARKDOWN

    try:
        if msg_type in (NoteType.TEXT, NoteType.BUTTON_TEXT):
            await context.bot.send_message(
                chat_id=chat_id,
                text=filt.content or filt.keyword,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                disable_web_page_preview=True,
            )
        elif msg_type == NoteType.STICKER:
            await context.bot.send_sticker(
                chat_id=chat_id,
                sticker=filt.file_id,
                reply_to_message_id=reply_to_id,
            )
        elif msg_type == NoteType.DOCUMENT:
            await context.bot.send_document(
                chat_id=chat_id,
                document=filt.file_id,
                caption=filt.content or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
            )
        elif msg_type == NoteType.PHOTO:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=filt.file_id,
                caption=filt.content or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
            )
        elif msg_type == NoteType.AUDIO:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=filt.file_id,
                caption=filt.content or None,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_id,
            )
        elif msg_type == NoteType.VOICE:
            await context.bot.send_voice(
                chat_id=chat_id,
                voice=filt.file_id,
                caption=filt.content or None,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_id,
            )
        elif msg_type == NoteType.VIDEO:
            await context.bot.send_video(
                chat_id=chat_id,
                video=filt.file_id,
                caption=filt.content or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
            )
    except BadRequest as exc:
        logger.warning("Filter reply failed for '%s': %s", filt.keyword, exc.message)
        # Fallback to plain text.
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=filt.content or filt.keyword,
                reply_to_message_id=reply_to_id,
            )
        except BadRequest:
            pass


# ---------------------------------------------------------------------------
# /filter <keyword> <reply>
# ---------------------------------------------------------------------------

@user_admin
async def add_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Add a keyword → reply filter.

    Usage:
        /filter <keyword> <reply text>
        /filter <keyword>   (reply to a media message)
    """
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()

    if not raw:
        await message.reply_text(
            "Usage: /filter <keyword> <reply>\n"
            "Or reply to a media message with /filter <keyword>"
        )
        return

    parts = raw.split(None, 1)
    keyword: str = parts[0].lower()
    args_text: str = parts[1].strip() if len(parts) > 1 else ""

    msg_type, content, file_id, buttons = _get_note_type_from_message(message, args_text)

    # Guard: buttons-only reply (no message body) is not allowed.
    if not content and not file_id and not buttons:
        await message.reply_text(
            "A filter reply must have at least some content (text or media)."
        )
        return

    async with get_session() as session:
        await _ensure_chat(session, chat.id, chat.title or "")

        # Remove existing filter for the same keyword.
        await session.execute(
            delete(CustomFilter).where(
                CustomFilter.chat_id == chat.id,
                CustomFilter.keyword == keyword,
            )
        )
        await session.flush()

        has_buttons: bool = bool(buttons)
        filt = CustomFilter(
            chat_id=chat.id,
            keyword=keyword,
            content=content,
            file_id=file_id,
            has_buttons=has_buttons,
            msg_type=msg_type.value,
        )
        session.add(filt)
        await session.flush()

        for btn_name, url, same_line in buttons:
            session.add(FilterButton(
                filter_id=filt.id,
                button_name=btn_name,
                url=url,
                same_line=same_line,
            ))

    # Update in-memory cache.
    triggers = CHAT_FILTERS.setdefault(chat.id, [])
    if keyword not in triggers:
        triggers.append(keyword)
    CHAT_FILTERS[chat.id] = sorted(triggers, key=lambda k: (-len(k), k))

    await message.reply_text(
        f"Filter added: <code>{keyword}</code>", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /stop <keyword>
# ---------------------------------------------------------------------------

@user_admin
async def stop_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove a keyword filter."""
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw:
        await message.reply_text("Specify the keyword to remove.")
        return

    keyword: str = raw.lower()

    async with get_session() as session:
        result = await session.execute(
            delete(CustomFilter).where(
                CustomFilter.chat_id == chat.id,
                CustomFilter.keyword == keyword,
            )
        )
        removed: bool = result.rowcount > 0

    if removed:
        if chat.id in CHAT_FILTERS and keyword in CHAT_FILTERS[chat.id]:
            CHAT_FILTERS[chat.id].remove(keyword)
        await message.reply_text(
            f"Filter removed: <code>{keyword}</code>", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"No filter for <code>{keyword}</code>.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# /filters
# ---------------------------------------------------------------------------

async def list_filters(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all active keyword filters for this group."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(CustomFilter.keyword)
            .where(CustomFilter.chat_id == chat.id)
            .order_by(CustomFilter.keyword)
        )
        keywords: List[str] = [row[0] for row in result.all()]

    if not keywords:
        await message.reply_text("No filters are configured for this group.")
        return

    body: str = "\n".join(f"• <code>{k}</code>" for k in keywords)
    full: str = f"<b>Active Filters in {chat.title}:</b>\n{body}"

    if len(full) > 4096:
        full = full[:4090] + "\n…"

    await message.reply_text(full, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Filter enforcement handler
# ---------------------------------------------------------------------------

async def check_filters(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Scan each incoming message against the active filter keywords.

    Fires the first matching filter and stops (no double-triggering).
    Loads FilterButton rows from DB on match to build the keyboard.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not chat or not message:
        return

    triggers = CHAT_FILTERS.get(chat.id)
    if not triggers:
        return

    text: str = message.text or message.caption or ""
    if not text:
        return

    for keyword in triggers:
        if not _word_boundary_pattern(keyword).search(text):
            continue

        async with get_session() as session:
            result = await session.execute(
                select(CustomFilter).where(
                    CustomFilter.chat_id == chat.id,
                    CustomFilter.keyword == keyword,
                )
            )
            filt: Optional[CustomFilter] = result.scalar_one_or_none()
            if not filt:
                continue
            if filt.has_buttons:
                btn_result = await session.execute(
                    select(FilterButton)
                    .where(FilterButton.filter_id == filt.id)
                    .order_by(FilterButton.id)
                )
                filt.buttons = list(btn_result.scalars().all())
            else:
                filt.buttons = []

        reply_to: Optional[int] = message.message_id
        await _send_filter_reply(context, chat.id, reply_to, filt)
        break  # First match wins.


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Load filter cache from DB and register all filter handlers."""
    async with get_session() as session:
        result = await session.execute(
            select(CustomFilter.chat_id, CustomFilter.keyword)
        )
        for chat_id, keyword in result.all():
            if chat_id not in CHAT_FILTERS:
                CHAT_FILTERS[chat_id] = []
            if keyword not in CHAT_FILTERS[chat_id]:
                CHAT_FILTERS[chat_id].append(keyword)

    for cid in CHAT_FILTERS:
        CHAT_FILTERS[cid] = sorted(CHAT_FILTERS[cid], key=lambda k: (-len(k), k))

    application.add_handler(
        CommandHandler("filter", add_filter, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("stop", stop_filter, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("filters", list_filters, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
            check_filters,
        ),
        group=FILTER_GROUP,
    )
    logger.info("Plugin loaded: filters (cache: %d chats)", len(CHAT_FILTERS))
