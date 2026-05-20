"""
plugins/topusers.py — Message-count leaderboard for a group.

Commands:
  /topusers [N]   — Show the top N most active users in the last 30 days
                    (default N=10, max 25).  Admin only.
  /mytop          — Show your own personal rank and count in this chat.

Data source:
  Reads from UserDailyStat rows written by the stats/users_tracking plugins.
  If no data exists yet, prompts the user to wait for tracking to start.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models_extra import UserDailyStat

logger = logging.getLogger(__name__)

_MAX_ENTRIES: int = 25
_DEFAULT_ENTRIES: int = 10
_WINDOW_DAYS: int = 30

# Medal emojis for top 3, number bullets for the rest
_MEDALS = ["🥇", "🥈", "🥉"]


def _rank_prefix(i: int) -> str:
    """Return medal or numeric bullet for rank i (0-indexed)."""
    return _MEDALS[i] if i < len(_MEDALS) else f"<b>{i + 1}.</b>"


# ---------------------------------------------------------------------------
# /topusers
# ---------------------------------------------------------------------------

@user_admin
async def topusers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show top-N most active users in the last 30 days."""
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat:
        return

    # Parse optional count argument
    limit = _DEFAULT_ENTRIES
    if context.args:
        try:
            limit = max(1, min(_MAX_ENTRIES, int(context.args[0])))
        except ValueError:
            await msg.reply_text("Usage: /topusers [number] — e.g. /topusers 15")
            return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_WINDOW_DAYS)

    async with get_session() as session:
        stmt = (
            select(
                UserDailyStat.user_id,
                UserDailyStat.display_name,
                func.sum(UserDailyStat.message_count).label("total"),
            )
            .where(
                UserDailyStat.chat_id == chat.id,
                UserDailyStat.stat_date >= cutoff,
            )
            .group_by(UserDailyStat.user_id, UserDailyStat.display_name)
            .order_by(func.sum(UserDailyStat.message_count).desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        await msg.reply_text(
            "📊 No activity data yet for this group.\n"
            "Data will accumulate as members send messages.",
        )
        return

    lines = [
        f"📊 <b>Top {limit} Active Users — Last {_WINDOW_DAYS} Days</b>\n"
        f"<i>{chat.title}</i>\n"
    ]
    for i, row in enumerate(rows):
        name = row.display_name or f"User {row.user_id}"
        count = row.total
        lines.append(
            f"{_rank_prefix(i)} {name} — <code>{count}</code> messages"
        )

    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /mytop
# ---------------------------------------------------------------------------

async def mytop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the invoking user's own message rank in this chat."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_WINDOW_DAYS)

    async with get_session() as session:
        # Personal total
        my_stmt = (
            select(func.sum(UserDailyStat.message_count))
            .where(
                UserDailyStat.chat_id == chat.id,
                UserDailyStat.user_id == user.id,
                UserDailyStat.stat_date >= cutoff,
            )
        )
        my_total: int = (await session.execute(my_stmt)).scalar() or 0

        if my_total == 0:
            await msg.reply_text(
                "You don't have any messages tracked in this chat yet.",
            )
            return

        # Rank: count how many users have MORE messages than this user
        rank_stmt = (
            select(func.count())
            .select_from(
                select(
                    UserDailyStat.user_id,
                    func.sum(UserDailyStat.message_count).label("total"),
                )
                .where(
                    UserDailyStat.chat_id == chat.id,
                    UserDailyStat.stat_date >= cutoff,
                )
                .group_by(UserDailyStat.user_id)
                .having(func.sum(UserDailyStat.message_count) > my_total)
                .subquery()
            )
        )
        rank: int = (await session.execute(rank_stmt)).scalar() or 0
        rank += 1  # 1-indexed

    name = user.full_name
    await msg.reply_text(
        f"📈 <b>{name}</b>\n"
        f"Rank <b>#{rank}</b> in {chat.title}\n"
        f"<code>{my_total}</code> messages in the last {_WINDOW_DAYS} days.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    application.add_handler(
        CommandHandler(
            "topusers",
            topusers,
            filters=filters.ChatType.GROUPS,
        )
    )
    application.add_handler(
        CommandHandler(
            "mytop",
            mytop,
            filters=filters.ChatType.GROUPS,
        )
    )
    logger.info("topusers plugin registered.")
