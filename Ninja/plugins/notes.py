"""
plugins/notes.py — Saved notes system with full media type support.

Commands:
  /save <name> [content]  — Save a note (or reply to a message to save it).
  /get  <name> [noformat] — Retrieve a note. Pass 'noformat' for raw text.
  #<name>                 — Hashtag shortcut to retrieve a note.
  /notes / /saved         — List all notes saved in this group.
  /clear <name>           — Delete a note.

Supported note types: TEXT, BUTTON_TEXT, STICKER, DOCUMENT, PHOTO,
                       AUDIO, VOICE, VIDEO.

Inline keyboard button syntax in note content:
  [Button Label](buttonurl:https://example.com)
  [Same Row](buttonurl:https://example.com:same)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from sqlalchemy import delete, select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
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
from database.models_extra import NoteButton, NoteType, Note

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: determine note type from a message
# ---------------------------------------------------------------------------

def _get_note_type(
    message: Message,
    args_text: str,
) -> Tuple[NoteType, str, Optional[str], List[Tuple[str, str, bool]]]:
    """
    Inspect the reply-to message (or the command arguments) to determine what
    kind of note to save.

    Returns ``(msg_type, value, file_id, buttons)`` where:
    - ``msg_type``  : NoteType enum value.
    - ``value``     : The text content (or message_id as str when is_reply).
    - ``file_id``   : Telegram file_id for media types (None for text).
    - ``buttons``   : Parsed InlineKeyboard button list from buttonurl: syntax.
    """
    reply = message.reply_to_message
    entities = {e: t for e, t in (message.parse_entities() or {}).items()}

    # Text note from reply or command argument.
    if args_text:
        content, btns = button_markdown_parser(args_text, entities)
        msg_type = NoteType.BUTTON_TEXT if btns else NoteType.TEXT
        return msg_type, content, None, btns

    if not reply:
        return NoteType.TEXT, "", None, []

    # Media replies.
    if reply.sticker:
        return NoteType.STICKER, reply.sticker.file_id, reply.sticker.file_id, []
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

    # Plain text reply.
    text: str = reply.text or reply.caption or ""
    content, btns = button_markdown_parser(text)
    msg_type = NoteType.BUTTON_TEXT if btns else NoteType.TEXT
    return msg_type, content, None, btns


# ---------------------------------------------------------------------------
# Internal: send a saved note
# ---------------------------------------------------------------------------

async def _send_note(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message: Message,
    note: Note,
    raw: bool = False,
) -> None:
    """
    Send the saved note to the chat, handling all 8 note types.

    ``raw=True`` sends the note text without Markdown parsing (for /get noformat).
    """
    # Build button markup from NoteButton rows.
    keyboard: Optional[InlineKeyboardMarkup] = None
    if note.has_buttons and not raw:
        rows: List[List[InlineKeyboardButton]] = []
        current_row: List[InlineKeyboardButton] = []
        for btn in note.buttons:
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

    parse_mode = None if raw else ParseMode.MARKDOWN

    try:
        msg_type = NoteType(note.msg_type)
    except ValueError:
        msg_type = NoteType.TEXT

    reply_to: Optional[int] = (
        message.reply_to_message.message_id
        if message.reply_to_message
        else None
    )

    try:
        if msg_type in (NoteType.TEXT, NoteType.BUTTON_TEXT):
            await context.bot.send_message(
                chat_id=chat_id,
                text=note.value or note.name,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to,
                disable_web_page_preview=True,
            )
        elif msg_type == NoteType.STICKER:
            await context.bot.send_sticker(
                chat_id=chat_id,
                sticker=note.file_id,
                reply_to_message_id=reply_to,
            )
        elif msg_type == NoteType.DOCUMENT:
            await context.bot.send_document(
                chat_id=chat_id,
                document=note.file_id,
                caption=note.value or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to,
            )
        elif msg_type == NoteType.PHOTO:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=note.file_id,
                caption=note.value or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to,
            )
        elif msg_type == NoteType.AUDIO:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=note.file_id,
                caption=note.value or None,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to,
            )
        elif msg_type == NoteType.VOICE:
            await context.bot.send_voice(
                chat_id=chat_id,
                voice=note.file_id,
                caption=note.value or None,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to,
            )
        elif msg_type == NoteType.VIDEO:
            await context.bot.send_video(
                chat_id=chat_id,
                video=note.file_id,
                caption=note.value or None,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                reply_to_message_id=reply_to,
            )
    except BadRequest as exc:
        logger.warning("Failed to send note '%s': %s", note.name, exc.message)
        # Fallback: send text only.
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=note.value or f"(Note: {note.name})",
                reply_to_message_id=reply_to,
            )
        except BadRequest:
            pass


async def _save_note_to_db(
    chat_id: int,
    name: str,
    value: str,
    msg_type: NoteType,
    file_id: Optional[str],
    buttons: List[Tuple[str, str, bool]],
    created_by: Optional[int],
) -> None:
    """Upsert a Note row and its associated NoteButton rows."""
    async with get_session() as session:
        # Ensure Chat row exists.
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()

        # Delete any existing note with the same (chat_id, name).
        await session.execute(
            delete(Note).where(Note.chat_id == chat_id, Note.name == name)
        )
        await session.flush()

        has_buttons: bool = bool(buttons)
        note = Note(
            chat_id=chat_id,
            name=name,
            value=value,
            file_id=file_id,
            is_reply=False,
            has_buttons=has_buttons,
            msg_type=msg_type.value,
            created_by=created_by,
        )
        session.add(note)
        await session.flush()

        for btn_name, url, same_line in buttons:
            session.add(NoteButton(
                note_id=note.id,
                button_name=btn_name,
                url=url,
                same_line=same_line,
            ))


# ---------------------------------------------------------------------------
# /save
# ---------------------------------------------------------------------------

@user_admin
async def save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Save a note for this group.

    Usage:
        /save <name> <text>          — Save text as a note.
        /save <name>                 — (as reply) Save the replied message.
        /save <name> [text with buttons]

    Note names are stored lowercase.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    raw_text: str = message.text or ""
    parts = raw_text.split(None, 2)

    if len(parts) < 2:
        await message.reply_text(
            "Usage: /save <name> <content>\n"
            "Or reply to a message with /save <name>"
        )
        return

    note_name: str = parts[1].lower()
    args_text: str = parts[2].strip() if len(parts) > 2 else ""

    # Warn if trying to save a bot's message (API limitation).
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.is_bot
        and not args_text
    ):
        await message.reply_text(
            "⚠️ Saving messages from bots is limited by the Telegram API. "
            "I'll save the text content only."
        )
        args_text = message.reply_to_message.text or message.reply_to_message.caption or ""

    msg_type, value, file_id, buttons = _get_note_type(message, args_text)

    # If both value and file_id are empty, use the note name as content.
    if not value and not file_id:
        value = note_name

    await _save_note_to_db(
        chat_id=chat.id,
        name=note_name,
        value=value,
        msg_type=msg_type,
        file_id=file_id,
        buttons=buttons,
        created_by=user.id if user else None,
    )

    await message.reply_text(
        f"Saved note <code>{note_name}</code>.", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /get
# ---------------------------------------------------------------------------

async def get_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Retrieve a saved note by name.

    Pass 'noformat' as a second argument to see the raw note text (useful for
    editing existing notes).

    Usage:
        /get <name>
        /get <name> noformat
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Specify a note name: /get <name>")
        return

    note_name: str = context.args[0].lower()
    raw: bool = len(context.args) > 1 and context.args[1].lower() == "noformat"

    async with get_session() as session:
        result = await session.execute(
            select(Note).where(
                Note.chat_id == chat.id,
                Note.name == note_name,
            )
        )
        note: Optional[Note] = result.scalar_one_or_none()

        if note and note.has_buttons:
            btn_result = await session.execute(
                select(NoteButton)
                .where(NoteButton.note_id == note.id)
                .order_by(NoteButton.id)
            )
            note.buttons = list(btn_result.scalars().all())

    if not note:
        await message.reply_text(
            f"No note named <code>{note_name}</code>.", parse_mode=ParseMode.HTML
        )
        return

    if raw:
        from core.helpers.string_handling import revert_buttons
        btns_text: str = (
            revert_buttons([(b.button_name, b.url, b.same_line) for b in note.buttons])
            if note.has_buttons
            else ""
        )
        await message.reply_text(
            f"<code>{note.value or ''}{btns_text}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await _send_note(context, chat.id, message, note, raw=False)


# ---------------------------------------------------------------------------
# #notename hashtag shortcut
# ---------------------------------------------------------------------------

async def get_note_by_hashtag(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle messages like ``#notename`` as a shortcut for /get <notename>.

    Shows no error if the note doesn't exist (show_none=False).
    """
    chat = update.effective_chat
    message = update.effective_message

    if not message or not message.text:
        return

    note_name: str = message.text[1:].split()[0].lower()  # strip # and any trailing text

    async with get_session() as session:
        result = await session.execute(
            select(Note).where(
                Note.chat_id == chat.id,
                Note.name == note_name,
            )
        )
        note: Optional[Note] = result.scalar_one_or_none()
        if note and note.has_buttons:
            btn_result = await session.execute(
                select(NoteButton)
                .where(NoteButton.note_id == note.id)
                .order_by(NoteButton.id)
            )
            note.buttons = list(btn_result.scalars().all())

    if note:
        await _send_note(context, chat.id, message, note)


