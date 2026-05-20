"""
plugins/broadcast.py — Owner broadcast system.

/broadcast <message>  — Send a message to every active chat.
/broadcast            — Reply to a message to forward it to every chat.

Features:
- Preview + confirm before sending (inline button).
- Rate-limited sending (1 message per 50ms) to avoid Telegram flood limits.
- Progress reporting: final stats (sent / failed / duration).
- Skips inactive chats automatically and marks chats where the bot was kicked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select, update as sql_update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from config import OWNER_IDS
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat

log = logging.getLogger(__name__)

_CONFIRM_PREFIX = "broadcast_confirm"
_CANCEL_PREFIX = "broadcast_cancel"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_active_chat_ids() -> list[int]:
    """Return a list of all active chat IDs from the database."""
    async with get_session() as session:
        result = await session.execute(
            select(Chat.id).where(Chat.is_active == True)  # noqa: E712
        )
        return [row[0] for row in result.all()]


async def _mark_chat_inactive(chat_id: int) -> None:
    """Mark a chat as inactive when the bot is no longer a member."""
    async with get_session() as session:
        await session.execute(
            sql_update(Chat).where(Chat.id == chat_id).values(is_active=False)
        )


# ---------------------------------------------------------------------------
# Core broadcast executor
# ---------------------------------------------------------------------------

async def _execute_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    chat_ids: list[int],
    text: Optional[str],
    forward_from_chat_id: Optional[int],
    forward_message_id: Optional[int],
    notify_chat_id: int,
    notify_message_id: int,
) -> None:
    """
    Send the broadcast to all chats and report results.

    Uses asyncio.sleep(0.05) between messages to stay well within
    Telegram's 30 msg/s global rate limit.
    """
    sent = 0
    failed = 0
    start_time = time.monotonic()

    for chat_id in chat_ids:
        try:
            if forward_from_chat_id and forward_message_id:
                await context.bot.forward_message(
                    chat_id=chat_id,
                    from_chat_id=forward_from_chat_id,
                    message_id=forward_message_id,
                )
            elif text:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                )
            sent += 1
        except Forbidden:
            # Bot was kicked — deactivate the chat record
            await _mark_chat_inactive(chat_id)
            failed += 1
        except TelegramError as exc:
            log.warning("Broadcast failed for chat %d: %s", chat_id, exc)
            failed += 1

        await asyncio.sleep(0.05)  # ~20 msg/s to stay safe

    duration = round(time.monotonic() - start_time, 1)

    # Report results to the owner
    try:
        lang = await get_chat_lang(notify_chat_id)
        await context.bot.edit_message_text(
            chat_id=notify_chat_id,
            message_id=notify_message_id,
            text=t("broadcast_done", lang, sent=sent, failed=failed, duration=duration),
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("Could not send broadcast completion message: %s", exc)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /broadcast — Owner-only command to broadcast a message to all chats.

    Usage:
      /broadcast <text>             — Send the provided text.
      Reply to a message + /broadcast — Forward that message to all chats.
    """
    if update.effective_user is None or update.effective_message is None:
        return

    if update.effective_user.id not in OWNER_IDS:
        return  # Silently ignore non-owners

    chat_ids = await _get_active_chat_ids()
    count = len(chat_ids)
    if count == 0:
        await update.effective_message.reply_text("No active chats found.")
        return

    lang = await get_chat_lang(update.effective_chat.id if update.effective_chat else 0)

    # Determine broadcast content
    reply = update.effective_message.reply_to_message
    text_content: Optional[str] = None
    forward_chat_id: Optional[int] = None
    forward_msg_id: Optional[int] = None

    if reply:
        forward_chat_id = reply.chat.id
        forward_msg_id = reply.message_id
    else:
        raw = " ".join(context.args or [])
        if not raw:
            await update.effective_message.reply_text(t("broadcast_usage", lang))
            return
        text_content = raw

    # Build preview + confirm keyboard
    preview = t("broadcast_confirm", lang, count=count)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                t("confirm", lang),
                callback_data=(
                    f"{_CONFIRM_PREFIX}:"
                    f"{forward_chat_id or ''}:"
                    f"{forward_msg_id or ''}:"
                    f"{update.effective_chat.id if update.effective_chat else 0}"
                ),
            ),
            InlineKeyboardButton(
                t("cancel", lang),
                callback_data=f"{_CANCEL_PREFIX}:",
            ),
        ]
    ])

    # Store broadcast content in bot_data keyed by user_id so the callback
    # can retrieve it without re-parsing.
    context.bot_data[f"bcast_{update.effective_user.id}"] = {
        "text": text_content,
        "fwd_chat": forward_chat_id,
        "fwd_msg": forward_msg_id,
        "chat_ids": chat_ids,
    }

    await update.effective_message.reply_html(preview, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Confirm / Cancel callbacks
# ---------------------------------------------------------------------------

async def _confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User confirmed the broadcast — execute it."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    if update.effective_user.id not in OWNER_IDS:
        return

    bcast_data = context.bot_data.pop(f"bcast_{update.effective_user.id}", None)
    if not bcast_data:
        await query.edit_message_text("Broadcast data expired. Please retry /broadcast.")
        return

    chat_ids: list[int] = bcast_data["chat_ids"]
    lang = await get_chat_lang(update.effective_chat.id if update.effective_chat else 0)

    progress_msg = await query.edit_message_text(
        t("broadcast_started", lang, count=len(chat_ids)),
        parse_mode="HTML",
    )

    # Run the actual broadcast in the background so the handler returns quickly
    asyncio.create_task(
        _execute_broadcast(
            context=context,
            chat_ids=chat_ids,
            text=bcast_data["text"],
            forward_from_chat_id=bcast_data["fwd_chat"],
            forward_message_id=bcast_data["fwd_msg"],
            notify_chat_id=progress_msg.chat.id,
            notify_message_id=progress_msg.message_id,
        )
    )


async def _cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancelled the broadcast."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    context.bot_data.pop(f"bcast_{update.effective_user.id}", None)
    lang = await get_chat_lang(update.effective_chat.id if update.effective_chat else 0)
    await query.edit_message_text(t("cancelled", lang))


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("broadcast", cmd_broadcast), group=10)
    application.add_handler(
        CallbackQueryHandler(_confirm_callback, pattern=rf"^{_CONFIRM_PREFIX}:"),
        group=10,
    )
    application.add_handler(
        CallbackQueryHandler(_cancel_callback, pattern=rf"^{_CANCEL_PREFIX}:"),
        group=10,
    )
    log.info("Plugin loaded: broadcast")
