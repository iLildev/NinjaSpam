"""
plugins/slowmode.py — Slow mode control + Temporary admin promotion.

SLOW MODE
─────────
/slowmode <seconds>  — Enable slow mode with the given interval.
/slowmode off        — Disable slow mode.
/slowmode            — Show current slow mode setting.

Allowed values: 0 (off), 10, 30, 60, 300, 900, 3600 seconds.
Telegram accepts any value 0–21600 (6 hours) but the app only shows
preset values; the API accepts any integer in that range.

TEMPORARY PROMOTION
────────────────────
/tpromote [@user] <duration> — Promote a user as admin for a limited time.
    Examples:
        /tpromote @user 2h
        /tpromote @user 30m
        /tpromote @user 1d

The bot records the promotion in the database and schedules a demotion
job via PTB JobQueue.  If the bot restarts, on_startup re-schedules
all pending demotions automatically.

Duration format: <number><unit>  where unit is s/m/h/d.
Maximum: 7 days.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import bot_admin, can_promote, user_admin
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models_extra import TempPromotion

log = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_MAX_SECONDS = 7 * 24 * 3600  # 7 days


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------

def _parse_duration(text: str) -> Optional[int]:
    """Return seconds from a string like '2h', '30m', '1d'. None if invalid."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = value * multipliers[unit]
    if seconds <= 0 or seconds > _MAX_SECONDS:
        return None
    return seconds


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# ---------------------------------------------------------------------------
# /slowmode
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def slowmode_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set or show the chat slow mode delay."""
    chat = update.effective_chat
    msg = update.effective_message
    args = context.args or []

    if not args:
        # Show current setting
        try:
            chat_obj = await context.bot.get_chat(chat.id)
            delay = chat_obj.slow_mode_delay or 0
        except BadRequest:
            delay = 0
        if delay:
            await msg.reply_text(
                f"⏱ Slow mode is currently <b>{delay}s</b> per message.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text("⏱ Slow mode is currently <b>off</b>.", parse_mode=ParseMode.HTML)
        return

    arg = args[0].lower()
    if arg in ("off", "0"):
        delay = 0
    else:
        try:
            delay = int(arg)
        except ValueError:
            await msg.reply_text(
                "Usage: /slowmode <seconds>  or  /slowmode off\n"
                "Allowed range: 0–21600 seconds (6 hours)."
            )
            return
        if not (0 <= delay <= 21600):
            await msg.reply_text("Delay must be between 0 and 21600 seconds (6 hours).")
            return

    try:
        await context.bot.set_chat_slow_mode_delay(chat.id, delay)
    except BadRequest as exc:
        await msg.reply_text(f"Failed to set slow mode: {exc}")
        return

    if delay:
        await msg.reply_html(
            f"⏱ Slow mode set to <b>{delay}s</b>. Members must wait {delay}s between messages."
        )
    else:
        await msg.reply_html("⏱ Slow mode <b>disabled</b>.")


# ---------------------------------------------------------------------------
# /tpromote
# ---------------------------------------------------------------------------

@user_admin
@can_promote
async def tpromote_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Temporarily promote a user. Auto-demotes when time expires."""
    chat = update.effective_chat
    msg = update.effective_message

    user_id, duration_str = await extract_user_and_text(update, context)
    if not user_id:
        await msg.reply_text(
            "Usage: /tpromote @user <duration>\n"
            "Example: /tpromote @user 2h\n"
            "Duration: s=seconds, m=minutes, h=hours, d=days (max 7d)"
        )
        return

    if not duration_str:
        await msg.reply_text("Please specify a duration. Example: /tpromote @user 2h")
        return

    seconds = _parse_duration(duration_str.split()[0])
    if not seconds:
        await msg.reply_text(
            f"Invalid duration '{duration_str}'.\n"
            "Use format: 30s, 15m, 2h, 1d  (max 7d)"
        )
        return

    # Promote the user
    try:
        await context.bot.promote_chat_member(
            chat.id,
            user_id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_chat=True,
            can_manage_video_chats=True,
        )
    except (BadRequest, Forbidden) as exc:
        await msg.reply_text(f"Failed to promote user: {exc}")
        return

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    # Store in DB
    async with get_session() as session:
        existing = await session.execute(
            select(TempPromotion).where(
                TempPromotion.chat_id == chat.id,
                TempPromotion.user_id == user_id,
            )
        )
        rec = existing.scalar_one_or_none()
        if rec:
            rec.expires_at = expires_at
            rec.promoted_by = update.effective_user.id if update.effective_user else None
        else:
            session.add(TempPromotion(
                chat_id=chat.id,
                user_id=user_id,
                expires_at=expires_at,
                promoted_by=update.effective_user.id if update.effective_user else None,
            ))

    # Schedule demotion job
    job_name = f"tpromote_demote_{chat.id}_{user_id}"
    # Remove existing job if any
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    context.job_queue.run_once(
        _demote_job,
        when=seconds,
        name=job_name,
        data={"chat_id": chat.id, "user_id": user_id},
    )

    dur_str = _format_duration(seconds)
    await msg.reply_html(
        f"✅ <a href='tg://user?id={user_id}'>User</a> temporarily promoted for <b>{dur_str}</b>.\n"
        f"Auto-demotion scheduled at: <code>{expires_at.strftime('%Y-%m-%d %H:%M UTC')}</code>"
    )


