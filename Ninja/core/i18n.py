"""
core/i18n.py — Internationalization (i18n) engine for the bot.

Design:
- All strings are located in locales/strings.py as a nested dictionary.
- t(key, **kwargs) is the only public interface.
- Default language: English (en).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

_catalogue: Optional[dict[str, dict[str, str]]] = None

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en",)
DEFAULT_LANG: str = "en"


def _get_catalogue() -> dict[str, dict[str, str]]:
    """Load the string catalogue on first use."""
    global _catalogue
    if _catalogue is None:
        from locales.strings import STRINGS
        _catalogue = STRINGS
    return _catalogue


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: object) -> str:
    """
    Return the translated string for the given key.

    If the key is not found, the key itself is returned as a signal to the developer.

    Args:
        key:     String key defined in locales/strings.py.
        lang:    Target language (default: en).
        **kwargs: Variables to be replaced via str.format_map.

    Returns:
        The formatted string.
    """
    catalogue = _get_catalogue()
    en_strings = catalogue.get("en", {})
    template: str = en_strings.get(key, key)

    if not kwargs:
        return template

    try:
        return template.format_map(kwargs)
    except (KeyError, IndexError) as exc:
        log.warning("i18n formatting error for key=%r: %s", key, exc)
        return template


async def get_chat_lang(chat_id: int) -> str:
    """Return the language code for the group — always 'en'."""
    return DEFAULT_LANG


def invalidate_lang_cache(chat_id: int) -> None:
    """Invalidate the language cache (no effect — language is fixed)."""
    pass


async def set_chat_lang(chat_id: int, lang_code: str) -> None:
    """
    Save language preference for the group.
    Only 'en' is accepted.
    """
    if lang_code not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Language not supported: {lang_code!r}")
