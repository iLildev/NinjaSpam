"""
db/repositories/members.py — مستودع أعضاء المجموعة.

يوفّر واجهة نظيفة للعمليات على جدول ChatMember:
الحصول على العضو أو إنشائه، تحديث عدد التحذيرات، إلخ.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from database.engine import get_session
from database.models import ChatMember
from db.repositories.base import ensure_user_and_chat

log = logging.getLogger(__name__)


async def get_or_create(
    chat_id: int,
    user_id: int,
    *,
    chat_title: str = "",
    user_name: str = "",
) -> ChatMember:
    """
    أرجع سجل العضوية في المجموعة، أو أنشئه إن لم يكن موجوداً.
    يضمن وجود صفَّي User و Chat قبل الإنشاء.
    """
    async with get_session() as session:
        await ensure_user_and_chat(session, chat_id, user_id, chat_title, user_name)

        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            member = ChatMember(chat_id=chat_id, user_id=user_id)
            session.add(member)
            await session.flush()
        return member


async def get(chat_id: int, user_id: int) -> Optional[ChatMember]:
    """أرجع سجل العضوية أو None إن لم يُعثر عليه."""
    async with get_session() as session:
        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


async def set_warn_count(chat_id: int, user_id: int, count: int) -> None:
    """حدّث عداد التحذيرات لعضو معين."""
    async with get_session() as session:
        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member:
            member.warn_count = count
        log.debug("warn_count updated: chat=%d user=%d count=%d", chat_id, user_id, count)
