"""
plugins/report.py — User-to-admin report system with inline action buttons.

Any group member can reply to a message with /report to notify all current
group administrators.  Each admin receives a private message with a link to
the reported message plus action buttons to handle it directly from PM.

Commands:
  /report [reason]      — Report the replied-to message (reply required).
  /reports <on|off>     — Toggle the report feature for this group (admin).
  /reports              — Show current report settings (admin).

Admin action buttons (sent to each admin's PM):
  🔨 Ban     — Permanently ban the reported user.
  👢 Kick    — Kick (remove) the reported user.
  🔇 Mute    — Mute the reported user for 24 hours.
  ⚠️ Warn    — Issue a formal warning to the reported user.
  ✅ Dismiss — Mark the report handled (removes buttons from admin PM).

Design notes:
  - Admins are fetched live on each /report call — always current.
  - Bots are excluded from the notification list.
  - Cooldown of 60 s per user per chat prevents flooding.
  - Action callbacks verify the clicking admin is a current group admin.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Dict, Optional

from sqlalchemy import select
from telegram import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import ReportSettings

log = logging.getLogger(__name__)

# In-memory cooldown: (chat_id, user_id) → last_report_timestamp
_REPORT_COOLDOWN: Dict[tuple, float] = {}
_COOLDOWN_SECONDS: int = 60

_CB = "rep_act"

# Mute permissions (all send-rights disabled)
_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


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
# Keyboard builder
# ---------------------------------------------------------------------------

def _action_keyboard(chat_id: int, user_id: int, msg_id: int, msg_link: str = "") -> InlineKeyboardMarkup:
    """Build the admin action keyboard attached to each report notification."""
    base = f"{_CB}:{chat_id}:{user_id}:{msg_id}"
    rows = [
        [
            InlineKeyboardButton("🔨 Ban",  callback_data=f"{base}:ban"),
            InlineKeyboardButton("👢 Kick", callback_data=f"{base}:kick"),
            InlineKeyboardButton("🔇 Mute", callback_data=f"{base}:mute"),
        ],
        [
            InlineKeyboardButton("⚠️ Warn",    callback_data=f"{base}:warn"),
            InlineKeyboardButton("✅ Dismiss", callback_data=f"{base}:dismiss"),
        ],
    ]
    if msg_link:
        rows.insert(0, [InlineKeyboardButton("📩 View Message", url=msg_link)])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Report a message to all group admins.

    Must be used as a reply.  Each admin receives a PM with a link to the
    reported message and inline action buttons.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not user or not chat or not message:
        return

    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ Reply to the message you want to report, then use /report."
        )
        return

    # Feature gate
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        if not settings.enabled:
            await message.reply_text("ℹ️ Reports are disabled in this group.")
            return

    # Cooldown
    key = (chat.id, user.id)
    now = time.time()
    last = _REPORT_COOLDOWN.get(key, 0)
    if now - last < _COOLDOWN_SECONDS:
        remaining = int(_COOLDOWN_SECONDS - (now - last))
        await message.reply_text(
            f"⏳ Please wait <b>{remaining}s</b> before submitting another report.",
            parse_mode=ParseMode.HTML,
        )
        return
    _REPORT_COOLDOWN[key] = now

    reason: str = " ".join(context.args).strip() if context.args else ""

    reported_msg = message.reply_to_message
    reported_user = reported_msg.from_user

    # Build a deep link to the reported message
    msg_link: str = ""
    if chat.username:
        msg_link = f"https://t.me/{chat.username}/{reported_msg.message_id}"
    elif str(chat.id).startswith("-100"):
        pure_id = str(chat.id)[4:]
        msg_link = f"https://t.me/c/{pure_id}/{reported_msg.message_id}"

    reported_name = html.escape(reported_user.full_name if reported_user else "Unknown")
    reporter_mention = user.mention_html()
    reported_id: int = reported_user.id if reported_user else 0

    report_text = (
        f"🚨 <b>New Report — {html.escape(chat.title or '')}</b>\n\n"
        f"<b>Reporter:</b> {reporter_mention}\n"
        f"<b>Reported:</b> <a href='tg://user?id={reported_id}'>{reported_name}</a>"
        + (f"\n<b>Reason:</b> {html.escape(reason)}" if reason else "")
        + (f"\n\n<i>Use the buttons below to take action.</i>" if reported_id else "")
    )

    keyboard = _action_keyboard(chat.id, reported_id, reported_msg.message_id, msg_link)

    # Fetch current admins
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except BadRequest:
        await message.reply_text("⚠️ Couldn't fetch the admin list — please try again.")
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
            # Admin hasn't started the bot in PM — skip silently
            pass

    # Delete the /report command to keep the chat clean
    try:
        await message.delete()
    except BadRequest:
        pass

    if notified > 0:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ Your report has been sent to <b>{notified}</b> admin(s). They'll review it shortly.",
            parse_mode=ParseMode.HTML,
        )
    else:
        # Fallback: no admin has started the bot in PM — mention them in the group
        mention_list = [
            f"<a href='tg://user?id={a.user.id}'>{html.escape(a.user.first_name)}</a>"
            for a in admins if not a.user.is_bot
        ]
        link_btn = InlineKeyboardMarkup([[InlineKeyboardButton("📩 View Message", url=msg_link)]]) if msg_link else None
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🚨 {reporter_mention} reported a message"
                + (f": <i>{html.escape(reason)}</i>" if reason else ".")
                + "\n\n"
                + " ".join(mention_list[:5])
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=link_btn,
        )

    log.info("Report by user %s in chat %s — notified %d admins.", user.id, chat.id, notified)


# ---------------------------------------------------------------------------
# Callback handler — admin action buttons
# ---------------------------------------------------------------------------

async def report_action_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle admin action buttons on report notifications.

    Callback data format:
        rep_act:{chat_id}:{user_id}:{msg_id}:{action}

    Actions: ban | kick | mute | warn | dismiss
    """
    query: Optional[CallbackQuery] = update.callback_query
    if not query:
        return
    await query.answer()

    admin = update.effective_user
    if not admin:
        return

    # Parse callback data: rep_act:{chat_id}:{user_id}:{msg_id}:{action}
    parts = (query.data or "").split(":")
    if len(parts) != 5:
        return
    _, chat_id_str, user_id_str, _msg_id_str, action = parts

    try:
        chat_id = int(chat_id_str)
        reported_user_id = int(user_id_str)
    except ValueError:
        return

    # Verify the clicking admin is still a group admin
    try:
        member = await context.bot.get_chat_member(chat_id, admin.id)
        if member.status not in ("administrator", "creator"):
            await query.answer("⛔ You are no longer an admin in that group.", show_alert=True)
            return
    except TelegramError:
        await query.answer("⚠️ Could not verify your admin status.", show_alert=True)
        return

    result_text: str = ""

    if action == "dismiss":
        result_text = f"✅ Dismissed by {admin.mention_html()}."

    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=reported_user_id)
            result_text = f"🔨 User banned by {admin.mention_html()}."
        except TelegramError as e:
            await query.answer(f"Failed: {e.message}", show_alert=True)
            return

    elif action == "kick":
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=reported_user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=reported_user_id)
            result_text = f"👢 User kicked by {admin.mention_html()}."
        except TelegramError as e:
            await query.answer(f"Failed: {e.message}", show_alert=True)
            return

    elif action == "mute":
        try:
            from datetime import datetime, timezone, timedelta
            until = datetime.now(tz=timezone.utc) + timedelta(hours=24)
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=reported_user_id,
                permissions=_MUTE_PERMISSIONS,
                until_date=until,
            )
            result_text = f"🔇 User muted 24h by {admin.mention_html()}."
        except TelegramError as e:
            await query.answer(f"Failed: {e.message}", show_alert=True)
            return

    elif action == "warn":
        try:
            from plugins.warns import issue_warn
            warn_result = await issue_warn(
                context=context,
                chat_id=chat_id,
                user_id=reported_user_id,
                reason="Reported by group member",
                issuer_name=admin.full_name,
            )
            result_text = f"⚠️ Warning issued by {admin.mention_html()}. {warn_result}"
        except Exception as e:
            log.warning("Could not issue warn from report action: %s", e)
            result_text = f"⚠️ Warn noted by {admin.mention_html()} — use /warn manually in the group."

    else:
        return

    # Edit the PM message to show outcome and remove buttons
    try:
        original = query.message.text or query.message.caption or ""
        # Keep the original report text, append outcome
        separator = "\n\n" + "─" * 20 + "\n"
        new_text = original + separator + result_text
        if len(new_text) > 4096:
            new_text = new_text[-4090:]
        await query.edit_message_text(
            new_text,
            parse_mode=ParseMode.HTML,
            reply_markup=None,  # Remove action buttons after handling
        )
    except TelegramError:
        pass

    await query.answer("✅ Action taken.")


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
                f"<b>📢 Report System — {html.escape(chat.title or '')}</b>\n\n"
                f"Status: <b>{status}</b>\n\n"
                f"<i>Members can reply to any message with /report to alert admins.\n"
                f"Admins receive action buttons to Ban, Kick, Mute, Warn, or Dismiss.</i>"
            )
            return

        val = args[0].lower()
        if val not in ("on", "off"):
            await message.reply_text("Usage: /reports <on|off>")
            return

        settings.enabled = val == "on"

    state = "enabled ✅" if settings.enabled else "disabled ✗"
    await message.reply_html(f"📢 Report system is now <b>{state}</b>.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register the report command, admin toggle, and action callbacks."""
    application.add_handler(
        CommandHandler("report", report, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("reports", reports_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CallbackQueryHandler(report_action_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: report")
