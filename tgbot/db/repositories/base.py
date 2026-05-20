"""
db/repositories/base.py — أدوات مشتركة بين جميع المستودعات.

توفّر دوال مساعدة لضمان وجود صفوف User و Chat في قاعدة البيانات
قبل إنشاء أي سجلات مرتبطة بها (Foreign Key safety).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Chat as ChatModel, User as UserModel


async def ensure_user(session: AsyncSession, user_id: int, name: str = "") -> None:
    """أنشئ صف المستخدم إن لم يكن موجوداً."""
    if not await session.get(UserModel, user_id):
        session.add(UserModel(id=user_id, first_name=name or str(user_id)))
        await session.flush()


async def ensure_chat(session: AsyncSession, chat_id: int, title: str = "") -> None:
    """أنشئ صف المجموعة إن لم يكن موجوداً."""
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title or str(chat_id)))
        await session.flush()


async def ensure_user_and_chat(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    chat_title: str = "",
    user_name: str = "",
) -> None:
    """أنشئ صفَّي المستخدم والمجموعة إن لم يكونا موجودَين."""
    await ensure_user(session, user_id, user_name)
    await ensure_chat(session, chat_id, chat_title)
