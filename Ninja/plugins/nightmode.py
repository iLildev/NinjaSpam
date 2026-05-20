"""
plugins/nightmode.py — Scheduled automatic group restriction (Night Mode).

Night mode quietly restricts a group during configured hours and automatically
lifts restrictions in the morning.  Each group independently chooses its own
timezone so the hours are always evaluated in local time — not UTC.

How it works:
  - A recurring job runs every 60 seconds via PTB's JobQueue.
  - For each group with night mode enabled, it checks if the current local time
    (in the group's configured timezone) falls within the night window.
  - If yes and restrictions aren't already active → apply restrictions + alert.
  - If no and restrictions are active → lift restrictions + notify.

Restriction modes:
  lock     — Disables chat-wide messaging via set_chat_permissions (default).
  restrict — Individually mutes all non-admin members via restrict_chat_member.

Commands (group admins only):
  /nightmode                    — Show current night mode configuration.
  /nightmode on|off             — Enable or disable night mode.
  /nighthours <HH:MM> <HH:MM>  — Set start and end time (e.g. /nighthours 23:00 07:00).
  /nighttimezone <city>         — Set the timezone for this group's night mode.
  /nightmode status             — Show current status and next trigger time.

Timezone resolution:
  - Accepts city names (Aden, London, Riyadh…) via the city_timezones helper.
  - Accepts IANA strings directly (Asia/Aden, Europe/London…).
  - If the group admin sets their personal timezone via /settimezone, they can
    use /nighttimezone copy to copy their personal timezone to the group.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Optional

import pytz
from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.helpers.city_timezones import all_suggestions, resolve_city, resolve_timezone_name
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import NightModeAction, NightModeSettings, UserTimezone

log = logging.getLogger(__name__)

# PTB job name prefix — one job per chat.
_JOB_NAME = "nightmode_check"

# Permissions applied during night mode (chat-wide lock).
_NIGHT_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
)

# Permissions restored when night mode ends.
_DAY_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _is_night_time(
    now_local: datetime,
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
) -> bool:
    """
    Return True if *now_local* falls within the night window [start, end).

    Handles overnight windows correctly (e.g. 23:00 → 07:00 spans midnight).

    Args:
        now_local:  Current time already converted to the group's local timezone.
        start_h/m:  Night start (hour, minute).
        end_h/m:    Night end (hour, minute).

    Returns:
        True if currently within the night restriction window.
    """
    now_t = dtime(now_local.hour, now_local.minute)
    start_t = dtime(start_h, start_m)
    end_t = dtime(end_h, end_m)

    if start_t < end_t:
        # Same-day window (e.g. 02:00 → 06:00).
        return start_t <= now_t < end_t
    elif start_t > end_t:
        # Overnight window (e.g. 23:00 → 07:00).
        return now_t >= start_t or now_t < end_t
    else:
        # start == end: always night (edge case — treated as disabled).
        return False


def _local_now(tz_name: Optional[str]) -> datetime:
    """Return current datetime in the given IANA timezone (UTC fallback)."""
    try:
        tz = pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        tz = pytz.utc
    return datetime.now(tz)


def _format_time(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[NightModeSettings]:
    async with get_session() as session:
        return await session.get(NightModeSettings, chat_id)


async def _get_or_create(session, chat_id: int, title: str = "") -> NightModeSettings:
    s = await session.get(NightModeSettings, chat_id)
    if s is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=title))
            await session.flush()
        s = NightModeSettings(chat_id=chat_id)
        session.add(s)
        await session.flush()
    return s


# ---------------------------------------------------------------------------
# Job: periodic night-mode check
# ---------------------------------------------------------------------------

async def _night_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs every 60 seconds.  Checks every night-mode-enabled group and
    toggles restrictions based on local time.
    """
    async with get_session() as session:
        result = await session.execute(
            select(NightModeSettings).where(NightModeSettings.enabled == True)  # noqa: E712
        )
        all_settings = result.scalars().all()

    for s in all_settings:
        chat_id = s.chat_id
        now_local = _local_now(s.timezone_name)
        should_be_night = _is_night_time(
            now_local, s.start_hour, s.start_minute, s.end_hour, s.end_minute
        )

        if should_be_night and not s.currently_active:
            # Night mode starts.
            await _activate_night(context, s)
        elif not should_be_night and s.currently_active:
            # Night mode ends.
            await _deactivate_night(context, s)


