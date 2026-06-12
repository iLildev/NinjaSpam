"""
economy/heist.py — Group Bank Heist.

Commands:
  /rob     — Start a heist (in group, requires bank account)
  /joinrob — Join a heist (within 60 seconds)

Mechanics:
  • Initiator starts the heist, 60 seconds to join.
  • At least 2 participants or it's cancelled.
  • Success 65% (increases with participants) → each gets 300-700 coins.
  • Failure 35% → all participants go to jail for one hour.
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
# Job: execute the heist after 60 seconds
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
                text="❌ <b>Heist cancelled!</b>\n\nNot enough participants.",
                parse_mode="HTML",
            )
            return

        # Increase success chance with participants
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
                    f"🎉 <b>Heist successful!</b>\n\n"
                    f"Participants:\n{names_txt}\n\n"
                    f"💰 Everyone received <b>{fmt_coins(loot)} coins</b>!"
                ),
                parse_mode="HTML",
            )
        else:
            heist.status = "failed"
            names = []
            for p in participants:
                await jail_user(
                    session, p.user_id,
                    reason="Caught while robbing the bank!",
                    duration_minutes=HEIST_JAIL_MINUTES,
                    bail=HEIST_BAIL,
                )
                names.append(fmt_user(p.first_name, p.username))

            names_txt = "\n".join(f"• {n}" for n in names)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🚔 <b>Heist failed!</b>\n\n"
                    f"The following were arrested:\n{names_txt}\n\n"
                    f"🔒 Everyone is in jail for one hour!\n"
                    f"Bail: <b>{fmt_coins(HEIST_BAIL)} coins</b> — use /bail"
                ),
                parse_mode="HTML",
            )


# ---------------------------------------------------------------------------
# /rob  — Start Heist
# ---------------------------------------------------------------------------

async def cmd_rob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ You must have a bank account to participate in a heist.\n"
                "Open an account with /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await update.message.reply_text("🔒 You are in jail — you cannot organize a heist!")
            return

        active_r = await session.execute(
            select(HeistSession).where(
                HeistSession.chat_id == chat_id,
                HeistSession.status == "recruiting",
            )
        )
        if active_r.scalar_one_or_none():
            await update.message.reply_text(
                "⚠️ There is already a heist in progress! Join with /joinrob"
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
        f"🦹 <b>{user.first_name} is planning a bank heist!</b>\n\n"
        f"Join the heist with /joinrob within <b>60 seconds</b>!\n\n"
        f"⚠️ You need a bank account — /openbank\n"
        f"🎯 Success: Everyone gets 300-700 coins\n"
        f"🚔 Failure: Everyone goes to jail for an hour!",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /joinrob  — Join Heist
# ---------------------------------------------------------------------------

async def cmd_joinrob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ You must have a bank account to participate.\n"
                "Open an account with /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await update.message.reply_text("🔒 You are in jail — you cannot join the heist!")
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
                "❌ No heist in progress right now.\n"
                "Start a heist with /rob"
            )
            return

        existing_r = await session.execute(
            select(HeistParticipant).where(
                HeistParticipant.session_id == heist.id,
                HeistParticipant.user_id == user.id,
            )
        )
        if existing_r.scalar_one_or_none():
            await update.message.reply_text("✅ You are already in the heist team!")
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
        f"✅ <b>{user.first_name} joined the heist!</b>\n"
        f"👥 Participants now: <b>{count}</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("rob",     cmd_rob))
    application.add_handler(CommandHandler("joinrob", cmd_joinrob))
