"""
economy/helpers.py — دوال مساعدة مشتركة لنظام الاقتصاد.
"""

from __future__ import annotations

import random
import string
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.game_wallet import get_wallet
from economy.models import BankAccount, EconomyStats


# ---------------------------------------------------------------------------
# EconomyStats helpers
# ---------------------------------------------------------------------------

async def get_stats(
    session: AsyncSession,
    user_id: int,
    first_name: str = "",
    username: Optional[str] = None,
) -> EconomyStats:
    """أرجع إحصائيات المستخدم — تُنشأ تلقائياً عند أول طلب."""
    result = await session.execute(
        select(EconomyStats).where(EconomyStats.user_id == user_id)
    )
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
# BankAccount helpers
# ---------------------------------------------------------------------------

async def _generate_account_number(session: AsyncSession) -> str:
    """توليد رقم حساب فريد من 10 أرقام."""
    while True:
        number = "".join(random.choices(string.digits, k=10))
        exists = await session.execute(
            select(BankAccount).where(BankAccount.account_number == number)
        )
        if exists.scalar_one_or_none() is None:
            return number


async def create_bank_account(
    session: AsyncSession,
    user_id: int,
    first_name: str,
    username: Optional[str],
) -> BankAccount:
    """أنشئ حساباً بنكياً جديداً — يرجع الحساب الموجود إن كان هناك واحد."""
    existing = await get_bank_account_by_user(session, user_id)
    if existing:
        return existing
    account_number = await _generate_account_number(session)
    account = BankAccount(
        user_id=user_id,
        account_number=account_number,
        owner_first_name=first_name,
        owner_username=username,
    )
    session.add(account)
    await session.flush()
    return account


async def get_bank_account_by_user(
    session: AsyncSession, user_id: int
) -> Optional[BankAccount]:
    result = await session.execute(
        select(BankAccount).where(BankAccount.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_bank_account_by_number(
    session: AsyncSession, account_number: str
) -> Optional[BankAccount]:
    result = await session.execute(
        select(BankAccount).where(BankAccount.account_number == account_number)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_user(first_name: str, username: Optional[str] = None) -> str:
    if username:
        return f"{first_name} (@{username})"
    return first_name


def fmt_coins(n: int) -> str:
    return f"{n:,}"