# ---------------------------------------------------------------------------
# /notes / /saved
# ---------------------------------------------------------------------------

async def list_notes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all notes saved in this group, alphabetically."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(Note.name)
            .where(Note.chat_id == chat.id)
            .order_by(Note.name)
        )
        names: List[str] = [row[0] for row in result.all()]

    if not names:
        await message.reply_text("📋 No notes are saved in this group.")
        return

    body: str = "\n".join(f"• <code>#{n}</code>" for n in names)
    full: str = (
        f"<b>Notes in {chat.title}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{body}"
    )

    if len(full) > 4096:
        full = full[:4090] + "\n…"

    await message.reply_text(full, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------

@user_admin
async def clear_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Delete a saved note."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Specify the note name: /clear <name>")
        return

    note_name: str = context.args[0].lower()

    async with get_session() as session:
        result = await session.execute(
            delete(Note).where(
                Note.chat_id == chat.id,
                Note.name == note_name,
            )
        )
        deleted: bool = result.rowcount > 0

    if deleted:
        await message.reply_text(
            f"Note <code>{note_name}</code> deleted.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"No note named <code>{note_name}</code>.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all note management handlers."""
    application.add_handler(
        CommandHandler("save", save, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("get", get_note, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(["notes", "saved"], list_notes, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("clear", clear_note, filters=filters.ChatType.GROUPS)
    )
    # Hashtag shortcut: #notename anywhere in the message.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.Regex(r"^#[^\s]+"),
            get_note_by_hashtag,
        )
    )
    logger.info("Plugin loaded: notes")
