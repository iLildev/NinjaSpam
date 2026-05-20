"""
plugins/disable.py — Per-chat command disable/enable system.

Commands:
  /disable <cmd>    — Disable a command for regular members in this group.
  /enable  <cmd>    — Re-enable a disabled command.
  /cmds / /disabled — List currently disabled commands.
  /listcmds         — List all commands that can be toggled.

Design:
  A registry of DisableAble command names is populated by each plugin calling
  ``register_disableable(name)`` during startup.  The DisableAbleCommandHandler
  class wraps PTB's CommandHandler and checks the DisabledCommand table before
  allowing execution.  Admins bypass the check unless ``admin_ok=False``.

  This matches Marie's behaviour:
  - ``DisableAbleCommandHandler(admin_ok=True)``  → admins can still use it.
  - ``DisableAbleCommandHandler(admin_ok=False)`` → disabled for everyone.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import DisabledCommand

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global registry of toggleable commands
# ---------------------------------------------------------------------------
_DISABLEABLE_COMMANDS: List[str] = []


def register_disableable(command: str) -> None:
    """
    Register ``command`` (without leading '/') as a toggleable command.

    Call this from each plugin's ``register()`` function for every command
    that admins should be able to disable for regular members.
    """
    name: str = command.lstrip("/").lower()
    if name not in _DISABLEABLE_COMMANDS:
        _DISABLEABLE_COMMANDS.append(name)
        _DISABLEABLE_COMMANDS.sort()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _is_disabled(chat_id: int, command: str) -> bool:
    """Return True if ``command`` is currently disabled in ``chat_id``."""
    async with get_session() as session:
        result = await session.execute(
            select(DisabledCommand).where(
                DisabledCommand.chat_id == chat_id,
                DisabledCommand.command == command,
            )
        )
        return result.scalar_one_or_none() is not None


async def _ensure_chat(session, chat_id: int) -> None:
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=""))
        await session.flush()


# ---------------------------------------------------------------------------
# DisableAbleCommandHandler
# ---------------------------------------------------------------------------

class DisableAbleCommandHandler(CommandHandler):
    """
    A CommandHandler subclass that respects per-chat command disable settings.

    When the command is disabled for the originating chat:
    - If ``admin_ok=True`` and the sender is an admin → the handler runs normally.
    - Otherwise → the handler is silently skipped (no reply).

    Usage (in plugin register functions)::

        application.add_handler(
            DisableAbleCommandHandler("rules", rules_handler, admin_ok=True)
        )
        register_disableable("rules")
    """

    def __init__(
        self,
        command: str | list[str],
        callback: Callable,
        admin_ok: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(command, callback, **kwargs)
        # Normalise to a single string for the disable check.
        self._primary_command: str = (
            command[0].lstrip("/").lower()
            if isinstance(command, list)
            else command.lstrip("/").lower()
        )
        self._admin_ok: bool = admin_ok

    async def check_update(self, update: object) -> Any:
        """Override PTB's check_update to inject disable logic."""
        result = await super().check_update(update)
        if not result:
            return result

        if not isinstance(update, Update):
            return result

        chat = update.effective_chat
        user = update.effective_user
        if not chat or not user:
            return result

        if not await _is_disabled(chat.id, self._primary_command):
            return result  # Not disabled — pass through.

        # Command is disabled. Check admin bypass.
        if self._admin_ok and await is_user_admin(chat, user.id):
            return result  # Admin bypass — allow.

        return False  # Silently block.


# ---------------------------------------------------------------------------
# /disable
# ---------------------------------------------------------------------------

@user_admin
async def disable(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Disable a command for regular members in this group.

    Usage:
        /disable rules
        /disable /rules   (leading slash is stripped automatically)
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text(
            "Specify a command to disable: /disable <command>\n"
            "Use /listcmds to see what can be disabled."
        )
        return

    command: str = context.args[0].lstrip("/").lower()

    if command not in _DISABLEABLE_COMMANDS:
        await message.reply_text(
            f"<code>{command}</code> is not a toggleable command.\n"
            "Use /listcmds to see the full list.",
            parse_mode=ParseMode.HTML,
        )
        return

    async with get_session() as session:
        await _ensure_chat(session, chat.id)
        existing = await session.execute(
            select(DisabledCommand).where(
                DisabledCommand.chat_id == chat.id,
                DisabledCommand.command == command,
            )
        )
        if existing.scalar_one_or_none():
            await message.reply_text(
                f"<code>{command}</code> is already disabled.", parse_mode=ParseMode.HTML
            )
            return
        session.add(DisabledCommand(chat_id=chat.id, command=command))

    await message.reply_text(
        f"Disabled <code>{command}</code> for regular members.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /enable
# ---------------------------------------------------------------------------

@user_admin
async def enable(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Re-enable a previously disabled command."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Specify a command to enable: /enable <command>")
        return

    command: str = context.args[0].lstrip("/").lower()

    async with get_session() as session:
        result = await session.execute(
            delete(DisabledCommand).where(
                DisabledCommand.chat_id == chat.id,
                DisabledCommand.command == command,
            )
        )
        removed: bool = result.rowcount > 0

    if removed:
        await message.reply_text(
            f"Re-enabled <code>{command}</code>.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"<code>{command}</code> wasn't disabled.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# /cmds / /disabled
# ---------------------------------------------------------------------------

async def disabled_cmds(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all commands currently disabled in this group."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(DisabledCommand.command)
            .where(DisabledCommand.chat_id == chat.id)
            .order_by(DisabledCommand.command)
        )
        cmds: List[str] = [row[0] for row in result.all()]

    if not cmds:
        await message.reply_text("No commands are currently disabled in this group.")
        return

    body: str = "\n".join(f"• <code>{c}</code>" for c in cmds)
    await message.reply_text(
        f"<b>Disabled Commands:</b>\n{body}", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /listcmds
# ---------------------------------------------------------------------------

async def list_cmds(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all commands that can be toggled via /disable."""
    message = update.effective_message

    if not _DISABLEABLE_COMMANDS:
        await message.reply_text("No commands are registered as toggleable.")
        return

    body: str = ", ".join(f"<code>{c}</code>" for c in _DISABLEABLE_COMMANDS)
    await message.reply_text(
        f"<b>Toggleable Commands:</b>\n{body}", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register command disable/enable management handlers."""
    application.add_handler(
        CommandHandler("disable", disable, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("enable", enable, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(
            ["cmds", "disabled"], disabled_cmds, filters=filters.ChatType.GROUPS
        )
    )
    application.add_handler(
        CommandHandler("listcmds", list_cmds, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: disable")
