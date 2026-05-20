"""
core/error_handler.py — Global PTB exception handler.

Catches all unhandled exceptions raised inside any update handler and logs
them with full traceback detail.  For ``TelegramError`` subclasses that
indicate a known-safe condition (e.g. the bot was blocked by the user) the
error is logged at WARNING level rather than ERROR to avoid alert fatigue.

Additionally, if ``config.OWNER_IDS`` is non-empty, the error details are
forwarded to the bot owner(s) via a private message for real-time monitoring.
"""

from __future__ import annotations

import html
import logging
import traceback

from telegram import Update
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    TelegramError,
    TimedOut,
)
from telegram.ext import Application, ContextTypes

import config

logger = logging.getLogger(__name__)

# Errors that indicate a recoverable or expected situation, not a bug.
_SAFE_ERRORS: tuple[type[TelegramError], ...] = (
    Forbidden,   # Bot was blocked by the user.
    TimedOut,    # Network hiccup — PTB will retry automatically.
    NetworkError,
)

# Bad-request messages that are safe to ignore silently.
_IGNORE_MESSAGES: frozenset[str] = frozenset([
    "Message is not modified",
    "Query is too old",
    "Message to delete not found",
    "Have no rights to send a message",
])


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Global PTB error handler — logs and (optionally) notifies the bot owner.

    Registered via ``application.add_error_handler(error_handler)`` in main.py.
    """
    error = context.error
    if error is None:
        return

    # Silently swallow known-safe Telegram errors.
    if isinstance(error, _SAFE_ERRORS):
        logger.debug("Ignored safe Telegram error: %s", error)
        return

    if isinstance(error, BadRequest) and error.message in _IGNORE_MESSAGES:
        logger.debug("Ignored benign BadRequest: %s", error.message)
        return

    # Build a rich traceback string.
    tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
    tb_text = "".join(tb_lines)

    # Determine where this error originated.
    update_str: str = "N/A"
    if isinstance(update, Update):
        chat_id: str = str(update.effective_chat.id) if update.effective_chat else "N/A"
        user_id: str = str(update.effective_user.id) if update.effective_user else "N/A"
        update_str = f"chat={chat_id} user={user_id}"

    logger.error(
        "Unhandled exception for update [%s]: %s\n%s",
        update_str,
        error,
        tb_text,
    )

    # Forward to all owner IDs.
    if not config.OWNER_IDS:
        return

    # Truncate traceback to fit within Telegram's 4096-char message limit.
    tb_preview: str = tb_text[-2800:] if len(tb_text) > 2800 else tb_text
    message_text: str = (
        f"⚠️ <b>Unhandled Exception</b>\n\n"
        f"<b>Update:</b> {html.escape(update_str)}\n"
        f"<b>Error:</b> {html.escape(str(error))}\n\n"
        f"<pre>{html.escape(tb_preview)}</pre>"
    )

    for owner_id in config.OWNER_IDS:
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=message_text,
                parse_mode="HTML",
            )
        except TelegramError:
            pass  # If we can't notify the owner, don't crash the error handler.


def register_error_handler(application: Application) -> None:
    """Register the global error handler with the PTB Application."""
    application.add_error_handler(error_handler)
    logger.info("Global error handler registered.")
