"""
core/log_channel.py — The @loggable decorator and log-channel sender.

Every moderation handler decorated with @loggable must return either:
- A non-empty HTML string  → forwarded to the configured log channel.
- An empty string ``""``   → action taken but nothing to log (silent).
- ``None``                 → handler returned without completing (guard failed).

A missing or deleted log channel is handled gracefully: the bot notifies the
group and automatically unsets the broken configuration.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from database.engine import get_session
from database.models_extra import LogChannelSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal sender
# ---------------------------------------------------------------------------

async def _send_log(
    context: ContextTypes.DEFAULT_TYPE,
    log_channel_id: int,
    origin_chat_id: int,
    text: str,
) -> None:
    """
    Attempt to send ``text`` (HTML) to ``log_channel_id``.

    On ``Chat not found`` the channel record is deleted and the group is
    notified.  Other parse errors send the message without formatting as a
    graceful fallback.
    """
    try:
        await context.bot.send_message(
            chat_id=log_channel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if exc.message == "Chat not found":
            # The log channel was deleted or the bot was removed.
            async with get_session() as session:
                record = await session.get(LogChannelSettings, origin_chat_id)
                if record:
                    await session.delete(record)
            try:
                await context.bot.send_message(
                    origin_chat_id,
                    "The log channel for this group seems to have been deleted. "
                    "I've unset it — use /setlog to configure a new one.",
                )
            except (BadRequest, Forbidden):
                pass
        else:
            # HTML parse failure — retry as plain text.
            logger.warning(
                "Could not parse log message for channel %s: %s",
                log_channel_id,
                exc.message,
            )
            try:
                await context.bot.send_message(
                    chat_id=log_channel_id,
                    text=text + "\n\n<i>(Formatting disabled due to a parse error.)</i>",
                    parse_mode=None,
                )
            except (BadRequest, Forbidden):
                pass
    except Forbidden:
        # Bot was kicked from the log channel.
        async with get_session() as session:
            record = await session.get(LogChannelSettings, origin_chat_id)
            if record:
                await session.delete(record)


# ---------------------------------------------------------------------------
# @loggable decorator
# ---------------------------------------------------------------------------

def loggable(func: Callable) -> Callable:
    """
    Wrap a moderation handler so its return value is forwarded to the
    group's configured log channel.

    For supergroups with a public username a direct link to the triggering
    message is appended to the log entry for quick navigation.

    Usage::

        @user_admin
        @bot_admin
        @loggable
        async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
            ...
            return "<b>Chat:</b>\\n#BAN\\n..."
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result: Any = await func(update, context, *args, **kwargs)

        if result and isinstance(result, str):
            chat = update.effective_chat
            message = update.effective_message
            if not chat or not message:
                return result

            log_text: str = result

            # Append a direct message link for supergroups with a public handle.
            if (
                chat.type == chat.SUPERGROUP
                and chat.username
                and message.message_id
            ):
                link = (
                    f"https://t.me/{chat.username}/{message.message_id}"
                )
                log_text += (
                    f'\n<b>Link:</b> <a href="{link}">click here</a>'
                )

            # Look up the configured log channel for this chat.
            async with get_session() as session:
                result_row = await session.execute(
                    select(LogChannelSettings).where(
                        LogChannelSettings.chat_id == chat.id
                    )
                )
                settings: Optional[LogChannelSettings] = (
                    result_row.scalar_one_or_none()
                )

            if settings:
                await _send_log(
                    context,
                    settings.log_channel_id,
                    chat.id,
                    log_text,
                )
        elif result is None:
            # Handler returned None — means a guard decorator short-circuited.
            # This is normal; no warning needed.
            pass

        return result

    return wrapper
