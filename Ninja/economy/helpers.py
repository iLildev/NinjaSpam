"""
economy/helpers.py — Common helper functions for the economy system.
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update

from core.game_wallet import get_wallet
from economy.models import BankAccount, EconomyStats, JailRecord, LoanRecord


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# EconomyStats
# ---------------------------------------------------------------------------

async def get_stats(
    session: AsyncSession,
    user_id: int,
    first_name: str = "",
    username: Optional[str] = None,
) -> EconomyStats:
    result = await session.execute(select(EconomyStats).where(EconomyStats.user_id == user_id))
    stats = result.scalar_one_or_none()
    if stats is None:
        stats = EconomyStats(
            user_id=user_id,
            first_name=first_name or str(user_id),
            username=username,
        )
        session.add(stats)
        await session.flush()
    else:
        if first_name:
            stats.first_name = first_name
        if username is not None:
            stats.username = username
    return stats


# ---------------------------------------------------------------------------
# BankAccount
# ---------------------------------------------------------------------------

async def _generate_account_number(session: AsyncSession) -> str:
    while True:
        number = "".join(random.choices(string.digits, k=10))
        exists = await session.execute(
            select(BankAccount).where(BankAccount.account_number == number)
        )
        if exists.scalar_one_or_none() is None:
            return number


async def create_bank_account(
    session: AsyncSession, user_id: int, first_name: str, username: Optional[str],
) -> BankAccount:
    existing = await get_bank_account_by_user(session, user_id)
    if existing:
        return existing
    account = BankAccount(
        user_id=user_id,
        account_number=await _generate_account_number(session),
        owner_first_name=first_name,
        owner_username=username,
    )
    session.add(account)
    await session.flush()
    return account


async def get_bank_account_by_user(session: AsyncSession, user_id: int) -> Optional[BankAccount]:
    r = await session.execute(select(BankAccount).where(BankAccount.user_id == user_id))
    return r.scalar_one_or_none()


async def get_bank_account_by_number(session: AsyncSession, number: str) -> Optional[BankAccount]:
    r = await session.execute(select(BankAccount).where(BankAccount.account_number == number))
    return r.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Jail helpers
# ---------------------------------------------------------------------------

async def get_jail(session: AsyncSession, user_id: int) -> Optional[JailRecord]:
    r = await session.execute(select(JailRecord).where(JailRecord.user_id == user_id))
    return r.scalar_one_or_none()


async def is_jailed(session: AsyncSession, user_id: int) -> bool:
    """Return True if the user is currently in jail."""
    jail = await get_jail(session, user_id)
    if jail is None:
        return False
    if not jail.is_active:
        jail.is_released = True
        return False
    return True


async def jail_user(
    session: AsyncSession,
    user_id: int,
    reason: str,
    duration_minutes: int = 60,
    bail: int = 300,
) -> JailRecord:
    """Put the user in jail — if already there, update duration."""
    existing = await get_jail(session, user_id)
    if existing and existing.is_active:
        return existing
    if existing:
        await session.delete(existing)
        await session.flush()
    jail = JailRecord(
        user_id=user_id,
        reason=reason,
        bail_amount=bail,
        jailed_at=_utcnow(),
        auto_release_at=_utcnow() + timedelta(minutes=duration_minutes),
        is_released=False,
    )
    session.add(jail)
    await session.flush()
    return jail


async def release_user(session: AsyncSession, user_id: int) -> None:
    jail = await get_jail(session, user_id)
    if jail:
        jail.is_released = True


# ---------------------------------------------------------------------------
# Loan helpers
# ---------------------------------------------------------------------------

async def get_active_loan(session: AsyncSession, user_id: int) -> Optional[LoanRecord]:
    r = await session.execute(
        select(LoanRecord).where(LoanRecord.user_id == user_id, LoanRecord.is_repaid == False)
    )
    return r.scalar_one_or_none()


async def auto_jail_if_overdue(
    session: AsyncSession, user_id: int
) -> Optional[JailRecord]:
    """
    If the user has an overdue loan, put them in jail automatically.
    Called before every earning command.
    """
    loan = await get_active_loan(session, user_id)
    if loan and loan.is_overdue:
        return await jail_user(
            session, user_id,
            reason=f"Failed to repay a loan of {loan.remaining:,} coins",
            duration_minutes=120,
            bail=300,
        )
    return None


# ---------------------------------------------------------------------------
# Check jail + send message (convenience for handlers)
# ---------------------------------------------------------------------------

async def check_jailed_and_reply(update: Update, session: AsyncSession, user_id: int) -> bool:
    """
    Check jail status and send a message if jailed.
    Returns True if jailed (handler should stop).
    """
    await auto_jail_if_overdue(session, user_id)
    if await is_jailed(session, user_id):
        jail = await get_jail(session, user_id)
        await update.message.reply_text(
            f"🔒 <b>You are in jail!</b>\n\n"
            f"Reason: {jail.reason}\n"
            f"Release in: <b>{jail.time_left_str}</b>\n\n"
            f"Or pay bail <b>{jail.bail_amount:,} coins</b> with /bail",
            parse_mode="HTML",
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_user(first_name: str, username: Optional[str] = None) -> str:
    if username:
        return f"{first_name} (@{username})"
    return first_name


def fmt_coins(n: int) -> str:
    return f"{n:,}"