async def _demote_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — demote a user whose temp promotion expired."""
    data = context.job.data
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]

    try:
        await context.bot.promote_chat_member(
            chat_id, user_id,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_manage_chat=False,
            can_manage_video_chats=False,
            can_change_info=False,
            can_invite_users=False,
        )
        log.info("Temp promotion expired: demoted user %d in chat %d", user_id, chat_id)
    except (BadRequest, Forbidden) as exc:
        log.warning("Failed to demote %d in %d: %s", user_id, chat_id, exc)

    # Clean up DB record
    async with get_session() as session:
        await session.execute(
            delete(TempPromotion).where(
                TempPromotion.chat_id == chat_id,
                TempPromotion.user_id == user_id,
            )
        )


async def _reschedule_demotions(application: Application) -> None:
    """
    On startup, re-schedule any pending temporary demotions that survived
    a bot restart.  Expired ones are demoted immediately.
    """
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        result = await session.execute(select(TempPromotion))
        pending = result.scalars().all()

    for rec in pending:
        expires = rec.expires_at.replace(tzinfo=timezone.utc) if rec.expires_at.tzinfo is None else rec.expires_at
        delay = (expires - now).total_seconds()
        job_name = f"tpromote_demote_{rec.chat_id}_{rec.user_id}"

        if delay <= 0:
            # Already expired — demote now
            try:
                await application.bot.promote_chat_member(
                    rec.chat_id, rec.user_id,
                    can_delete_messages=False,
                    can_restrict_members=False,
                    can_pin_messages=False,
                    can_manage_chat=False,
                )
            except Exception as exc:
                log.debug("Startup demote failed for %d: %s", rec.user_id, exc)
            async with get_session() as session:
                await session.execute(
                    delete(TempPromotion).where(TempPromotion.id == rec.id)
                )
        else:
            application.job_queue.run_once(
                _demote_job,
                when=delay,
                name=job_name,
                data={"chat_id": rec.chat_id, "user_id": rec.user_id},
            )
            log.info("Re-scheduled demotion for user %d in chat %d (%.0fs)", rec.user_id, rec.chat_id, delay)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("slowmode", slowmode_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("tpromote", tpromote_cmd, filters=filters.ChatType.GROUPS)
    )
    # Re-schedule any surviving temp promotions from before restart
    await _reschedule_demotions(application)
    log.info("Plugin loaded: slowmode (+ tpromote)")
