"""
db/repositories/bans.py — مستودع سجلات الحظر.

يوفّر واجهة نظيفة للقراءة والكتابة على جدول BanRecord
بدون أن تحتاج الـ plugins لكتابة SQL مباشرةً.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from database.engine import get_session
from database.models_extra import BanRecord
from db.repositories.base import ensure_user_and_chat

log = logging.getLogger(__name__)


async def record_ban(
    chat_id: int,
    user_id: int,
    banned_by: int,
    reason: str = "",
) -> None:
    """سجّل حظراً دائماً أو حدّث السجل القائم."""
    async with get_session() as session:
        await ensure_user_and_chat(session, chat_id, user_id)
        await ensure_user_and_chat(session, chat_id, banned_by)

        result = await session.execute(
            select(BanRecord).where(
                BanRecord.chat_id == chat_id,
                BanRecord.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            session.add(BanRecord(
                chat_id=chat_id,
                user_id=user_id,
                reason=reason or None,
                banned_by=banned_by,
                unbanned=False,
            ))
        else:
            record.unbanned = False
            record.reason = reason or record.reason
            record.banned_by = banned_by

    log.debug("ban recorded: chat=%d user=%d by=%d", chat_id, user_id, banned_by)


async def record_unban(chat_id: int, user_id: int) -> None:
    """ضع علامة 'رُفع الحظر' على السجل القائم."""
    async with get_session() as session:
        result = await session.execute(
            select(BanRecord).where(
                BanRecord.chat_id == chat_id,
                BanRecord.user_id == user_id,
                BanRecord.unbanned == False,  # noqa: E712
            )
        )
        record = result.scalar_one_or_none()
        if record:
            record.unbanned = True
            record.unbanned_at = datetime.now(tz=timezone.utc)

    log.debug("unban recorded: chat=%d user=%d", chat_id, user_id)


async def get_ban(chat_id: int, user_id: int) -> Optional[BanRecord]:
    """أرجع سجل الحظر النشط أو None."""
    async with get_session() as session:
        result = await session.execute(
            select(BanRecord).where(
                BanRecord.chat_id == chat_id,
                BanRecord.user_id == user_id,
                BanRecord.unbanned == False,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()