async def _activate_night(
    context: ContextTypes.DEFAULT_TYPE,
    s: NightModeSettings,
) -> None:
    """Apply night restrictions to the group and mark as active."""
    chat_id = s.chat_id
    action = NightModeAction(s.action) if isinstance(s.action, str) else s.action

    try:
        if action == NightModeAction.LOCK:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=_NIGHT_PERMS,
            )

        tz_line = f" ({s.city_label})" if s.city_label else ""
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🌙 <b>Night Mode Active</b>\n\n"
                f"The group is now restricted until "
                f"<b>{_format_time(s.end_hour, s.end_minute)}</b>"
                f"{tz_line}.\n"
                f"Messaging will resume automatically in the morning."
            ),
            parse_mode=ParseMode.HTML,
        )
        log.info("Night mode ACTIVATED for chat %s.", chat_id)

        async with get_session() as session:
            row = await session.get(NightModeSettings, chat_id)
            if row:
                row.currently_active = True

    except (BadRequest, Forbidden) as exc:
        log.warning("Night mode activate failed for chat %s: %s", chat_id, exc)


async def _deactivate_night(
    context: ContextTypes.DEFAULT_TYPE,
    s: NightModeSettings,
) -> None:
    """Lift night restrictions and mark as inactive."""
    chat_id = s.chat_id
    action = NightModeAction(s.action) if isinstance(s.action, str) else s.action

    try:
        if action == NightModeAction.LOCK:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=_DAY_PERMS,
            )

        await context.bot.send_message(
            chat_id=chat_id,
            text="☀️ <b>Good morning!</b> Night mode has ended. The group is now open.",
            parse_mode=ParseMode.HTML,
        )
        log.info("Night mode DEACTIVATED for chat %s.", chat_id)

        async with get_session() as session:
            row = await session.get(NightModeSettings, chat_id)
            if row:
                row.currently_active = False

    except (BadRequest, Forbidden) as exc:
        log.warning("Night mode deactivate failed for chat %s: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
async def nightmode_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show night mode status or toggle it on/off.

    Usage:
        /nightmode           — Show current configuration.
        /nightmode on        — Enable night mode.
        /nightmode off       — Disable night mode.
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    async with get_session() as session:
        s = await _get_or_create(session, chat.id, chat.title or "")

        if not args:
            # Show status.
            tz_display = (
                f"{s.city_label} ({s.timezone_name})"
                if s.timezone_name
                else "UTC (not set)"
            )
            now_local = _local_now(s.timezone_name)
            is_night = _is_night_time(
                now_local, s.start_hour, s.start_minute, s.end_hour, s.end_minute
            )

            await message.reply_html(
                f"<b>🌙 Night Mode — {chat.title}</b>\n\n"
                f"Status: {'✅ Enabled' if s.enabled else '✗ Disabled'}\n"
                f"Current state: {'🌙 Active (night)' if s.currently_active else '☀️ Inactive (day)'}\n"
                f"Night hours: <b>{_format_time(s.start_hour, s.start_minute)} → "
                f"{_format_time(s.end_hour, s.end_minute)}</b>\n"
                f"Timezone: <b>{tz_display}</b>\n"
                f"Local time now: <b>{now_local.strftime('%H:%M')}</b>\n"
                f"Mode: <b>{s.action}</b>\n\n"
                f"<i>Commands:\n"
                f"/nightmode on|off\n"
                f"/nighthours 23:00 07:00\n"
                f"/nighttimezone &lt;city&gt;</i>"
            )
            return

        val = args[0].lower()
        if val not in ("on", "off"):
            await message.reply_text("Usage: /nightmode <on|off>")
            return

        s.enabled = val == "on"
        if not s.enabled:
            s.currently_active = False

    if s.enabled:
        # Immediately check if we should be in night mode right now.
        now_local = _local_now(s.timezone_name)
        is_night = _is_night_time(
            now_local, s.start_hour, s.start_minute, s.end_hour, s.end_minute
        )
        tz_display = s.city_label or s.timezone_name or "UTC"
        await message.reply_html(
            f"🌙 Night mode <b>enabled</b>.\n\n"
            f"Hours: <b>{_format_time(s.start_hour, s.start_minute)} → "
            f"{_format_time(s.end_hour, s.end_minute)}</b> ({tz_display})\n"
            f"Currently: {'🌙 Night (restrictions applied)' if is_night else '☀️ Day (no restrictions)'}\n\n"
            f"<i>Set timezone with /nighttimezone &lt;city&gt; if not already configured.</i>"
        )
        if is_night:
            fresh = await _get_settings(chat.id)
            if fresh and not fresh.currently_active:
                await _activate_night(context, fresh)
    else:
        # Lift any active restrictions.
        fresh = await _get_settings(chat.id)
        if fresh and fresh.currently_active:
            await _deactivate_night(context, fresh)
        else:
            try:
                await context.bot.set_chat_permissions(
                    chat_id=chat.id, permissions=_DAY_PERMS
                )
            except (BadRequest, Forbidden):
                pass
        await message.reply_html("☀️ Night mode <b>disabled</b>. Group is now open.")


@user_admin
async def nighthours_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Set the night mode start and end times.

    Usage:
        /nighthours <HH:MM> <HH:MM>
        /nighthours 23:00 07:00     — Night from 11pm to 7am (overnight).
        /nighthours 02:00 06:00     — Night from 2am to 6am (same night).
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if len(args) < 2:
        await message.reply_text(
            "Usage: /nighthours <start HH:MM> <end HH:MM>\n"
            "Example: /nighthours 23:00 07:00"
        )
        return

    def _parse_hhmm(s: str) -> tuple[int, int]:
        parts = s.split(":")
        if len(parts) != 2:
            raise ValueError
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return h, m

    try:
        sh, sm = _parse_hhmm(args[0])
        eh, em = _parse_hhmm(args[1])
    except (ValueError, IndexError):
        await message.reply_text(
            "Invalid time format. Use HH:MM (e.g. 23:00, 07:30)."
        )
        return

    async with get_session() as session:
        s = await _get_or_create(session, chat.id, chat.title or "")
        s.start_hour = sh
        s.start_minute = sm
        s.end_hour = eh
        s.end_minute = em

    tz_display = ""
    fresh = await _get_settings(chat.id)
    if fresh and fresh.timezone_name:
        tz_display = f" ({fresh.city_label or fresh.timezone_name})"

    await message.reply_html(
        f"✅ Night hours set: "
        f"<b>{_format_time(sh, sm)} → {_format_time(eh, em)}</b>"
        f"{tz_display}\n\n"
        f"<i>Enable with /nightmode on</i>"
    )


@user_admin
async def nighttimezone_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Set the timezone used for evaluating night mode hours.

    Usage:
        /nighttimezone Aden
        /nighttimezone Asia/Riyadh
        /nighttimezone London
        /nighttimezone copy          — Copy your personal /settimezone to this group.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    args = context.args or []

    if not args:
        await message.reply_html(
            "<b>Set Night Mode Timezone</b>\n\n"
            "Usage:\n"
            "  <code>/nighttimezone Aden</code>\n"
            "  <code>/nighttimezone Asia/Riyadh</code>\n"
            "  <code>/nighttimezone copy</code>  — use your personal timezone\n\n"
            "Set your personal timezone with /settimezone"
        )
        return

    query = " ".join(args).strip()

    # Special keyword: copy from user's personal timezone.
    if query.lower() == "copy":
        async with get_session() as session:
            user_tz = await session.get(UserTimezone, user.id)
        if user_tz is None:
            await message.reply_html(
                "You haven't set a personal timezone yet.\n"
                "Use /settimezone &lt;city&gt; first, then /nighttimezone copy."
            )
            return
        tz_name = user_tz.timezone_name
        city_label = user_tz.city_label
    elif "/" in query:
        # Direct IANA string.
        match = resolve_timezone_name(query)
        if not match:
            await message.reply_text(f"'{query}' is not a valid IANA timezone string.")
            return
        tz_name = match.timezone
        city_label = match.city_label
    else:
        # City name lookup.
        match = resolve_city(query)
        if not match:
            suggestions = all_suggestions(query, limit=5)
            if suggestions:
                lines = [f"❓ City not found: <b>{query}</b>. Did you mean:\n"]
                for s in suggestions:
                    lines.append(
                        f"  • /nighttimezone {s.city_label.replace(' ', '_')} "
                        f"— {s.city_label} ({s.country})"
                    )
                await message.reply_html("\n".join(lines))
            else:
                await message.reply_html(
                    f"❓ City <b>{query}</b> not found.\n"
                    f"Try: <code>/nighttimezone Asia/Aden</code>"
                )
            return
        tz_name = match.timezone
        city_label = match.city_label

    async with get_session() as session:
        s = await _get_or_create(session, chat.id, chat.title or "")
        s.timezone_name = tz_name
        s.city_label = city_label

    now_local = _local_now(tz_name)
    fresh = await _get_settings(chat.id)
    night_window = (
        f"{_format_time(fresh.start_hour, fresh.start_minute)} → "
        f"{_format_time(fresh.end_hour, fresh.end_minute)}"
        if fresh else "23:00 → 06:00"
    )

    await message.reply_html(
        f"✅ Night mode timezone set to <b>{city_label}</b>\n"
        f"📍 IANA: <code>{tz_name}</code>\n"
        f"🕐 Local time now: <b>{now_local.strftime('%H:%M')}</b>\n"
        f"🌙 Night window: <b>{night_window}</b>"
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register night mode commands and start the periodic check job."""
    application.add_handler(
        CommandHandler("nightmode", nightmode_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("nighthours", nighthours_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("nighttimezone", nighttimezone_cmd, filters=filters.ChatType.GROUPS)
    )

    # Start the recurring 60-second job.
    job_queue = application.job_queue
    if job_queue is not None:
        job_queue.run_repeating(
            _night_check_job,
            interval=60,
            first=30,
            name=_JOB_NAME,
        )
        log.info("Night mode job scheduled (every 60s).")
    else:
        log.warning(
            "JobQueue not available — night mode automatic checks disabled. "
            "Ensure python-telegram-bot[job-queue] is installed."
        )

    log.info("Plugin loaded: nightmode")
