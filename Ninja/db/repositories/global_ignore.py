"""
db/repositories/global_ignore.py — Repository for global ignore list.
"""

from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select, delete

from database.engine import get_session
from database.models_extra import GlobalIgnore

log = logging.getLogger(__name__)


async def is_ignored(user_id: int) -> bool:
    """Check if a user is globally ignored."""
    async with get_session() as session:
        result = await session.execute(
            select(GlobalIgnore).where(GlobalIgnore.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None


async def add(user_id: int, reason: str = "") -> bool:
    """Add a user to the global ignore list."""
    async with get_session() as session:
        existing = await session.get(GlobalIgnore, user_id)
        if existing:
            return False
        
        session.add(GlobalIgnore(user_id=user_id, reason=reason))
        await session.commit()
        return True


async def remove(user_id: int) -> bool:
    """Remove a user from the global ignore list."""
    async with get_session() as session:
        obj = await session.get(GlobalIgnore, user_id)
        if not obj:
            return False
        
        await session.delete(obj)
        await session.commit()
        return True


async def get_all() -> Sequence[GlobalIgnore]:
    """Get all globally ignored users."""
    async with get_session() as session:
        result = await session.execute(select(GlobalIgnore))
        return result.scalars().all()
