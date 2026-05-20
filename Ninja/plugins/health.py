"""
plugins/health.py — Group Health Score.

Computes a 0–100 score that reflects how healthy, active, and clean a group
is.  The score is displayed as a colour-coded gauge with a breakdown of
each contributing component.

Score components (max points):
  ──────────────────────────────────────────────────────────
  Component              Max    What lowers it
  ──────────────────────────────────────────────────────────
  Activity              25 pts  No messages in last 7 days
  Member engagement     20 pts  Few unique senders vs. members
  Warn rate             20 pts  High warns-per-member ratio
  Spam cleanliness      20 pts  High Bayes spam-action count
  Feature coverage      15 pts  Key safety features disabled
  ──────────────────────────────────────────────────────────
  Total                100 pts

Score interpretation:
  90–100  🟢 Excellent
  70–89   🟡 Good
  50–69   🟠 Fair
  30–49   🔴 Needs attention
   0–29   💀 Critical

Commands:
  /health   — Show the group health score with component breakdown.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from database.engine import get_session
from database.models import ChatFeatureSettings, ChatMember, WarnEntry
from database.models_extra import UserDailyStat

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

async def _compute_health(chat_id: int, total_members: int) -> dict:
    """Compute all health score components. Returns a breakdown dict."""
    scores: dict[str, int] = {}
    details: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    async with get_session() as session:

        # ── 1. Activity (25 pts) ──
        msgs_week_result = await session.execute(
            select(func.sum(UserDailyStat.message_count))
            .where(
                UserDailyStat.chat_id == chat_id,
                UserDailyStat.stat_date >= week_ago,
            )
        )
        msgs_week: int = msgs_week_result.scalar() or 0

        if msgs_week >= 500:
            activity_score = 25
        elif msgs_week >= 100:
            activity_score = 18
        elif msgs_week >= 20:
            activity_score = 10
        elif msgs_week >= 1:
            activity_score = 4
        else:
            activity_score = 0
        scores["activity"] = activity_score
        details["activity"] = f"{msgs_week:,} messages this week"

        # ── 2. Member engagement (20 pts) ──
        unique_senders_result = await session.execute(
            select(func.count(func.distinct(UserDailyStat.user_id)))
            .where(
                UserDailyStat.chat_id == chat_id,
                UserDailyStat.stat_date >= week_ago,
            )
        )
        unique_senders: int = unique_senders_result.scalar() or 0

        if total_members > 0:
            engagement_pct = min(unique_senders / max(total_members, 1), 1.0)
        else:
            engagement_pct = 0.0

        if engagement_pct >= 0.3:
            engagement_score = 20
        elif engagement_pct >= 0.15:
            engagement_score = 14
        elif engagement_pct >= 0.05:
            engagement_score = 8
        elif engagement_pct >= 0.01:
            engagement_score = 3
        else:
            engagement_score = 0
        scores["engagement"] = engagement_score
        details["engagement"] = (
            f"{unique_senders} active / {total_members} members "
            f"({engagement_pct * 100:.0f}% engagement)"
        )

        # ── 3. Warn rate (20 pts) ──
        active_warn_members_result = await session.execute(
            select(func.count(func.distinct(WarnEntry.user_id)))
            .where(WarnEntry.chat_id == chat_id)
        )
        warned_members: int = active_warn_members_result.scalar() or 0

        if total_members > 0:
            warn_pct = warned_members / max(total_members, 1)
        else:
            warn_pct = 0.0

        if warn_pct == 0:
            warn_score = 20
        elif warn_pct < 0.01:
            warn_score = 16
        elif warn_pct < 0.05:
            warn_score = 10
        elif warn_pct < 0.10:
            warn_score = 4
        else:
            warn_score = 0
        scores["warn_rate"] = warn_score
        details["warn_rate"] = f"{warned_members} members warned ({warn_pct * 100:.1f}%)"

        # ── 4. Spam cleanliness (20 pts) ──
        settings = await session.get(ChatFeatureSettings, chat_id)
        bayes_ham = getattr(settings, "bayes_ham_count", 0) if settings else 0
        bayes_spam = getattr(settings, "bayes_spam_count", 0) if settings else 0
        total_classified = bayes_ham + bayes_spam

        if total_classified == 0:
            spam_score = 15  # No data — assume moderately clean
            spam_detail = "No classification data yet"
        else:
            spam_ratio = bayes_spam / total_classified
            if spam_ratio < 0.02:
                spam_score = 20
            elif spam_ratio < 0.05:
                spam_score = 15
            elif spam_ratio < 0.10:
                spam_score = 8
            elif spam_ratio < 0.20:
                spam_score = 3
            else:
                spam_score = 0
            spam_detail = (
                f"{bayes_spam} spam / {total_classified} classified "
                f"({spam_ratio * 100:.1f}% spam rate)"
            )
        scores["spam"] = spam_score
        details["spam"] = spam_detail

        # ── 5. Feature coverage (15 pts) ──
        safety_features = {
            "captcha_enabled": ("CAPTCHA", 3),
            "cas_enabled": ("CAS", 3),
            "bayes_filter_enabled": ("Bayes Filter", 3),
            "antiflood_enabled": ("Anti-flood", 2),
            "welcome_message_enabled": ("Welcome msg", 2),
            "log_channel_id": ("Log channel", 2),
        }
        feature_score = 0
        active_features: list[str] = []
        missing_features: list[str] = []

        for field, (label, pts) in safety_features.items():
            val = getattr(settings, field, None) if settings else None
            if val:
                feature_score += pts
                active_features.append(label)
            else:
                missing_features.append(label)

        scores["features"] = feature_score
        details["features"] = (
            f"Active: {', '.join(active_features) or 'none'}"
            + (f" | Missing: {', '.join(missing_features)}" if missing_features else "")
        )

    return {"scores": scores, "details": details}


def _gauge(score: int) -> str:
    """Return an ASCII gauge bar for the total score."""
    filled = round(score / 5)  # 20 chars = 100 points
    bar = "█" * filled + "░" * (20 - filled)
    return f"[{bar}] {score}/100"


def _rating(score: int) -> str:
    if score >= 90:
        return "🟢 Excellent"
    if score >= 70:
        return "🟡 Good"
    if score >= 50:
        return "🟠 Fair"
    if score >= 30:
        return "🔴 Needs Attention"
    return "💀 Critical"


def _component_line(label: str, score: int, max_pts: int, detail: str) -> str:
    bar = "█" * round((score / max_pts) * 10) + "░" * (10 - round((score / max_pts) * 10))
    return f"  <code>{bar}</code> {score}/{max_pts}  <b>{label}</b>\n  <i>{detail}</i>"


# ---------------------------------------------------------------------------
# /health command
# ---------------------------------------------------------------------------

async def health_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the group health score."""
    chat = update.effective_chat
    msg = update.effective_message

    sent = await msg.reply_text("📊 Computing group health score…")

    # Get member count from Telegram
    try:
        member_count = await context.bot.get_chat_member_count(chat.id)
    except Exception:
        member_count = 0

    breakdown = await _compute_health(chat.id, member_count)
    scores = breakdown["scores"]
    details = breakdown["details"]

    total = sum(scores.values())
    rating = _rating(total)
    gauge = _gauge(total)

    maxes = {
        "activity": 25,
        "engagement": 20,
        "warn_rate": 20,
        "spam": 20,
        "features": 15,
    }
    labels = {
        "activity": "Activity",
        "engagement": "Engagement",
        "warn_rate": "Warn Rate",
        "spam": "Spam Cleanliness",
        "features": "Feature Coverage",
    }

    components_text = "\n\n".join(
        _component_line(labels[k], scores[k], maxes[k], details[k])
        for k in maxes
    )

    text = (
        f"<b>📊 Group Health Score</b>\n"
        f"<b>{chat.title}</b>\n\n"
        f"<code>{gauge}</code>\n"
        f"Rating: {rating}\n\n"
        f"<b>━━ Component Breakdown ━━</b>\n\n"
        f"{components_text}\n\n"
        f"<i>Score updates in real-time as activity is tracked. "
        f"Enable more safety features to improve your score.</i>"
    )

    try:
        await sent.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    from telegram.ext import filters as _f
    application.add_handler(
        CommandHandler("health", health_cmd, filters=_f.ChatType.GROUPS)
    )
    log.info("Plugin loaded: health")
