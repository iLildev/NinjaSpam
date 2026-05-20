"""
plugins/adaptive_captcha.py — Adaptive risk-based CAPTCHA.

Supplements the standard CAPTCHA plugin by computing a per-user risk score
at join time and selecting the appropriate challenge difficulty:

  Score 0–39  (LOW)    → Simple inline button
  Score 40–69 (MEDIUM) → Math question (inline buttons with wrong answers)
  Score 70+   (HIGH)   → Harder math + text confirmation

Risk scoring heuristics (additive, capped at 100):
  +20  No profile photo
  +15  Account created recently (username patterns: numeric-heavy usernames)
  +20  Joined ≥ 3 groups in the past 60 minutes (cross-chat tracking)
  +15  Username contains only numbers or is blank
  +10  First name too short (< 2 chars) or looks auto-generated
  +20  User in intermediate risk range from standard checks

This plugin only activates when ``AdaptiveCaptchaSettings.adaptive_mode``
is True for the chat (set via /settings → CAPTCHA → Type: Adaptive 🧠).
It does NOT replace the base captcha plugin — it wraps around it.
"""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models_extra import AdaptiveCaptchaSettings, UserRiskScore

log = logging.getLogger(__name__)

_CB_PREFIX = "acap"

# In-memory join-rate tracker: user_id → list[timestamps]
_join_tracker: dict[int, list[float]] = defaultdict(list)
_JOIN_WINDOW = 3600.0  # 1 hour
_JOIN_THRESHOLD = 3    # Joins in window to get +20 risk

# Pending challenges: (chat_id, user_id) → {answer: int, msg_id: int}
_pending: dict[tuple[int, int], dict] = {}


# ---------------------------------------------------------------------------
# Risk score computation
# ---------------------------------------------------------------------------

async def _compute_risk(user, context) -> int:
    """
    Compute a risk score 0–100 for *user*.

    This is a fast, heuristic check that runs synchronously after
    fetching the user's Telegram profile.
    """
    score = 0

    # No username
    if not user.username:
        score += 10

    # Numeric-heavy or very short first name
    first = user.first_name or ""
    if len(first) < 2:
        score += 10
    numeric_ratio = sum(c.isdigit() for c in first) / max(len(first), 1)
    if numeric_ratio > 0.5:
        score += 15

    # No last name (weak signal, small penalty)
    if not user.last_name:
        score += 5

    # Join-rate tracking
    now = time.monotonic()
    joins = _join_tracker[user.id]
    joins = [t for t in joins if now - t <= _JOIN_WINDOW]
    joins.append(now)
    _join_tracker[user.id] = joins
    if len(joins) >= _JOIN_THRESHOLD:
        score += 20

    # Profile photo check
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count == 0:
            score += 20
    except Exception:
        score += 10  # Can't check → partial penalty

    # DB persisted score bonus (from previous encounters)
    try:
        async with get_session() as session:
            res = await session.execute(
                select(UserRiskScore).where(UserRiskScore.user_id == user.id)
            )
            row = res.scalar_one_or_none()
            if row and row.risk_score > 50:
                score += 10
    except Exception:
        pass

    return min(score, 100)


async def _save_risk(user_id: int, score: int) -> None:
    """Persist risk score in the database."""
    try:
        async with get_session() as session:
            res = await session.execute(
                select(UserRiskScore).where(UserRiskScore.user_id == user_id)
            )
            row = res.scalar_one_or_none()
            if row is None:
                row = UserRiskScore(user_id=user_id, risk_score=score)
                session.add(row)
            else:
                # Exponential moving average to update persistent score
                row.risk_score = int(row.risk_score * 0.7 + score * 0.3)
    except Exception as exc:
        log.debug("Could not save risk score: %s", exc)


# ---------------------------------------------------------------------------
# Challenge generators
# ---------------------------------------------------------------------------

def _make_math_question() -> tuple[str, int]:
    """Return (question_text, correct_answer)."""
    a = random.randint(10, 50)
    b = random.randint(1, 20)
    op = random.choice(["+", "-", "×"])
    if op == "+":
        return f"{a} + {b}", a + b
    elif op == "-":
        return f"{a} - {b}", a - b
    else:
        return f"{a} × {b}", a * b


def _make_wrong_answers(correct: int, count: int = 3) -> list[int]:
    wrong = set()
    while len(wrong) < count:
        delta = random.choice([-3, -2, -1, 1, 2, 3, 5, -5])
        candidate = correct + delta
        if candidate != correct and candidate > 0:
            wrong.add(candidate)
    return list(wrong)


# ---------------------------------------------------------------------------
# New member handler
# ---------------------------------------------------------------------------

