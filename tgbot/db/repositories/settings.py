"""
db/repositories/settings.py — مستودع إعدادات المجموعة.

يوفّر واجهة Upsert نظيفة على جدول ChatFeatureSettings
وجداول الإعدادات الأخرى (LockSettings، AntiLinkSettings، إلخ).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from database.engine import get_session
from database.models import ChatFeatureSettings
from db.repositories.base import ensure_chat

log = logging.getLogger(__name__)


async def get_or_create(chat_id: int) -> ChatFeatureSettings:
    """
    أرجع إعدادات المجموعة، أو أنشئ صفاً افتراضياً إن لم يكن موجوداً.
    هذه هي نقطة الدخول الوحيدة للحصول على الإعدادات.
    """
    async with get_session() as session:
        await ensure_chat(session, chat_id)

        settings = await session.get(ChatFeatureSettings, chat_id)
        if settings is None:
            settings = ChatFeatureSettings(chat_id=chat_id)
            session.add(settings)
            await session.flush()
        return settings


async def get(chat_id: int) -> Optional[ChatFeatureSettings]:
    """أرجع الإعدادات أو None — بدون إنشاء."""
    async with get_session() as session:
        return await session.get(ChatFeatureSettings, chat_id)


async def update(chat_id: int, **fields) -> ChatFeatureSettings:
    """
    حدّث حقلاً أو أكثر في إعدادات المجموعة.

    مثال:
        await settings.update(chat_id, warn_limit=5, warn_action=WarnAction.BAN)
    """
    async with get_session() as session:
        await ensure_chat(session, chat_id)

        cfg = await session.get(ChatFeatureSettings, chat_id)
        if cfg is None:
            cfg = ChatFeatureSettings(chat_id=chat_id)
            session.add(cfg)
            await session.flush()

        for key, value in fields.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
            else:
                log.warning("ChatFeatureSettings لا يحتوي على الحقل: %s", key)

        log.debug("settings updated: chat=%d fields=%s", chat_id, list(fields.keys()))
        return cfg
