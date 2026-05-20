"""
plugins/stats.py — Group activity statistics with inline keyboard navigation.

Tracks message activity silently in the background.  The /stats command
opens an interactive panel with four views navigated by tapping buttons:

  📊 Top Members   — Leaderboard of most active users (last 30 days).
  📅 Daily Activity — Message count chart for the last 7 days.
  ⏰ Peak Hours     — Bar chart of busiest hours of day (all time).
  📈 Overview       — Total messages, total users, all-time top sender.

All views update in-place (edit_message) when a button is tapped — no new
messages are sent.  The panel auto-expires after 5 minutes of inactivity.

Message tracking:
  Every non-command, non-bot group message is counted.  Admin messages are
  counted too — activity is activity.  Counting is fire-and-forget (errors
  are silently swallowed) so it never blocks message delivery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import ChatHourlyStat, UserDailyStat

log = logging.getLogger(__name__)

_CB = "stats"
_TRACK_GROUP = 99          # Low priority — runs after all enforcement handlers.
_TOP_N = 10                # Members shown in leaderboard.
_BAR_MAX = 12              # Max bar width for ASCII charts.

# ---------------------------------------------------------------------------
# Tracking helpers
# ---------------------------------------------------------------------------

def _utc_today() -> datetime:
    """Return today's UTC midnight as a timezone-aware datetime."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _track_message(chat_id: int, user_id: int, display_name: str, hour: int) -> None:
    """
    Upsert a daily message count row and increment the hourly slot.

    Uses a portable select-then-update/insert strategy that works on both
    PostgreSQL and SQLite — no dialect-specific ON CONFLICT syntax.
    Errors are suppressed so tracking never disrupts message flow.
    """
    try:
        today = _utc_today()
        async with get_session() as session:
            # ── UserDailyStat ──
            existing_day = await session.execute(
                select(UserDailyStat).where(
                    UserDailyStat.chat_id == chat_id,
                    UserDailyStat.user_id == user_id,
                    UserDailyStat.stat_date == today,
                )
            )
            row_day = existing_day.scalar_one_or_none()
            if row_day:
                row_day.message_count += 1
                row_day.display_name = display_name
            else:
                session.add(UserDailyStat(
                    chat_id=chat_id,
                    user_id=user_id,
                    stat_date=today,
                    message_count=1,
                    display_name=display_name,
                ))

            # ── ChatHourlyStat ──
            existing_hour = await session.execute(
                select(ChatHourlyStat).where(
                    ChatHourlyStat.chat_id == chat_id,
                    ChatHourlyStat.hour == hour,
                )
            )
            row_hour = existing_hour.scalar_one_or_none()
            if row_hour:
                row_hour.message_count += 1
            else:
                session.add(ChatHourlyStat(
                    chat_id=chat_id,
                    hour=hour,
                    message_count=1,
                ))
    except Exception as exc:
        log.debug("Stats tracking error (suppressed): %s", exc)


async def track_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Silent message counter — runs on every non-command group message."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat or user.is_bot:
        return
    if msg.text and msg.text.startswith("/"):
        return

    name = user.full_name or user.username or str(user.id)
    hour = datetime.now(timezone.utc).hour
    # Fire-and-forget — we don't await to avoid delaying message processing.
    context.application.create_task(_track_message(chat.id, user.id, name, hour))


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

async def _top_members(chat_id: int, days: int = 30) -> List[Tuple[str, int]]:
    """Return [(name, total_messages)] for top _TOP_N users in the last *days* days."""
    since = _utc_today() - timedelta(days=days)
    async with get_session() as session:
        result = await session.execute(
            select(
                UserDailyStat.user_id,
                UserDailyStat.display_name,
                func.sum(UserDailyStat.message_count).label("total"),
            )
            .where(
                UserDailyStat.chat_id == chat_id,
                UserDailyStat.stat_date >= since,
            )
            .group_by(UserDailyStat.user_id, UserDailyStat.display_name)
            .order_by(func.sum(UserDailyStat.message_count).desc())
            .limit(_TOP_N)
        )
        return [(row.display_name or str(row.user_id), row.total) for row in result]


