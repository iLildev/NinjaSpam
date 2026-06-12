"""
quiz/plugin.py — Guessing Game.

Commands:
  /quiz      — Random question from all categories
  /animequiz — Question from Anime category only
  /carquiz   — Question from Cars category only
  /endquiz   — End the current question (Admins only)

Mechanism:
  • Only one active question per group at a time.
  • The first user to type the correct answer wins 500 coins.
  • If the winner doesn't have a bank account, coins are added to their general wallet.
  • The question ends automatically after 45 seconds if no one answers.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.engine import get_session
from quiz.questions import QUESTIONS, Question

logger = logging.getLogger(__name__)

QUIZ_REWARD  = 500
QUIZ_TIMEOUT = 45

ANIME_QUESTIONS = [q for q in QUESTIONS if q["category"].startswith("Anime")]
CAR_QUESTIONS   = [q for q in QUESTIONS if q["category"].startswith("Cars")]

_ACTIVE_KEY = "quiz_active"


# ---------------------------------------------------------------------------
# Normalize Arabic and English text for comparison
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """
    Normalize text to compare answers:
    - Remove diacritics
    - Unify Alef (أ إ آ ٱ → ا)
    - Unify Yaa (ى → ي)
    - Unify Taa Marbuta (ة → ه)
    - Remove Tatweel (ـ)
    - Convert English to lowercase
    - Compress spaces
    """
    text = text.strip()
    # Remove diacritics
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    # Unify Alef
    text = re.sub(r'[أإآٱ]', 'ا', text)
    # Unify Yaa
    text = text.replace('ى', 'ي')
    # Unify Taa Marbuta
    text = text.replace('ة', 'ه')
    # Remove Tatweel
    text = text.replace('ـ', '')
    # English → lowercase
    text = text.lower()
    # Compress spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _is_correct(user_answer: str, answers: list[str]) -> bool:
    """Check if answer is correct with flexible normalization."""
    normed = _normalise(user_answer)
    if not normed:
        return False
    for ans in answers:
        if _normalise(ans) == normed:
            return True
    return False


# ---------------------------------------------------------------------------
# Start Question
# ---------------------------------------------------------------------------

async def _start_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pool: list[Question],
) -> None:
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("🎮 The game only works in groups!")
        return

    if context.chat_data.get(_ACTIVE_KEY):
        await update.message.reply_text(
            "⚠️ There is already an active question! Answer it or wait for the time to expire."
        )
        return

    if not pool:
        await update.message.reply_text("⚠️ No questions found in this category.")
        return

    question: Question = random.choice(pool)
    context.chat_data[_ACTIVE_KEY] = question

    context.job_queue.run_once(
        _timeout_quiz,
        when=QUIZ_TIMEOUT,
        chat_id=chat_id,
        data={"question": question},
        name=f"quiz_timeout_{chat_id}",
    )

    await update.message.reply_text(
        f"🎮 <b>Question — {question['category']}</b>\n\n"
        f"<i>{question['clue']}</i>\n\n"
        f"⏱️ You have <b>{QUIZ_TIMEOUT} seconds</b> to answer!\n"
        f"🏆 Winner gets <b>{QUIZ_REWARD:,} coins</b>",
        parse_mode="HTML",
    )


async def _timeout_quiz(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = context.job.chat_id
    question = context.job.data["question"]
    chat_data = context.application.chat_data.get(chat_id, {})

    if chat_data.get(_ACTIVE_KEY) is not None:
        stored = chat_data.pop(_ACTIVE_KEY, None)
        if stored is not None:
            correct = question["answers"][0]
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ <b>Time's up!</b>\n\n"
                    f"The correct answer was: <b>{correct}</b>\n"
                    f"Hint: {question['hint']}"
                ),
                parse_mode="HTML",
            )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, QUESTIONS)


async def cmd_animequiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, ANIME_QUESTIONS)


async def cmd_carquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, CAR_QUESTIONS)


async def cmd_endquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user   = update.effective_user
    chat   = update.effective_chat
    member = await chat.get_member(user.id)

    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Admins only.")
        return

    question: Optional[Question] = context.chat_data.pop(_ACTIVE_KEY, None)
    if not question:
        await update.message.reply_text("✅ There is no active question.")
        return

    jobs = context.job_queue.get_jobs_by_name(f"quiz_timeout_{chat.id}")
    for job in jobs:
        job.schedule_removal()

    await update.message.reply_text(
        f"🛑 <b>Question ended!</b>\n\n"
        f"The answer was: <b>{question['answers'][0]}</b>\n"
        f"Hint: {question['hint']}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Answer Handler
# ---------------------------------------------------------------------------

async def _check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == "private":
        return

    question: Optional[Question] = context.chat_data.get(_ACTIVE_KEY)
    if not question:
        return

    text = (update.effective_message.text or "").strip()
    if not text or text.startswith("/"):
        return

    if not _is_correct(text, question["answers"]):
        return

    # Correct answer!
    context.chat_data.pop(_ACTIVE_KEY, None)

    user = update.effective_user
    chat_id = update.effective_chat.id

    # Cancel timeout job
    jobs = context.job_queue.get_jobs_by_name(f"quiz_timeout_{chat_id}")
    for job in jobs:
        job.schedule_removal()

    # Grant reward
    async with get_session() as session:
        from core.game_wallet import add_coins
        wallet = await add_coins(session, user.id, QUIZ_REWARD)
        new_balance = wallet.coins
        reward_msg = f"💰 Balance: <b>{new_balance:,} coins</b>"

    await update.message.reply_text(
        f"🎉 <b>{user.first_name} got it right!</b>\n\n"
        f"✅ Answer: <b>{question['answers'][0]}</b>\n"
        f"🏆 Won <b>{QUIZ_REWARD:,} coins</b>!\n"
        f"{reward_msg}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Register Plugin
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("quiz",      cmd_quiz))
    application.add_handler(CommandHandler("animequiz", cmd_animequiz))
    application.add_handler(CommandHandler("carquiz",   cmd_carquiz))
    application.add_handler(CommandHandler("endquiz",   cmd_endquiz))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _check_answer),
        group=10,
    )
    logger.info("quiz plugin registered — 4 commands + answer handler.")