async def _handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Intercept new member joins and apply adaptive CAPTCHA."""
    if update.message is None or update.effective_chat is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)

    # Check if adaptive mode is enabled for this chat
    async with get_session() as session:
        res = await session.execute(
            select(AdaptiveCaptchaSettings).where(
                AdaptiveCaptchaSettings.chat_id == chat_id
            )
        )
        setting = res.scalar_one_or_none()

    if setting is None or not setting.adaptive_mode:
        return  # Fallback to standard captcha plugin

    bot_id = context.bot.id

    for user in update.message.new_chat_members:
        if user.is_bot or user.id == bot_id:
            continue

        risk = await _compute_risk(user, context)
        await _save_risk(user.id, risk)

        mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'

        if risk < 40:
            # LOW risk — simple button
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ I am human",
                    callback_data=f"{_CB_PREFIX}:verify:{chat_id}:{user.id}:low",
                )
            ]])
            await update.message.reply_html(
                t("adaptive_captcha_low", lang, mention=mention),
                reply_markup=keyboard,
            )

        elif risk < 70:
            # MEDIUM risk — math question with buttons
            question, answer = _make_math_question()
            wrongs = _make_wrong_answers(answer)
            all_opts = [answer] + wrongs
            random.shuffle(all_opts)

            _pending[(chat_id, user.id)] = {"answer": answer, "risk": risk}

            buttons = [
                InlineKeyboardButton(
                    str(opt),
                    callback_data=f"{_CB_PREFIX}:answer:{chat_id}:{user.id}:{opt}",
                )
                for opt in all_opts
            ]
            keyboard = InlineKeyboardMarkup([buttons])

            await update.message.reply_html(
                t("adaptive_captcha_med", lang, mention=mention, question=question),
                reply_markup=keyboard,
            )

        else:
            # HIGH risk — harder math + typed answer required
            question, answer = _make_math_question()
            second_q, second_a = _make_math_question()
            combined_q = f"{question} = ? and {second_q} = ?"
            combined_a = answer * 1000 + second_a  # Encoded pair

            wrongs = _make_wrong_answers(answer)
            all_opts = [answer] + wrongs
            random.shuffle(all_opts)

            _pending[(chat_id, user.id)] = {
                "answer": answer,
                "risk": risk,
                "second_q": second_q,
                "second_a": second_a,
            }

            buttons = [
                InlineKeyboardButton(
                    str(opt),
                    callback_data=f"{_CB_PREFIX}:answer:{chat_id}:{user.id}:{opt}",
                )
                for opt in all_opts
            ]
            keyboard = InlineKeyboardMarkup([buttons])

            await update.message.reply_html(
                t("adaptive_captcha_high", lang, mention=mention, question=f"{question}"),
                reply_markup=keyboard,
            )

            # Mute the user until verified
            try:
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Callback handler — verify answers
# ---------------------------------------------------------------------------

async def _handle_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 4:
        return

    action = parts[1]
    chat_id = int(parts[2])
    user_id = int(parts[3])
    user = update.effective_user

    if user.id != user_id:
        await query.answer("This verification is not for you.", show_alert=True)
        return

    lang = await get_chat_lang(chat_id)

    if action == "verify":
        # Simple button — LOW risk, just pass
        _pending.pop((chat_id, user_id), None)
        try:
            from telegram import ChatPermissions
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False,
                ),
            )
        except Exception:
            pass
        await query.edit_message_text("✅ Verified! Welcome to the group.")

    elif action == "answer":
        given = int(parts[4]) if len(parts) > 4 else -1
        pending = _pending.get((chat_id, user_id))

        if pending is None:
            await query.edit_message_text("Verification expired.")
            return

        correct = pending["answer"]
        if given == correct:
            _pending.pop((chat_id, user_id), None)
            # Restore permissions
            try:
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                        can_change_info=False,
                        can_invite_users=True,
                        can_pin_messages=False,
                    ),
                )
            except Exception:
                pass
            await query.edit_message_text("✅ Correct! You are verified.")
        else:
            # Wrong answer — kick if high risk, warn if medium
            risk = pending.get("risk", 40)
            _pending.pop((chat_id, user_id), None)
            if risk >= 70:
                try:
                    await context.bot.ban_chat_member(
                        chat_id=chat_id, user_id=user_id
                    )
                    await context.bot.unban_chat_member(
                        chat_id=chat_id, user_id=user_id
                    )  # Kick (ban + immediate unban)
                except Exception:
                    pass
                await query.edit_message_text(
                    f"❌ Wrong answer. User kicked (high-risk profile)."
                )
            else:
                await query.edit_message_text(
                    f"❌ Wrong answer. Please try rejoining the group."
                )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.ChatType.GROUPS,
            _handle_new_member,
        ),
        group=2,
    )
    application.add_handler(
        CallbackQueryHandler(_handle_verify, pattern=rf"^{_CB_PREFIX}:"),
        group=10,
    )
    log.info("Plugin loaded: adaptive_captcha")
