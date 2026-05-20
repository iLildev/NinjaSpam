"""
core/i18n.py — محرك التعريب (i18n) للبوت.

التصميم:
- جميع النصوص موجودة في locales/strings.py كقاموس متداخل.
- t(key, **kwargs) هي الواجهة العامة الوحيدة.
- اللغة الوحيدة والافتراضية: العربية (ar).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

_catalogue: Optional[dict[str, dict[str, str]]] = None

SUPPORTED_LANGUAGES: tuple[str, ...] = ("ar",)
DEFAULT_LANG: str = "ar"


def _get_catalogue() -> dict[str, dict[str, str]]:
    """تحميل كتالوج النصوص عند أول استخدام."""
    global _catalogue
    if _catalogue is None:
        from locales.strings import STRINGS
        _catalogue = STRINGS
    return _catalogue


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: object) -> str:
    """
    إرجاع النص المترجم للمفتاح المعطى باللغة العربية.

    إذا لم يُعثر على المفتاح يُعاد المفتاح نفسه كإشارة للمطوّر.

    المعاملات:
        key:     مفتاح النص المعرَّف في locales/strings.py.
        lang:    مُهمَل (مبقى للتوافق) — اللغة دائماً العربية.
        **kwargs: متغيرات تُستبدل بـ str.format_map.

    المُخرج:
        النص المنسَّق.
    """
    catalogue = _get_catalogue()
    ar_strings = catalogue.get("ar", {})
    template: str = ar_strings.get(key, key)

    if not kwargs:
        return template

    try:
        return template.format_map(kwargs)
    except (KeyError, IndexError) as exc:
        log.warning("خطأ في تنسيق i18n للمفتاح=%r: %s", key, exc)
        return template


async def get_chat_lang(chat_id: int) -> str:
    """إرجاع رمز اللغة للمجموعة — دائماً 'ar'."""
    return DEFAULT_LANG


def invalidate_lang_cache(chat_id: int) -> None:
    """إبطال ذاكرة التخزين المؤقت للغة (لا تأثير — اللغة ثابتة)."""
    pass


async def set_chat_lang(chat_id: int, lang_code: str) -> None:
    """
    حفظ تفضيل اللغة للمجموعة.
    اللغة الوحيدة المقبولة هي 'ar'.
    """
    if lang_code not in SUPPORTED_LANGUAGES:
        raise ValueError(f"اللغة غير مدعومة: {lang_code!r}")
