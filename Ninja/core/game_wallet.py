"""
core/game_wallet.py — Shared wallet operations for all game plugins.

This module is an internal library that plugins import from instead of importing
from each other (prevents loading order issues).

Interface:
  get_wallet(session, user_id)           → Wallet
  add_coins(session, user_id, amount)    → Wallet
  deduct_coins(session, user_id, amount) → Wallet | None
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.game_models import Wallet

STARTING_COINS = 100


async def get_wallet(session: AsyncSession, user_id: int) -> Wallet:
    """Return user wallet — created automatically on first request."""
    result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(user_id=user_id, coins=STARTING_COINS, total_earned=STARTING_COINS)
        session.add(wallet)
        await session.flush()
    return wallet


async def add_coins(session: AsyncSession, user_id: int, amount: int) -> Wallet:
    """Add coins to user wallet and return the updated object."""
    wallet = await get_wallet(session, user_id)
    wallet.coins        += amount
    wallet.total_earned += amount
    return wallet


async def deduct_coins(session: AsyncSession, user_id: int, amount: int) -> Optional[Wallet]:
    """
    Deduct coins from user wallet.
    Return None if balance is insufficient (no deduction performed).
    """
    wallet = await get_wallet(session, user_id)
    if wallet.coins < amount:
        return None
    wallet.coins -= amount
    return wallet
