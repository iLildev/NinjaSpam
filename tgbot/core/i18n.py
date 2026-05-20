"""
core/i18n.py — Lightweight internationalisation (i18n) engine.

Design:
- All translated strings live in ``locales/strings.py`` as a nested dict
  keyed by language code → string key → template string.
- ``t(key, lang, **kwargs)`` is the only public API.  It falls back to
  English automatically when a key is missing in the requested language.
- Chat language is stored in ``ChatLanguage`` (database) and cached in a
  TTLCache to avoid per-message DB hits.
- Supported languages: en, ar, fa, tr, ru, id  (easily extensible).
"""

from __future__ import annotations

import logging
from typing import Optional

from cachetools import TTLCache

log = logging.getLogger(__name__)

# Import the string catalogue (populated lazily to avoid circular imports)
_catalogue: Optional[dict[str, dict[str, str]]] = None

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "ar", "fa", "tr", "ru", "id", "fr", "zh")
DEFAULT_LANG: str = "en"

# Cache: chat_id → lang code  (5-minute TTL, 2048 chats max)
_lang_cache: TTLCache[int, str] = TTLCache(maxsize=2048, ttl=300)


def _get_catalogue() -> dict[str, dict[str, str]]:
    """Lazily load the string catalogue on first access."""
    global _catalogue
    if _catalogue is None:
        from locales.strings import STRINGS
        _catalogue = STRINGS
    return _catalogue


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: object) -> str:
    """
    Return the localised string for *key* in *lang*.

    Falls back to English when the key is absent in the requested language.
    Falls back to the key itself when it's absent in English too (signals a
    missing translation so developers can spot it immediately).

    Args:
        key:    String key defined in ``locales/strings.py``.
        lang:   BCP-47 language code (e.g. "ar", "en", "ru").
        **kwargs: Template variables substituted with ``str.format_map``.

    Returns:
        Formatted, localised string.
    """
    catalogue = _get_catalogue()
    lang_dict = catalogue.get(lang) or catalogue.get(DEFAULT_LANG, {})
    template: str = lang_dict.get(key) or catalogue.get(DEFAULT_LANG, {}).get(key, key)

    if not kwargs:
        return template

    try:
        return template.format_map(kwargs)
    except (KeyError, IndexError) as exc:
        log.warning("i18n format error for key=%r lang=%r: %s", key, lang, exc)
        return template


async def get_chat_lang(chat_id: int) -> str:
    """
    Return the language code configured for *chat_id*.

    Serves from the in-process TTL cache when possible; otherwise queries
    the database.  Returns ``DEFAULT_LANG`` when no preference is set.
    """
    if chat_id in _lang_cache:
        return _lang_cache[chat_id]

    try:
        from sqlalchemy import select
        from database.engine import get_session
        from database.models_extra import ChatLanguage

        async with get_session() as session:
            result = await session.execute(
                select(ChatLanguage.lang_code).where(ChatLanguage.chat_id == chat_id)
            )
            row = result.scalar_one_or_none()
            lang = row if row in SUPPORTED_LANGUAGES else DEFAULT_LANG

    except Exception as exc:
        log.warning("get_chat_lang error for chat %d: %s", chat_id, exc)
        lang = DEFAULT_LANG

    _lang_cache[chat_id] = lang
    return lang


def invalidate_lang_cache(chat_id: int) -> None:
    """Remove chat from language cache after a language change."""
    _lang_cache.pop(chat_id, None)


async def set_chat_lang(chat_id: int, lang_code: str) -> None:
    """
    Persist the language preference for *chat_id* and invalidate the cache.

    Creates the row if it doesn't exist (upsert).
    """
    if lang_code not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {lang_code!r}")

    from sqlalchemy import select
    from database.engine import get_session
    from database.models_extra import ChatLanguage

    async with get_session() as session:
        result = await session.execute(
            select(ChatLanguage).where(ChatLanguage.chat_id == chat_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ChatLanguage(chat_id=chat_id, lang_code=lang_code)
            session.add(row)
        else:
            row.lang_code = lang_code
        await session.commit()

    invalidate_lang_cache(chat_id)
