"""
db/repositories/warns.py — مستودع سجلات التحذيرات.

يوفّر واجهة نظيفة لإضافة التحذيرات وعدّها وحذفها
بدون أن تحتوي الـ plugins على استعلامات SQL مباشرة.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, or_, select

from database.engine import get_session
from database.models import ChatFeatureSettings, WarnEntry
from db.repositories.base import ensure_user_and_chat

log = logging.getLogger(__name__)


async def add(
    chat_id: int,
    user_id: int,
    issued_by: int,
    reason: str = "",
) -> tuple[int, int]:
    """
    أضف تحذيراً جديداً وأرجع (العدد الحالي، الحد الأقصى).

    تُحسب التحذيرات غير المنتهية الصلاحية فقط.
    """
    async with get_session() as session:
        await ensure_user_and_chat(session, chat_id, user_id)
        await ensure_user_and_chat(session, chat_id, issued_by)

        cfg = await session.get(ChatFeatureSettings, chat_id)
        warn_limit: int = cfg.warn_limit if cfg else 3
        expiry_days: int = (cfg.warn_expiry_days or 0) if cfg else 0

        expires_at: Optional[datetime] = None
        if expiry_days > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

        session.add(WarnEntry(
            chat_id=chat_id,
            user_id=user_id,
            reason=reason or None,
            issued_by_id=issued_by,
            expires_at=expires_at,
        ))
        await session.flush()

        count = await _count_active(session, chat_id, user_id)

    log.debug("warn added: chat=%d user=%d count=%d/%d", chat_id, user_id, count, warn_limit)
    return count, warn_limit


async def count(chat_id: int, user_id: int) -> tuple[int, int]:
    """أرجع (العدد الحالي، الحد الأقصى) للتحذيرات النشطة."""
    async with get_session() as session:
        cfg = await session.get(ChatFeatureSettings, chat_id)
        warn_limit: int = cfg.warn_limit if cfg else 3
        active = await _count_active(session, chat_id, user_id)
    return active, warn_limit


async def list_entries(chat_id: int, user_id: int) -> List[WarnEntry]:
    """أرجع جميع سجلات التحذيرات لمستخدم في مجموعة."""
    async with get_session() as session:
        result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat_id,
                WarnEntry.user_id == user_id,
            ).order_by(WarnEntry.created_at)
        )
        return list(result.scalars().all())


async def remove_latest(chat_id: int, user_id: int) -> bool:
    """احذف أحدث تحذير للمستخدم. يُرجع True إن وُجد سجل للحذف."""
    async with get_session() as session:
        result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat_id,
                WarnEntry.user_id == user_id,
            ).order_by(WarnEntry.created_at.desc()).limit(1)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False
        await session.delete(entry)
        return True


async def clear_all(chat_id: int, user_id: int) -> int:
    """احذف جميع التحذيرات للمستخدم. يُرجع العدد المحذوف."""
    async with get_session() as session:
        result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat_id,
                WarnEntry.user_id == user_id,
            )
        )
        entries = result.scalars().all()
        count_deleted = len(entries)
        for entry in entries:
            await session.delete(entry)
    log.debug("warns cleared: chat=%d user=%d count=%d", chat_id, user_id, count_deleted)
    return count_deleted


# ---------------------------------------------------------------------------
# دوال داخلية
# ---------------------------------------------------------------------------

async def _count_active(session, chat_id: int, user_id: int) -> int:
    """عدّ التحذيرات غير المنتهية الصلاحية (يُستخدم داخل session قائم)."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(WarnEntry).where(
            WarnEntry.chat_id == chat_id,
            WarnEntry.user_id == user_id,
            or_(
                WarnEntry.expires_at.is_(None),
                WarnEntry.expires_at > now,
            ),
        )
    )
    return len(result.scalars().all())
