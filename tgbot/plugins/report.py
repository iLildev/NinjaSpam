"""
plugins/report.py — User-to-admin report system.

Any group member can reply to a message with /report to notify all current
group administrators.  Each admin receives a private message with a link to
the reported message.  If the bot cannot message an admin privately, it
sends a group mention instead.

Commands:
  /report [reason]      — Report the replied-to message to all admins.
  /reports <on|off>     — Toggle the report feature for this group (admin).
  /reports              — Show current report settings (admin).

Design notes:
  - Admins are fetched live from the Telegram API on each /report call so the
    list is always current without a caching layer.
  - Bots are excluded from the admin notification list.
  - A cooldown of 60 seconds per user prevents report flooding.
"""

from __future__ import annotations

import logging
import time
from typing import Dict

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import ReportSettings

log = logging.getLogger(__name__)

# In-memory cooldown: (chat_id, user_id) → last_report_timestamp
_REPORT_COOLDOWN: Dict[tuple, float] = {}
_COOLDOWN_SECONDS: int = 60


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_or_create_settings(session, chat_id: int, title: str = "") -> ReportSettings:
    settings = await session.get(ReportSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=title))
            await session.flush()
        settings = ReportSettings(chat_id=chat_id, enabled=True)
        session.add(settings)
        await session.flush()
    return settings


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Report a message to all group admins.

    Must be used as a reply.  The bot notifies each admin privately with a
    link to the reported message and the reporter's optional reason.

    Usage:
        Reply to a message with /report
        Reply to a message with /report <reason>
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user or not chat or not message:
        return

    if not message.reply_to_message:
        await message.reply_text(
            "Reply to the message you want to report, then use /report."
        )
        return

    # Check if feature is enabled for this chat.
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        if not settings.enabled:
            await message.reply_text("Reports are disabled in this group.")
            return

    # Enforce per-user cooldown to prevent abuse.
    key = (chat.id, user.id)
    now = time.time()
    last = _REPORT_COOLDOWN.get(key, 0)
    if now - last < _COOLDOWN_SECONDS:
        remaining = int(_COOLDOWN_SECONDS - (now - last))
        await message.reply_text(
            f"Please wait {remaining}s before reporting again."
        )
        return
    _REPORT_COOLDOWN[key] = now

    # Build reason string.
    reason: str = ""
    if context.args:
        reason = " ".join(context.args).strip()

    reported_msg = message.reply_to_message
    reported_user = reported_msg.from_user

    # Build a direct link to the reported message (only works in supergroups).
    msg_link: str = ""
    if chat.username:
        msg_link = f"https://t.me/{chat.username}/{reported_msg.message_id}"
    elif str(chat.id).startswith("-100"):
        pure_id = str(chat.id)[4:]
        msg_link = f"https://t.me/c/{pure_id}/{reported_msg.message_id}"

    reported_name = (
        reported_user.full_name if reported_user else "Unknown"
    )
    reporter_name = user.full_name or str(user.id)

    report_text = (
        f"🚨 <b>Report in {chat.title}</b>\n\n"
        f"<b>Reporter:</b> <a href='tg://user?id={user.id}'>{reporter_name}</a>\n"
        f"<b>Reported:</b> <a href='tg://user?id={reported_user.id if reported_user else 0}'>"
        f"{reported_name}</a>\n"
        + (f"<b>Reason:</b> {reason}\n" if reason else "")
        + (f"\n<b>Message:</b> <a href='{msg_link}'>Go to message</a>" if msg_link else "")
    )

    keyboard = None
    if msg_link:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 View Message", url=msg_link)]
        ])

    # Fetch current admin list.
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except BadRequest:
        await message.reply_text("Couldn't fetch admin list — please try again.")
        return

    notified: int = 0
    for admin in admins:
        if admin.user.is_bot:
            continue
        try:
            await context.bot.send_message(
                chat_id=admin.user.id,
                text=report_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            notified += 1
        except (Forbidden, BadRequest):
            # Admin hasn't started the bot in private — mention them in the group instead.
            pass

    # Delete the /report command to keep the chat clean.
    try:
        await message.delete()
    except BadRequest:
        pass

    if notified > 0:
        confirm = await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ Report sent to <b>{notified}</b> admin(s). They'll review it shortly.",
            parse_mode=ParseMode.HTML,
        )
    else:
        # No admin has the bot in PM — fall back to a group mention.
        mention_list = [
            f"<a href='tg://user?id={a.user.id}'>{a.user.first_name}</a>"
            for a in admins if not a.user.is_bot
        ]
        confirm = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🚨 {reporter_name} reported a message"
                + (f": {reason}" if reason else ".")
                + "\n\n"
                + " ".join(mention_list[:5])
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    log.info(
        "Report by user %s in chat %s — notified %d admins.", user.id, chat.id, notified
    )


# ---------------------------------------------------------------------------
# /reports (admin toggle)
# ---------------------------------------------------------------------------

@user_admin
async def reports_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Toggle the report feature or show its status.

    Usage:
        /reports          — Show current status.
        /reports on       — Enable reports.
        /reports off      — Disable reports.
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")

        if not args:
            status = "✅ Enabled" if settings.enabled else "✗ Disabled"
            await message.reply_html(
                f"<b>Report System — {chat.title}</b>\n\n"
                f"Status: {status}\n\n"
                f"<i>Members can reply to any message with /report to alert admins.</i>"
            )
            return

        val = args[0].lower()
        if val not in ("on", "off"):
            await message.reply_text("Usage: /reports <on|off>")
            return

        settings.enabled = val == "on"

    state = "enabled ✅" if settings.enabled else "disabled ✗"
    await message.reply_html(f"Report system is now <b>{state}</b>.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register the report command and admin toggle."""
    application.add_handler(
        CommandHandler("report", report, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("reports", reports_cmd, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: report")
