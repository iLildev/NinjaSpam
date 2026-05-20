"""
plugins/report_vote.py — Community Report-to-Delete voting system.

Any group member can reply to a message with /reportmsg to cast a vote
for its deletion.  When the vote count reaches the configured threshold
the message is automatically deleted and a notice is posted.

Admins are exempt from being voted on.  Each user can vote only once per
message.  The vote window expires after 10 minutes.

Commands:
  /reportmsg [reason]         — Cast a deletion vote on the replied message.
  /reportvote on|off          — Enable or disable this feature (admin).
  /reportvote threshold <n>   — Set how many votes are needed (admin, default 5).
  /reportvote status          — Show current settings (admin).

Notes:
  - A user cannot vote on their own messages.
  - Admins' messages can never be deleted by community vote.
  - The vote panel shows real-time vote count and who voted.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete as sa_delete, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models_extra import (
    ReportVoteSettings,
    ReportVoteRecord,
)

log = logging.getLogger(__name__)

_CB = "rvote"
_VOTE_WINDOW_MINUTES = 10
_DEFAULT_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[ReportVoteSettings]:
    async with get_session() as session:
        return await session.get(ReportVoteSettings, chat_id)


async def _get_vote_count(chat_id: int, message_id: int) -> int:
    async with get_session() as session:
        result = await session.execute(
            select(ReportVoteRecord).where(
                ReportVoteRecord.chat_id == chat_id,
                ReportVoteRecord.message_id == message_id,
            )
        )
        return len(result.scalars().all())


async def _has_voted(chat_id: int, message_id: int, user_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(ReportVoteRecord).where(
                ReportVoteRecord.chat_id == chat_id,
                ReportVoteRecord.message_id == message_id,
                ReportVoteRecord.voter_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None


def _vote_keyboard(chat_id: int, msg_id: int, count: int, threshold: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🚩 Report ({count}/{threshold})",
            callback_data=f"{_CB}:vote:{chat_id}:{msg_id}",
        ),
        InlineKeyboardButton(
            "❌ Close",
            callback_data=f"{_CB}:close:{chat_id}:{msg_id}",
        ),
    ]])


async def _cleanup_votes(chat_id: int, message_id: int) -> None:
    async with get_session() as session:
        await session.execute(
            sa_delete(ReportVoteRecord).where(
                ReportVoteRecord.chat_id == chat_id,
                ReportVoteRecord.message_id == message_id,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# /reportmsg
# ---------------------------------------------------------------------------

async def reportmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cast a community deletion vote on the replied-to message."""
    message = update.effective_message
    chat = update.effective_chat
    voter = update.effective_user

    if not message.reply_to_message:
        await message.reply_text("Reply to the message you want to report for deletion.")
        return

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        await message.reply_text(
            "Community report voting is not enabled here.\n"
            "Admins can enable it with /reportvote on"
        )
        return

    target = message.reply_to_message
    target_user = target.from_user

    # Cannot report own message.
    if target_user and target_user.id == voter.id:
        await message.reply_text("You can't report your own message.")
        try:
            await message.delete()
        except (BadRequest, Forbidden):
            pass
        return

    # Cannot report admin messages.
    if target_user and await is_user_admin(chat, target_user.id):
        await message.reply_text("Admin messages cannot be reported for deletion.")
        try:
            await message.delete()
        except (BadRequest, Forbidden):
            pass
        return

    # Prevent duplicate vote.
    if await _has_voted(chat.id, target.message_id, voter.id):
        await message.reply_text("You have already voted to delete this message.")
        try:
            await message.delete()
        except (BadRequest, Forbidden):
            pass
        return

    # Record vote.
    async with get_session() as session:
        session.add(ReportVoteRecord(
            chat_id=chat.id,
            message_id=target.message_id,
            voter_id=voter.id,
        ))
        await session.commit()

    # Try to delete the /reportmsg command itself.
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    vote_count = await _get_vote_count(chat.id, target.message_id)
    threshold = settings.threshold

    # Check if threshold reached.
    if vote_count >= threshold:
        try:
            await target.delete()
        except (BadRequest, Forbidden):
            pass
        await _cleanup_votes(chat.id, target.message_id)
        target_name = target_user.first_name if target_user else "Unknown"
        notice = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🗑 A message from <b>{target_name}</b> was deleted by community vote "
                f"({vote_count}/{threshold} reports)."
            ),
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(8)
        try:
            await notice.delete()
        except (BadRequest, Forbidden):
            pass
        return

    # Show or update the vote panel.
    kb = _vote_keyboard(chat.id, target.message_id, vote_count, threshold)
    reason = " ".join(context.args) if context.args else ""
    reason_text = f"\n<i>Reason: {reason}</i>" if reason else ""
    target_name = target_user.first_name if target_user else "message"

    panel_text = (
        f"🚩 <b>Community report</b> — message by <b>{target_name}</b>\n"
        f"Votes to delete: <b>{vote_count}/{threshold}</b>{reason_text}\n\n"
        f"<i>Vote panel expires in {_VOTE_WINDOW_MINUTES} minutes.</i>"
    )

    # Check if a panel already exists (stored in bot_data).
    panel_key = f"rvote_panel:{chat.id}:{target.message_id}"
    existing_panel_id = context.bot_data.get(panel_key)

    if existing_panel_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=existing_panel_id,
                text=panel_text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except (BadRequest, Forbidden):
            existing_panel_id = None

    if not existing_panel_id:
        try:
            panel = await context.bot.send_message(
                chat_id=chat.id,
                text=panel_text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            context.bot_data[panel_key] = panel.message_id
        except (BadRequest, Forbidden):
            pass

    # Schedule cleanup after vote window.
    async def _expire():
        await asyncio.sleep(_VOTE_WINDOW_MINUTES * 60)
        pid = context.bot_data.pop(panel_key, None)
        if pid:
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=pid)
            except (BadRequest, Forbidden):
                pass
        await _cleanup_votes(chat.id, target.message_id)

    asyncio.create_task(_expire())


