"""
plugins/scheduler.py — Daily scheduled message announcements.

Allows group admins to schedule recurring daily messages.  All scheduled
times are stored as HH:MM UTC.  The bot uses PTB's ``job_queue`` to send
messages at the configured time every day.

Commands (admin-only):
  /schedule <HH:MM> <message text>  — Add a new scheduled daily message.
  /schedules                         — List all scheduled messages.
  /delschedule <id>                  — Delete a scheduled message by ID.

Limits: 5 scheduled messages per group.

On bot startup, all scheduled messages are loaded from the database and
registered with the job queue so they survive restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, time as dt_time
from typing import Optional

from sqlalchemy import select, delete as sql_delete
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models_extra import ScheduledMessage

log = logging.getLogger(__name__)

_MAX_PER_GROUP = 5
_JOB_PREFIX = "sched_"


# ---------------------------------------------------------------------------
# Job callback
# ---------------------------------------------------------------------------

async def _send_scheduled(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB job callback — sends the scheduled message."""
    data = context.job.data  # type: ignore[union-attr]
    if not data:
        return

    chat_id: int = data["chat_id"]
    text: str = data["text"]

    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as exc:
        log.warning("Scheduled message failed for chat %d: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(time_str: str) -> Optional[dt_time]:
    """Parse HH:MM into a datetime.time object. Returns None on error."""
    try:
        h, m = time_str.split(":")
        return dt_time(int(h), int(m), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _job_name(msg_id: int) -> str:
    return f"{_JOB_PREFIX}{msg_id}"


async def _register_job(application: Application, msg: ScheduledMessage) -> None:
    """Register (or re-register) a scheduled message with the PTB job queue."""
    job_queue = application.job_queue
    if job_queue is None:
        return

    scheduled_time = _parse_time(msg.time_utc)
    if scheduled_time is None:
        return

    # Remove any existing job for this ID
    for job in job_queue.get_jobs_by_name(_job_name(msg.id)):
        job.schedule_removal()

    job_queue.run_daily(
        callback=_send_scheduled,
        time=scheduled_time,
        chat_id=msg.chat_id,
        name=_job_name(msg.id),
        data={"chat_id": msg.chat_id, "text": msg.message_text},
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule <HH:MM> <message text>"""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    args = context.args or []

    if len(args) < 2:
        await update.effective_message.reply_text(t("schedule_usage", lang))
        return

    time_str = args[0]
    text = " ".join(args[1:])

    parsed_time = _parse_time(time_str)
    if parsed_time is None:
        await update.effective_message.reply_text(t("schedule_invalid_time", lang))
        return

    # Check limit
    async with get_session() as session:
        count_result = await session.execute(
            select(ScheduledMessage).where(ScheduledMessage.chat_id == chat_id)
        )
        existing = count_result.scalars().all()
        if len(existing) >= _MAX_PER_GROUP:
            await update.effective_message.reply_text(t("schedule_limit", lang))
            return

        new_msg = ScheduledMessage(
            chat_id=chat_id,
            time_utc=time_str,
            message_text=text,
        )
        session.add(new_msg)
        await session.flush()
        msg_id = new_msg.id

    # Register the job
    await _register_job(context.application, ScheduledMessage(
        id=msg_id, chat_id=chat_id, time_utc=time_str, message_text=text
    ))

    await update.effective_message.reply_html(
        t("schedule_added", lang, time=time_str)
    )


@user_admin
async def cmd_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedules — List all scheduled messages."""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)

    async with get_session() as session:
        result = await session.execute(
            select(ScheduledMessage).where(
                ScheduledMessage.chat_id == chat_id
            ).order_by(ScheduledMessage.time_utc)
        )
        messages = result.scalars().all()

    if not messages:
        await update.effective_message.reply_text(t("schedule_list_empty", lang))
        return

    lines = [f"<b>{t('schedule_list_title', lang)}</b>", ""]
    for msg in messages:
        preview = msg.message_text[:60] + "…" if len(msg.message_text) > 60 else msg.message_text
        lines.append(f"<b>#{msg.id}</b> ⏰ {msg.time_utc} UTC\n  └ {preview}")

    lines.append("\n<i>Use /delschedule &lt;id&gt; to remove.</i>")
    await update.effective_message.reply_html("\n".join(lines))


@user_admin
async def cmd_delschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delschedule <id>"""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    args = context.args or []

    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Usage: /delschedule <id>")
        return

    msg_id = int(args[0])

    async with get_session() as session:
        result = await session.execute(
            select(ScheduledMessage).where(
                ScheduledMessage.id == msg_id,
                ScheduledMessage.chat_id == chat_id,
            )
        )
        msg = result.scalar_one_or_none()
        if msg is None:
            await update.effective_message.reply_text("Scheduled message not found.")
            return
        await session.delete(msg)

    # Remove PTB job
    if context.application.job_queue:
        for job in context.application.job_queue.get_jobs_by_name(_job_name(msg_id)):
            job.schedule_removal()

    await update.effective_message.reply_text(t("schedule_deleted", lang))


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("schedule", cmd_schedule, filters=filters.ChatType.GROUPS),
        group=10,
    )
    application.add_handler(
        CommandHandler("schedules", cmd_schedules, filters=filters.ChatType.GROUPS),
        group=10,
    )
    application.add_handler(
        CommandHandler("delschedule", cmd_delschedule, filters=filters.ChatType.GROUPS),
        group=10,
    )

    # Load all scheduled messages from DB and register jobs
    try:
        async with get_session() as session:
            result = await session.execute(select(ScheduledMessage))
            all_msgs = result.scalars().all()

        for msg in all_msgs:
            await _register_job(application, msg)

        if all_msgs:
            log.info("Plugin loaded: scheduler (%d jobs registered)", len(all_msgs))
        else:
            log.info("Plugin loaded: scheduler (no active jobs)")

    except Exception as exc:
        log.warning("Scheduler: could not load jobs from DB: %s", exc)