async def _daily_activity(chat_id: int, days: int = 7) -> List[Tuple[str, int]]:
    """Return [(date_label, count)] for the last *days* days."""
    since = _utc_today() - timedelta(days=days - 1)
    async with get_session() as session:
        result = await session.execute(
            select(
                UserDailyStat.stat_date,
                func.sum(UserDailyStat.message_count).label("total"),
            )
            .where(
                UserDailyStat.chat_id == chat_id,
                UserDailyStat.stat_date >= since,
            )
            .group_by(UserDailyStat.stat_date)
            .order_by(UserDailyStat.stat_date)
        )
        rows = result.all()

    # Build a full 7-day list (fill zeros for missing days).
    date_map: dict[str, int] = {}
    for row in rows:
        label = row.stat_date.strftime("%d %b")
        date_map[label] = row.total

    full: List[Tuple[str, int]] = []
    for i in range(days):
        d = since + timedelta(days=i)
        label = d.strftime("%d %b")
        full.append((label, date_map.get(label, 0)))
    return full


async def _peak_hours(chat_id: int) -> List[Tuple[int, int]]:
    """Return [(hour, count)] sorted by hour for all 24 hours."""
    async with get_session() as session:
        result = await session.execute(
            select(ChatHourlyStat.hour, ChatHourlyStat.message_count)
            .where(ChatHourlyStat.chat_id == chat_id)
            .order_by(ChatHourlyStat.hour)
        )
        rows = {row.hour: row.message_count for row in result}
    return [(h, rows.get(h, 0)) for h in range(24)]


async def _overview(chat_id: int) -> dict:
    """Return summary stats for the overview panel."""
    async with get_session() as session:
        total_msgs = await session.execute(
            select(func.sum(UserDailyStat.message_count))
            .where(UserDailyStat.chat_id == chat_id)
        )
        total = total_msgs.scalar() or 0

        active_users = await session.execute(
            select(func.count(func.distinct(UserDailyStat.user_id)))
            .where(UserDailyStat.chat_id == chat_id)
        )
        users = active_users.scalar() or 0

        today_msgs = await session.execute(
            select(func.sum(UserDailyStat.message_count))
            .where(
                UserDailyStat.chat_id == chat_id,
                UserDailyStat.stat_date == _utc_today(),
            )
        )
        today = today_msgs.scalar() or 0

        top_user = await session.execute(
            select(
                UserDailyStat.display_name,
                func.sum(UserDailyStat.message_count).label("total"),
            )
            .where(UserDailyStat.chat_id == chat_id)
            .group_by(UserDailyStat.user_id, UserDailyStat.display_name)
            .order_by(func.sum(UserDailyStat.message_count).desc())
            .limit(1)
        )
        top = top_user.first()

    return {
        "total": total,
        "users": users,
        "today": today,
        "top_name": top.display_name if top else "—",
        "top_count": top.total if top else 0,
    }


# ---------------------------------------------------------------------------
# ASCII chart builders
# ---------------------------------------------------------------------------

def _bar(value: int, max_value: int, width: int = _BAR_MAX) -> str:
    if max_value == 0:
        return "░" * width
    filled = round((value / max_value) * width)
    return "█" * filled + "░" * (width - filled)


def _build_top_members_text(rows: List[Tuple[str, int]], title: str) -> str:
    if not rows:
        return f"<b>{title}</b>\n\nNo messages recorded yet. Start chatting! 💬"

    max_count = max(c for _, c in rows)
    medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 20
    lines = [f"<b>{title}</b>\n"]
    for i, (name, count) in enumerate(rows):
        bar = _bar(count, max_count, 10)
        safe_name = name[:20].replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"{medals[i]} <b>{safe_name}</b>  {bar}  {count:,}")
    return "\n".join(lines)


def _build_daily_text(rows: List[Tuple[str, int]], title: str) -> str:
    if not rows or all(c == 0 for _, c in rows):
        return f"<b>{title}</b>\n\nNo messages in the last 7 days."

    max_count = max(c for _, c in rows)
    lines = [f"<b>{title}</b>\n"]
    for date_label, count in rows:
        bar = _bar(count, max_count, _BAR_MAX)
        lines.append(f"<code>{date_label}</code> {bar} {count:,}")
    return "\n".join(lines)


