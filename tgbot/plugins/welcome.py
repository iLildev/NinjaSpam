"""
plugins/welcome.py — Welcome and goodbye message system.

Commands:
  /welcome [on|off|noformat]   — Toggle or view the welcome message.
  /setwelcome <text>           — Set a custom welcome (reply to media for media types).
  /resetwelcome                — Restore the built-in default welcome.
  /goodbye [on|off]            — Toggle the goodbye message.
  /setgoodbye <text>           — Set a custom goodbye.
  /resetgoodbye                — Restore the built-in default goodbye.
  /cleanwelcome [on|off]       — Auto-delete the previous welcome on each new join.
  /welcomehelp                 — Show available template variables and button syntax.

Template variables (welcome message):
  {first}, {last}, {fullname}, {username}, {mention}, {id}, {count}, {chatname}

Inline button syntax:
  [Label](buttonurl:https://example.com)
  [Same Row](buttonurl:https://example.com:same)

All events (join / leave) handled via StatusUpdate message handlers.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from sqlalchemy import select
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

from config import settings as cfg
from core.helpers.chat_status import user_admin
from core.helpers.string_handling import (
    button_markdown_parser,
    escape_invalid_curly_brackets,
)
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import NoteType, WelcomeButton, WelcomeSettings

logger = logging.getLogger(__name__)

_VALID_TEMPLATE_VARS: List[str] = [
    "first", "last", "fullname", "username", "mention", "id", "count", "chatname",
]

_DEFAULT_WELCOME: str = (
    "Hello {mention}! Welcome to {chatname}."
)
_DEFAULT_GOODBYE: str = (
    "Goodbye, {first}. We'll miss you!"
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_or_create_welcome_settings(session, chat_id: int) -> WelcomeSettings:
    settings = await session.get(WelcomeSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        settings = WelcomeSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


async def _get_buttons(session, chat_id: int, kind: str) -> List[WelcomeButton]:
    result = await session.execute(
        select(WelcomeButton).where(
            WelcomeButton.chat_id == chat_id,
            WelcomeButton.msg_kind == kind,
        ).order_by(WelcomeButton.id)
    )
    return list(result.scalars().all())


async def _save_buttons(
    session,
    chat_id: int,
    kind: str,
    buttons: List[Tuple[str, str, bool]],
) -> None:
    """Delete and replace all buttons for the given kind."""
    from sqlalchemy import delete as sa_delete
    await session.execute(
        sa_delete(WelcomeButton).where(
            WelcomeButton.chat_id == chat_id,
            WelcomeButton.msg_kind == kind,
        )
    )
    for btn_name, url, same_line in buttons:
        session.add(WelcomeButton(
            chat_id=chat_id,
            msg_kind=kind,
            button_name=btn_name,
            url=url,
            same_line=same_line,
        ))


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

async def _fill_template(
    template: str,
    user,
    chat,
    context,
) -> str:
    """Substitute template variables into the welcome/goodbye text."""
    first: str = user.first_name or "PersonWithNoName"
    last: str = user.last_name or ""
    fullname: str = (first + " " + last).strip()
    username: str = f"@{user.username}" if user.username else fullname
    mention: str = f'<a href="tg://user?id={user.id}">{first}</a>'

    try:
        count: int = await chat.get_member_count()
    except BadRequest:
        count = 0

    safe_template: str = escape_invalid_curly_brackets(template, _VALID_TEMPLATE_VARS)
    try:
        return safe_template.format(
            first=first,
            last=last,
            fullname=fullname,
            username=username,
            mention=mention,
            id=user.id,
            count=count,
            chatname=chat.title or "this group",
        )
    except (KeyError, IndexError):
        return template


# ---------------------------------------------------------------------------
# Send welcome or goodbye
# ---------------------------------------------------------------------------

async def _send_message_with_type(
    context,
    chat_id: int,
    text: str,
    file_id: Optional[str],
    msg_type: int,
    buttons: List[WelcomeButton],
) -> Optional[int]:
    """
    Send the welcome/goodbye message and return the message_id for clean_welcome.

    Returns None on failure.
    """
    keyboard: Optional[InlineKeyboardMarkup] = None
    if buttons:
        rows: List[List[InlineKeyboardButton]] = []
        current_row: List[InlineKeyboardButton] = []
        for btn in buttons:
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
        note_type = NoteType(msg_type)
    except ValueError:
        note_type = NoteType.TEXT

    try:
        sent: Message
        if note_type in (NoteType.TEXT, NoteType.BUTTON_TEXT):
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        elif note_type == NoteType.STICKER and file_id:
            sent = await context.bot.send_sticker(
                chat_id=chat_id, sticker=file_id
            )
        elif note_type == NoteType.PHOTO and file_id:
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        elif note_type == NoteType.DOCUMENT and file_id:
            sent = await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=text or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        elif note_type == NoteType.VIDEO and file_id:
            sent = await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=text or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        elif note_type == NoteType.AUDIO and file_id:
            sent = await context.bot.send_audio(
                chat_id=chat_id,
                audio=file_id,
                caption=text or None,
                parse_mode=ParseMode.HTML,
            )
        elif note_type == NoteType.VOICE and file_id:
            sent = await context.bot.send_voice(
                chat_id=chat_id,
                voice=file_id,
                caption=text or None,
                parse_mode=ParseMode.HTML,
            )
        else:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=text or "Welcome!",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        return sent.message_id
    except BadRequest as exc:
        logger.warning("Failed to send welcome/goodbye to chat %s: %s", chat_id, exc.message)
        # Fallback: send as plain text.
        try:
            fallback = await context.bot.send_message(
                chat_id=chat_id,
                text=text or "Welcome!",
            )
            return fallback.message_id
        except BadRequest:
            return None


# ---------------------------------------------------------------------------
# New member join event
# ---------------------------------------------------------------------------

async def greet_new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send the welcome message when a new member joins the group."""
    chat = update.effective_chat
    message = update.effective_message

    if not message or not message.new_chat_members:
        return

    for new_member in message.new_chat_members:
        if new_member.is_bot:
            continue  # No welcome for bots.

        async with get_session() as session:
            settings = await session.get(WelcomeSettings, chat.id)

        if not settings or not settings.welcome_enabled:
            continue

        # Delete the previous welcome message if clean_welcome is on.
        if settings.clean_welcome and settings.last_welcome_msg_id:
            try:
                await context.bot.delete_message(
                    chat_id=chat.id,
                    message_id=settings.last_welcome_msg_id,
                )
            except BadRequest:
                pass

        # Special welcome for bot owner.
        if new_member.id == cfg.OWNER_ID:
            raw_text = "My master has arrived! 🎉"
        else:
            raw_text = settings.welcome_text or _DEFAULT_WELCOME

        filled: str = await _fill_template(raw_text, new_member, chat, context)

        async with get_session() as session:
            buttons = await _get_buttons(session, chat.id, "welcome")

        sent_id = await _send_message_with_type(
            context,
            chat.id,
            filled,
            settings.welcome_file_id,
            settings.welcome_msg_type,
            buttons,
        )

        if settings.clean_welcome and sent_id:
            async with get_session() as session:
                ws = await session.get(WelcomeSettings, chat.id)
                if ws:
                    ws.last_welcome_msg_id = sent_id