# ---------------------------------------------------------------------------
# Callback — inline vote button
# ---------------------------------------------------------------------------

async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline 🚩 Report button press."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return

    _, action, chat_id_str, msg_id_str = parts
    chat_id = int(chat_id_str)
    msg_id = int(msg_id_str)
    voter = query.from_user

    if action == "close":
        if await is_user_admin(update.effective_chat, voter.id):
            await _cleanup_votes(chat_id, msg_id)
            try:
                await query.message.delete()
            except (BadRequest, Forbidden):
                pass
            panel_key = f"rvote_panel:{chat_id}:{msg_id}"
            context.bot_data.pop(panel_key, None)
        else:
            await query.answer("Only admins can close the vote panel.", show_alert=True)
        return

    # action == "vote"
    settings = await _get_settings(chat_id)
    if not settings or not settings.enabled:
        await query.answer("Report voting is disabled.", show_alert=True)
        return

    if await _has_voted(chat_id, msg_id, voter.id):
        await query.answer("You already voted on this message.", show_alert=True)
        return

    async with get_session() as session:
        session.add(ReportVoteRecord(
            chat_id=chat_id,
            message_id=msg_id,
            voter_id=voter.id,
        ))
        await session.commit()

    vote_count = await _get_vote_count(chat_id, msg_id)
    threshold = settings.threshold

    if vote_count >= threshold:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except (BadRequest, Forbidden):
            pass
        await _cleanup_votes(chat_id, msg_id)
        try:
            await query.message.delete()
        except (BadRequest, Forbidden):
            pass
        panel_key = f"rvote_panel:{chat_id}:{msg_id}"
        context.bot_data.pop(panel_key, None)
        notice = await context.bot.send_message(
            chat_id=chat_id,
            text=f"🗑 Message deleted by community vote ({vote_count}/{threshold} reports).",
        )
        await asyncio.sleep(8)
        try:
            await notice.delete()
        except (BadRequest, Forbidden):
            pass
        return

    kb = _vote_keyboard(chat_id, msg_id, vote_count, threshold)
    try:
        await query.edit_message_reply_markup(reply_markup=kb)
    except BadRequest:
        pass
    await query.answer(f"Vote recorded! ({vote_count}/{threshold})")


# ---------------------------------------------------------------------------
# /reportvote — admin configuration
# ---------------------------------------------------------------------------

@user_admin
async def reportvote_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin configuration for the community report voting system."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args:
        settings = await _get_settings(chat.id)
        if not settings:
            await message.reply_html(
                "<b>Community Report Voting</b>\n\n"
                "Status: ❌ Not configured\n"
                "Use /reportvote on to enable."
            )
        else:
            state = "✅ Active" if settings.enabled else "❌ Disabled"
            await message.reply_html(
                f"<b>Community Report Voting</b>\n\n"
                f"Status: {state}\n"
                f"Threshold: <b>{settings.threshold}</b> votes to delete"
            )
        return

    sub = args[0].lower()

    if sub in ("on", "off"):
        enabled = sub == "on"
        async with get_session() as session:
            row = await session.get(ReportVoteSettings, chat.id)
            if row is None:
                row = ReportVoteSettings(chat_id=chat.id, enabled=enabled)
                session.add(row)
            else:
                row.enabled = enabled
            await session.commit()
        state = "✅ enabled" if enabled else "❌ disabled"
        await message.reply_text(f"Community report voting is now {state}.")

    elif sub == "threshold" and len(args) >= 2:
        try:
            n = int(args[1])
            if n < 2 or n > 50:
                raise ValueError
        except ValueError:
            await message.reply_text("Threshold must be a number between 2 and 50.")
            return
        async with get_session() as session:
            row = await session.get(ReportVoteSettings, chat.id)
            if row is None:
                row = ReportVoteSettings(chat_id=chat.id, threshold=n)
                session.add(row)
            else:
                row.threshold = n
            await session.commit()
        await message.reply_text(f"✅ Report threshold set to {n} votes.")

    elif sub == "status":
        settings = await _get_settings(chat.id)
        if not settings:
            await message.reply_text("Not configured. Use /reportvote on to enable.")
        else:
            state = "✅ Active" if settings.enabled else "❌ Disabled"
            await message.reply_html(
                f"<b>Community Report Voting</b>\n\n"
                f"Status: {state}\n"
                f"Threshold: <b>{settings.threshold}</b> votes"
            )
    else:
        await message.reply_text(
            "Usage:\n"
            "/reportvote on|off\n"
            "/reportvote threshold <number>\n"
            "/reportvote status"
        )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("reportmsg", reportmsg, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("reportvote", reportvote_config, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CallbackQueryHandler(vote_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: report_vote")
