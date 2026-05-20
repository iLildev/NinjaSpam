"""
economy/heist.py — السطو الجماعي على البنك.

الأوامر:
  /rob     — ابدأ سطواً (في المجموعة، يحتاج حساباً بنكياً)
  /joinrob — انضم للسطو (خلال 60 ثانية)

الميكانيكية:
  • المبادر يبدأ السطو، 60 ثانية للانضمام.
  • لا يقل عن 2 مشاركين وإلا يُلغى.
  • نجاح 65% (يرتفع مع عدد المشاركين) → كل واحد يحصل على 300-700 عملة.
  • فشل 35% → كل المشاركين يذهبون للسجن ساعة كاملة.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.game_wallet import add_coins
from database.engine import get_session
from economy.helpers import (
    fmt_coins,
    fmt_user,
    get_bank_account_by_user,
    is_jailed,
    jail_user,
)
from economy.models import HeistParticipant, HeistSession

logger = logging.getLogger(__name__)

HEIST_WAIT_SECONDS = 60
HEIST_LOOT_MIN     = 300
HEIST_LOOT_MAX     = 700
HEIST_JAIL_MINUTES = 60
HEIST_BAIL         = 200


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Job: تنفيذ السطو بعد 60 ثانية
# ---------------------------------------------------------------------------

async def _execute_heist(context: ContextTypes.DEFAULT_TYPE) -> None:
    data     = context.job.data
    chat_id  = data["chat_id"]
    heist_id = data["heist_id"]

    async with get_session() as session:
        r = await session.execute(
            select(HeistSession).where(HeistSession.id == heist_id)
        )
        heist = r.scalar_one_or_none()
        if heist is None or heist.status != "recruiting":
            return

        parts_r = await session.execute(
            select(HeistParticipant).where(HeistParticipant.session_id == heist_id)
        )
        participants = parts_r.scalars().all()

        if len(participants) < 2:
            heist.status = "cancelled"
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ <b>السطو أُلغي!</b>\n\nما في كافٍ من المشاركين.",
                parse_mode="HTML",
            )
            return

        # زيادة احتمال النجاح مع المشاركين
        base_chance = 0.65
        bonus       = min(0.15, (len(participants) - 2) * 0.05)
        success     = random.random() < (base_chance + bonus)

        if success:
            loot = random.randint(HEIST_LOOT_MIN, HEIST_LOOT_MAX)
            heist.status          = "success"
            heist.loot_per_person = loot

            names = []
            for p in participants:
                await add_coins(session, p.user_id, loot)
                names.append(fmt_user(p.first_name, p.username))

            names_txt = "\n".join(f"• {n}" for n in names)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 <b>السطو نجح!</b>\n\n"
                    f"المشاركون:\n{names_txt}\n\n"
                    f"💰 كل واحد حصل على <b>{fmt_coins(loot)} عملة</b>!"
                ),
                parse_mode="HTML",
            )
        else:
            heist.status = "failed"
            names = []
            for p in participants:
                await jail_user(
                    session, p.user_id,
                    reason="القبض عليك أثناء السطو على البنك!",
                    duration_minutes=HEIST_JAIL_MINUTES,
                    bail=HEIST_BAIL,
                )
                names.append(fmt_user(p.first_name, p.username))

            names_txt = "\n".join(f"• {n}" for n in names)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🚔 <b>السطو فشل!</b>\n\n"
                    f"تم إلقاء القبض على:\n{names_txt}\n\n"
                    f"🔒 الجميع في السجن لمدة ساعة!\n"
                    f"الكفالة: <b>{fmt_coins(HEIST_BAIL)} عملة</b> — استخدم /bail"
                ),
                parse_mode="HTML",
            )


# ---------------------------------------------------------------------------
# /rob  — ابدأ السطو
# ---------------------------------------------------------------------------

async def cmd_rob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل في المجموعات فقط!")
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ يجب أن يكون لديك حساب بنكي للمشاركة في السطو.\n"
                "افتح حساباً بـ /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await update.message.reply_text("🔒 أنت في السجن — ما تقدر تنظم سطواً!")
            return

        active_r = await session.execute(
            select(HeistSession).where(
                HeistSession.chat_id == chat_id,
                HeistSession.status == "recruiting",
            )
        )
        if active_r.scalar_one_or_none():
            await update.message.reply_text(
                "⚠️ يوجد سطو جارٍ بالفعل! انضم بـ /joinrob"
            )
            return

        heist = HeistSession(
            chat_id=chat_id,
            started_by_id=user.id,
            started_by_name=user.first_name,
            status="recruiting",
        )
        session.add(heist)
        await session.flush()

        participant = HeistParticipant(
            session_id=heist.id,
            user_id=user.id,
            first_name=user.first_name,
            username=user.username,
        )
        session.add(participant)
        heist_id = heist.id

    context.job_queue.run_once(
        _execute_heist,
        when=HEIST_WAIT_SECONDS,
        data={"chat_id": chat_id, "heist_id": heist_id},
        name=f"heist_{chat_id}",
    )

    await update.message.reply_text(
        f"🦹 <b>{user.first_name} يخطط لسطو على البنك!</b>\n\n"
        f"انضم للسطو بـ /joinrob خلال <b>60 ثانية</b>!\n\n"
        f"⚠️ تحتاج حساباً بنكياً — /openbank\n"
        f"🎯 نجاح: كل واحد يحصل 300-700 عملة\n"
        f"🚔 فشل: الجميع يروح السجن ساعة!",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /joinrob  — انضم للسطو
# ---------------------------------------------------------------------------

async def cmd_joinrob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل في المجموعات فقط!")
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ يجب أن يكون لديك حساب بنكي للمشاركة.\n"
                "افتح حساباً بـ /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await update.message.reply_text("🔒 أنت في السجن — ما تقدر تشارك في السطو!")
            return

        heist_r = await session.execute(
            select(HeistSession).where(
                HeistSession.chat_id == chat_id,
                HeistSession.status == "recruiting",
            )
        )
        heist = heist_r.scalar_one_or_none()
        if not heist:
            await update.message.reply_text(
                "❌ لا يوجد سطو جارٍ الآن.\n"
                "ابدأ سطواً بـ /rob"
            )
            return

        existing_r = await session.execute(
            select(HeistParticipant).where(
                HeistParticipant.session_id == heist.id,
                HeistParticipant.user_id == user.id,
            )
        )
        if existing_r.scalar_one_or_none():
            await update.message.reply_text("✅ أنت بالفعل في فريق السطو!")
            return

        participant = HeistParticipant(
            session_id=heist.id,
            user_id=user.id,
            first_name=user.first_name,
            username=user.username,
        )
        session.add(participant)

        count_r = await session.execute(
            select(HeistParticipant).where(HeistParticipant.session_id == heist.id)
        )
        count = len(count_r.scalars().all()) + 1

    await update.message.reply_text(
        f"✅ <b>{user.first_name} انضم للسطو!</b>\n"
        f"👥 المشاركون الآن: <b>{count}</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("rob",     cmd_rob))
    application.add_handler(CommandHandler("joinrob", cmd_joinrob))