# ---------------------------------------------------------------------------
# Member leave event
# ---------------------------------------------------------------------------

async def farewell_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send the goodbye message when a member leaves."""
    chat = update.effective_chat
    message = update.effective_message

    if not message or not message.left_chat_member:
        return

    left_member = message.left_chat_member
    if left_member.is_bot:
        return

    async with get_session() as session:
        settings = await session.get(WelcomeSettings, chat.id)

    if not settings or not settings.goodbye_enabled:
        return

    raw_text: str = settings.goodbye_text or _DEFAULT_GOODBYE
    filled: str = await _fill_template(raw_text, left_member, chat, context)

    async with get_session() as session:
        buttons = await _get_buttons(session, chat.id, "goodbye")

    await _send_message_with_type(
        context,
        chat.id,
        filled,
        settings.goodbye_file_id,
        settings.goodbye_msg_type,
        buttons,
    )


# ---------------------------------------------------------------------------
# /welcome
# ---------------------------------------------------------------------------

@user_admin
async def welcome_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Toggle welcome or view the current welcome message."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        # Show current setting.
        async with get_session() as session:
            settings = await session.get(WelcomeSettings, chat.id)
        status: str = "enabled" if (settings and settings.welcome_enabled) else "disabled"
        text: str = (settings.welcome_text or _DEFAULT_WELCOME) if settings else _DEFAULT_WELCOME
        await message.reply_text(
            f"Welcome messages are <b>{status}</b>.\n\n"
            f"Current message:\n<code>{text}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    choice: str = context.args[0].lower()
    if choice == "noformat":
        async with get_session() as session:
            settings = await session.get(WelcomeSettings, chat.id)
        text = (settings.welcome_text or _DEFAULT_WELCOME) if settings else _DEFAULT_WELCOME
        await message.reply_text(f"<code>{text}</code>", parse_mode=ParseMode.HTML)
        return

    if choice in ("on", "yes"):
        enabled = True
    elif choice in ("off", "no"):
        enabled = False
    else:
        await message.reply_text("Use /welcome on, /welcome off, or /welcome noformat.")
        return

    async with get_session() as session:
        ws = await _get_or_create_welcome_settings(session, chat.id)
        ws.welcome_enabled = enabled

    await message.reply_text(f"Welcome messages <b>{'enabled' if enabled else 'disabled'}</b>.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /setwelcome
# ---------------------------------------------------------------------------

@user_admin
async def set_welcome(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set a custom welcome message (supports reply to media)."""
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    reply = message.reply_to_message

    content: str = raw
    file_id: Optional[str] = None
    msg_type: NoteType = NoteType.TEXT
    buttons: List[Tuple[str, str, bool]] = []

    if reply:
        if reply.sticker:
            msg_type, file_id = NoteType.STICKER, reply.sticker.file_id
        elif reply.photo:
            msg_type, file_id = NoteType.PHOTO, reply.photo[-1].file_id
            content = reply.caption or raw
        elif reply.document:
            msg_type, file_id = NoteType.DOCUMENT, reply.document.file_id
            content = reply.caption or raw
        elif reply.video:
            msg_type, file_id = NoteType.VIDEO, reply.video.file_id
            content = reply.caption or raw
        elif reply.audio:
            msg_type, file_id = NoteType.AUDIO, reply.audio.file_id
            content = reply.caption or raw
        elif reply.voice:
            msg_type, file_id = NoteType.VOICE, reply.voice.file_id
            content = reply.caption or raw

    if msg_type == NoteType.TEXT and content:
        content, buttons = button_markdown_parser(content)
        msg_type = NoteType.BUTTON_TEXT if buttons else NoteType.TEXT

    if not content and not file_id:
        await message.reply_text(
            "Provide the welcome message text or reply to a media message."
        )
        return

    async with get_session() as session:
        ws = await _get_or_create_welcome_settings(session, chat.id)
        ws.welcome_text = content
        ws.welcome_file_id = file_id
        ws.welcome_msg_type = msg_type.value
        await _save_buttons(session, chat.id, "welcome", buttons)

    await message.reply_text("Welcome message updated.")


