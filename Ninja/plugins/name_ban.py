"""
plugins/name_ban.py — Auto-ban users by display name / username pattern.
(Inspired by OriginProtocol/telegram-moderator)

When a new member joins, their first_name + last_name + username are checked
against admin-configured patterns. Matching users are banned immediately.
Patterns can be plain substrings (case-insensitive) or full Python regex.

Commands (admins only):
  /addnameban <text>        — Add a plain substring pattern.
  /addnameban r/<regex>/    — Add a regex pattern.
  /remnameban <pattern>     — Remove a pattern by exact text.
  /namebans                 — List all active patterns.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import NameBanPattern

logger = logging.getLogger(__name__)


def _matches(pattern: str, is_regex: bool, text: str) -> bool:
    """Return True if text matches the pattern."""
    if is_regex:
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return False
    return pattern.lower() in text.lower()


def _parse_input(raw: str) -> tuple[str, bool]:
    """Parse user input into (pattern, is_regex)."""
    raw = raw.strip()
    if raw.startswith("r/") and raw.endswith("/") and len(raw) > 3:
        return raw[2:-1], True
    return raw, False


# ---------------------------------------------------------------------------
# /addnameban
# ---------------------------------------------------------------------------

@user_admin
async def cmd_addnameban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    raw = (message.text or "").split(None, 1)
    if len(raw) < 2 or not raw[1].strip():
        await message.reply_text(
            "⚠️ Usage:\n"
            "<code>/addnameban word</code> — Plain text\n"
            "<code>/addnameban r/regex.*/</code> — Regex",
            parse_mode=ParseMode.HTML,
        )
        return

    pattern, is_regex = _parse_input(raw[1])

    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            await message.reply_text(f"❌ Invalid Regex: <code>{e}</code>", parse_mode=ParseMode.HTML)
            return

    async with get_session() as session:
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()

        existing = await session.execute(
            select(NameBanPattern).where(
                NameBanPattern.chat_id == chat.id,
                NameBanPattern.pattern == pattern,
            )
        )
        if existing.scalar_one_or_none():
            await message.reply_text("ℹ️ This pattern already exists.")
            return

        session.add(NameBanPattern(chat_id=chat.id, pattern=pattern, is_regex=is_regex))
        await session.commit()

    kind = "Regex" if is_regex else "Text"
    await message.reply_text(
        f"✅ <b>Name Ban Added</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📝 Type: <b>{kind}</b>\n"
        f"🔍 Pattern: <code>{pattern}</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /remnameban
# ---------------------------------------------------------------------------

@user_admin
async def cmd_remnameban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    raw = (message.text or "").split(None, 1)
    if len(raw) < 2 or not raw[1].strip():
        await message.reply_text("⚠️ Provide the pattern to remove.")
        return

    pattern, _ = _parse_input(raw[1])

    async with get_session() as session:
        result = await session.execute(
            select(NameBanPattern).where(
                NameBanPattern.chat_id == chat.id,
                NameBanPattern.pattern == pattern,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            await message.reply_text("❌ Pattern not found.")
            return
        await session.delete(row)
        await session.commit()

    await message.reply_text(
        f"🗑 <b>Name Ban Removed</b>: <code>{pattern}</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /namebans
# ---------------------------------------------------------------------------

@user_admin
async def cmd_namebans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    async with get_session() as session:
        result = await session.execute(
            select(NameBanPattern)
            .where(NameBanPattern.chat_id == chat.id)
            .order_by(NameBanPattern.id)
        )
        rows = result.scalars().all()

    if not rows:
        await update.message.reply_text("📋 No name-ban patterns in this group.")
        return

    lines = []
    for r in rows:
        kind = "regex" if r.is_regex else "text"
        lines.append(f"• [{kind}] <code>{r.pattern}</code>")

    await update.message.reply_text(
        f"🚫 <b>Name Ban Patterns ({len(rows)})</b>\n"
        f"━━━━━━━━━━━━━━━\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Enforcement — check new members
# ---------------------------------------------------------------------------

async def _check_user(chat_id: int, user_id: int, full_name: str, username: str,
                      context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True and ban user if their name/username matches any pattern."""
    async with get_session() as session:
        result = await session.execute(
            select(NameBanPattern).where(NameBanPattern.chat_id == chat_id)
        )
        patterns = result.scalars().all()

    if not patterns:
        return False

    check_strings = [s for s in [full_name, username] if s]

    for row in patterns:
        for s in check_strings:
            if _matches(row.pattern, row.is_regex, s):
                try:
                    await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                    logger.info(
                        "name_ban: banned user %d (name=%r) matched pattern %r in chat %d",
                        user_id, s, row.pattern, chat_id,
                    )
                    return True
                except (BadRequest, TelegramError) as e:
                    logger.warning("name_ban: failed to ban %d: %s", user_id, e)
                    return False
    return False


async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.new_chat_members:
        return
    chat = update.effective_chat
    for user in message.new_chat_members:
        if user.is_bot:
            continue
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        username = user.username or ""
        banned = await _check_user(chat.id, user.id, full_name, username, context)
        if banned:
            await message.reply_text(
                f"🚫 <b>Auto-Ban</b>: <a href='tg://user?id={user.id}'>{full_name}</a>\n"
                f"Reason: Name matches a banned pattern.",
                parse_mode=ParseMode.HTML,
            )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("addnameban", cmd_addnameban, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("remnameban", cmd_remnameban, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("namebans", cmd_namebans, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            handle_new_member,
            block=False,
        )
    )
    logger.info("Plugin loaded: name_ban")
