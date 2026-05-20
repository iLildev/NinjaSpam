"""
quiz/plugin.py — لعبة التخمين.

الأوامر:
  /quiz      — سؤال عشوائي من كل الفئات
  /animequiz — سؤال من فئة الأنمي فقط
  /carquiz   — سؤال من فئة السيارات فقط
  /endquiz   — إنهاء السؤال الحالي (المشرفون فقط)

الآلية:
  • سؤال واحد نشط في كل مجموعة في نفس الوقت.
  • المستخدم الأول الذي يكتب الإجابة الصحيحة يفوز بـ 500 عملة.
  • ينتهي السؤال تلقائياً بعد 45 ثانية إذا لم يجب أحد.
  • لا يُشترط وجود حساب بنكي للمشاركة.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.game_wallet import add_coins
from database.engine import get_session
from quiz.questions import QUESTIONS, Question

logger = logging.getLogger(__name__)

QUIZ_REWARD  = 500
QUIZ_TIMEOUT = 45

ANIME_QUESTIONS = [q for q in QUESTIONS if q["category"].startswith("أنمي")]
CAR_QUESTIONS   = [q for q in QUESTIONS if q["category"].startswith("سيارات")]

# مفتاح تخزين السؤال النشط في chat_data
_ACTIVE_KEY = "quiz_active"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """توحيد النص لمقارنة الإجابات — صغّر + أزل مسافات زائدة."""
    return text.strip().lower().replace("  ", " ")


async def _start_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pool: list[Question],
) -> None:
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("🎮 اللعبة تعمل في المجموعات فقط!")
        return

    if context.chat_data.get(_ACTIVE_KEY):
        await update.message.reply_text(
            "⚠️ يوجد سؤال نشط بالفعل! أجب عليه أو انتظر انتهاء الوقت."
        )
        return

    question: Question = random.choice(pool)
    context.chat_data[_ACTIVE_KEY] = question

    # جدوِل انتهاء الوقت
    context.job_queue.run_once(
        _timeout_quiz,
        when=QUIZ_TIMEOUT,
        chat_id=chat_id,
        data={"question": question},
        name=f"quiz_timeout_{chat_id}",
    )

    await update.message.reply_text(
        f"🎮 <b>سؤال — {question['category']}</b>\n\n"
        f"<i>{question['clue']}</i>\n\n"
        f"⏱️ لديك <b>{QUIZ_TIMEOUT} ثانية</b> للإجابة!\n"
        f"🏆 الفائز يحصل على <b>{QUIZ_REWARD:,} عملة</b>",
        parse_mode="HTML",
    )


async def _timeout_quiz(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = context.job.chat_id
    question = context.job.data["question"]
    chat_data = context.application.chat_data.get(chat_id, {})

    if chat_data.get(_ACTIVE_KEY) == question:
        chat_data.pop(_ACTIVE_KEY, None)
        correct = question["answers"][0]
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏰ <b>انتهى الوقت!</b>\n\n"
                f"الإجابة الصحيحة كانت: <b>{correct}</b>\n"
                f"تلميح: {question['hint']}"
            ),
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# /quiz  — سؤال عشوائي
# ---------------------------------------------------------------------------

async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, QUESTIONS)


# ---------------------------------------------------------------------------
# /animequiz
# ---------------------------------------------------------------------------

async def cmd_animequiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, ANIME_QUESTIONS)


# ---------------------------------------------------------------------------
# /carquiz
# ---------------------------------------------------------------------------

async def cmd_carquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_quiz(update, context, CAR_QUESTIONS)


# ---------------------------------------------------------------------------
# /endquiz  — إنهاء السؤال (المشرفون)
# ---------------------------------------------------------------------------

async def cmd_endquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user   = update.effective_user
    chat   = update.effective_chat
    member = await chat.get_member(user.id)

    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return

    question: Optional[Question] = context.chat_data.pop(_ACTIVE_KEY, None)
    if not question:
        await update.message.reply_text("✅ لا يوجد سؤال نشط حالياً.")
        return

    # إلغاء جدول المهلة
    jobs = context.job_queue.get_jobs_by_name(f"quiz_timeout_{chat.id}")
    for job in jobs:
        job.schedule_removal()

    await update.message.reply_text(
        f"🛑 <b>انتهى السؤال!</b>\n\n"
        f"الإجابة كانت: <b>{question['answers'][0]}</b>\n"
        f"تلميح: {question['hint']}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# معالج الرسائل — التحقق من الإجابات
# ---------------------------------------------------------------------------

async def _check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == "private":
        return

    question: Optional[Question] = context.chat_data.get(_ACTIVE_KEY)
    if not question:
        return

    user    = update.effective_user
    text    = update.effective_message.text or ""
    normed  = _normalise(text)
    correct = any(_normalise(ans) == normed for ans in question["answers"])

    if not correct:
        return

    # إجابة صحيحة!
    context.chat_data.pop(_ACTIVE_KEY, None)

    # إلغاء المهلة
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"quiz_timeout_{chat_id}")
    for job in jobs:
        job.schedule_removal()

    async with get_session() as session:
        wallet = await add_coins(session, user.id, QUIZ_REWARD)

    await update.message.reply_text(
        f"🎉 <b>{user.first_name} أجاب صح!</b>\n\n"
        f"✅ الإجابة: <b>{question['answers'][0]}</b>\n"
        f"🏆 ربح <b>{QUIZ_REWARD:,} عملة</b>!\n"
        f"💰 رصيده الآن: <b>{wallet.coins:,} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Registration
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