# ---------------------------------------------------------------------------
# /resetwelcome
# ---------------------------------------------------------------------------

@user_admin
async def reset_welcome(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reset the welcome message to the built-in default."""
    chat = update.effective_chat
    async with get_session() as session:
        ws = await session.get(WelcomeSettings, chat.id)
        if ws:
            ws.welcome_text = None
            ws.welcome_file_id = None
            ws.welcome_msg_type = NoteType.TEXT.value
            await _save_buttons(session, chat.id, "welcome", [])
    await update.effective_message.reply_text("Welcome message reset to default.")


# ---------------------------------------------------------------------------
# /goodbye
# ---------------------------------------------------------------------------

@user_admin
async def goodbye_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Toggle goodbye messages on or off."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        async with get_session() as session:
            settings = await session.get(WelcomeSettings, chat.id)
        status = "enabled" if (settings and settings.goodbye_enabled) else "disabled"
        await message.reply_text(f"Goodbye messages are <b>{status}</b>.", parse_mode=ParseMode.HTML)
        return

    choice = context.args[0].lower()
    if choice in ("on", "yes"):
        enabled = True
    elif choice in ("off", "no"):
        enabled = False
    else:
        await message.reply_text("Use /goodbye on or /goodbye off.")
        return

    async with get_session() as session:
        ws = await _get_or_create_welcome_settings(session, chat.id)
        ws.goodbye_enabled = enabled

    await message.reply_text(
        f"Goodbye messages <b>{'enabled' if enabled else 'disabled'}</b>.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /setgoodbye
# ---------------------------------------------------------------------------

@user_admin
async def set_goodbye(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set a custom goodbye message."""
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw:
        await message.reply_text("Provide the goodbye text after the command.")
        return

    content, buttons = button_markdown_parser(raw)
    msg_type = NoteType.BUTTON_TEXT if buttons else NoteType.TEXT

    async with get_session() as session:
        ws = await _get_or_create_welcome_settings(session, chat.id)
        ws.goodbye_text = content
        ws.goodbye_msg_type = msg_type.value
        await _save_buttons(session, chat.id, "goodbye", buttons)

    await message.reply_text("Goodbye message updated.")


# ---------------------------------------------------------------------------
# /resetgoodbye
# ---------------------------------------------------------------------------

@user_admin
async def reset_goodbye(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reset the goodbye message to the built-in default."""
    chat = update.effective_chat
    async with get_session() as session:
        ws = await session.get(WelcomeSettings, chat.id)
        if ws:
            ws.goodbye_text = None
            ws.goodbye_msg_type = NoteType.TEXT.value
            await _save_buttons(session, chat.id, "goodbye", [])
    await update.effective_message.reply_text("Goodbye message reset to default.")


# ---------------------------------------------------------------------------
# /cleanwelcome
# ---------------------------------------------------------------------------

@user_admin
async def clean_welcome_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Toggle auto-deletion of the previous welcome message."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Use /cleanwelcome on or /cleanwelcome off.")
        return

    choice = context.args[0].lower()
    if choice in ("on", "yes"):
        enabled = True
    elif choice in ("off", "no"):
        enabled = False
    else:
        await message.reply_text("Use /cleanwelcome on or /cleanwelcome off.")
        return

    async with get_session() as session:
        ws = await _get_or_create_welcome_settings(session, chat.id)
        ws.clean_welcome = enabled

    await message.reply_text(
        f"Clean welcome <b>{'enabled' if enabled else 'disabled'}</b>.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /welcomehelp
# ---------------------------------------------------------------------------

async def welcome_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Display all available template variables and button syntax."""
    await update.effective_message.reply_text(
        "<b>Welcome / Goodbye Template Help</b>\n\n"
        "<b>Variables:</b>\n"
        "  {first}    — First name\n"
        "  {last}     — Last name\n"
        "  {fullname} — Full name\n"
        "  {username} — @username (or full name if none)\n"
        "  {mention}  — Clickable mention link\n"
        "  {id}       — User ID\n"
        "  {count}    — Current member count\n"
        "  {chatname} — Group name\n\n"
        "<b>Inline Buttons:</b>\n"
        "  [Label](buttonurl:https://example.com)\n"
        "  [Same Row](buttonurl:https://example.com:same)\n\n"
        "<b>Example:</b>\n"
        "  <code>/setwelcome Hello {mention}, welcome to {chatname}!</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register welcome/goodbye commands and member event handlers."""
    application.add_handler(
        CommandHandler("welcome", welcome_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("setwelcome", set_welcome, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("resetwelcome", reset_welcome, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("goodbye", goodbye_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("setgoodbye", set_goodbye, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("resetgoodbye", reset_goodbye, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("cleanwelcome", clean_welcome_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("welcomehelp", welcome_help, filters=filters.ChatType.GROUPS)
    )

    # Member join event.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            greet_new_member,
        )
    )
    # Member leave event.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.LEFT_CHAT_MEMBER,
            farewell_member,
        )
    )

    logger.info("Plugin loaded: welcome")