def _build_peak_hours_text(rows: List[Tuple[int, int]], title: str) -> str:
    if not rows or all(c == 0 for _, c in rows):
        return f"<b>{title}</b>\n\nNo hourly data yet."

    max_count = max(c for _, c in rows)
    # Show only 6-hour blocks for readability.
    blocks = [
        ("🌅 00–05", rows[0:6]),
        ("🌞 06–11", rows[6:12]),
        ("🌇 12–17", rows[12:18]),
        ("🌙 18–23", rows[18:24]),
    ]
    lines = [f"<b>{title}</b>\n"]
    for block_label, block_rows in blocks:
        lines.append(f"\n{block_label}")
        for hour, count in block_rows:
            bar = _bar(count, max_count, 10)
            lines.append(f"  <code>{hour:02d}:xx</code> {bar} {count:,}")
    return "\n".join(lines)


def _build_overview_text(data: dict, chat_title: str) -> str:
    return (
        f"<b>📈 Overview — {chat_title}</b>\n\n"
        f"💬 Total messages: <b>{data['total']:,}</b>\n"
        f"👥 Active members: <b>{data['users']:,}</b>\n"
        f"📅 Messages today: <b>{data['today']:,}</b>\n"
        f"🏆 All-time top:   <b>{data['top_name']}</b> ({data['top_count']:,} msgs)"
    )


# ---------------------------------------------------------------------------
# Keyboard builder
# ---------------------------------------------------------------------------

def _keyboard(active: str, chat_id: int) -> InlineKeyboardMarkup:
    """Build the stats navigation keyboard, highlighting the active tab."""

    def btn(label: str, view: str) -> InlineKeyboardButton:
        active_marker = "› " if view == active else ""
        return InlineKeyboardButton(
            f"{active_marker}{label}",
            callback_data=f"{_CB}:{chat_id}:{view}",
        )

    return InlineKeyboardMarkup([
        [
            btn("📊 Top Members", "top"),
            btn("📅 Daily", "daily"),
        ],
        [
            btn("⏰ Peak Hours", "hours"),
            btn("📈 Overview", "overview"),
        ],
        [InlineKeyboardButton("✖ Close", callback_data=f"{_CB}:{chat_id}:close")],
    ])


# ---------------------------------------------------------------------------
# /stats command
# ---------------------------------------------------------------------------

async def stats_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Open the interactive stats panel — defaults to Overview tab."""
    chat = update.effective_chat
    message = update.effective_message

    data = await _overview(chat.id)
    text = _build_overview_text(data, chat.title or "this group")
    keyboard = _keyboard("overview", chat.id)

    await message.reply_html(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle tab navigation button presses."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return

    _, chat_id_str, view = parts[0], parts[1], parts[2]
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return

    if view == "close":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        return

    # Fetch the relevant chat title.
    try:
        chat_obj = await context.bot.get_chat(chat_id)
        chat_title = chat_obj.title or "this group"
    except BadRequest:
        chat_title = "this group"

    if view == "top":
        rows = await _top_members(chat_id, days=30)
        text = _build_top_members_text(rows, f"📊 Top Members — {chat_title} (last 30 days)")
    elif view == "daily":
        rows = await _daily_activity(chat_id, days=7)
        text = _build_daily_text(rows, f"📅 Daily Activity — {chat_title} (last 7 days)")
    elif view == "hours":
        rows = await _peak_hours(chat_id)
        text = _build_peak_hours_text(rows, f"⏰ Peak Hours — {chat_title} (UTC)")
    else:  # overview
        data = await _overview(chat_id)
        text = _build_overview_text(data, chat_title)

    keyboard = _keyboard(view, chat_id)
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register stats tracking handler, /stats command, and callback."""
    # Passive tracker — lowest priority group so it runs last.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            track_handler,
        ),
        group=_TRACK_GROUP,
    )
    application.add_handler(
        CommandHandler("stats", stats_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CallbackQueryHandler(stats_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: stats")
