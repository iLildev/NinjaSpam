"""
core/helpers/fuzzy.py — محرك المطابقة الضبابية للنص العربي.

يوفّر:
  normalize(text)            — تطبيع النص (إزالة التشكيل، توحيد الحروف).
  similarity(a, b)           — نسبة التشابه بين نصَّين (0.0 – 1.0).
  levenshtein(a, b)          — مسافة Levenshtein (عدد التعديلات).
  is_close(a, b, threshold)  — هل النصان متقاربان بما يكفي للقبول؟
  best_match(text, pool)     — أفضل تطابق من قائمة كلمات.

منهجية المطابقة ذات المستويين:
  1. SequenceMatcher ≥ 90%  → تطابق مباشر (مريح للكلمات الطويلة).
  2. Levenshtein = 1        → تطابق بخطأ واحد فقط (مريح للكلمات القصيرة).
     الشرط: النص المُدخَل لا يقل عن 3 أحرف لتجنّب التطابقات العشوائية.

التطبيع:
  - يُزيل التشكيل (حركات) والمدّ.
  - يوحّد أشكال الهمزة (أ/إ/آ/ء → ا).
  - يوحّد التاء المربوطة (ة → ه) والألف المقصورة (ى → ي).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

_DIACRITICS = re.compile(
    r"[\u0610-\u061A"   # علامات قرآنية
    r"\u064B-\u065F"    # تشكيل (فتحة، ضمة، كسرة...)
    r"\u0670"           # ألف فوقية
    r"\u06D6-\u06DC"    # علامات تلاوة
    r"\u06DF-\u06E4"    # علامات تلاوة
    r"\u06E7\u06E8"
    r"\u06EA-\u06ED]"
)

_HAMZA = re.compile(r"[أإآء]")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    طبّع النص العربي لمقارنة مرنة وغير حساسة للأخطاء الإملائية الشائعة.

    خطوات التطبيع:
      1. إزالة التشكيل والحركات.
      2. توحيد همزات الألف (أ/إ/آ/ء) → ا.
      3. توحيد التاء المربوطة (ة) → ه.
      4. توحيد الألف المقصورة (ى) → ي.
      5. ضغط المسافات وإزالة الحواف.
      6. التحويل للأحرف الصغيرة (للنصوص اللاتينية).
    """
    text = _DIACRITICS.sub("", text)
    text = _HAMZA.sub("ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text


def similarity(a: str, b: str) -> float:
    """أرجع نسبة التشابه (0.0 – 1.0) بين نصَّين بعد تطبيعهما."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def levenshtein(a: str, b: str) -> int:
    """
    أرجع مسافة Levenshtein (الحد الأدنى من الإدراجات/الحذوفات/الاستبدالات)
    بين نصَّين. يعمل على النص الخام (قبل التطبيع أو بعده).

    التعقيد: O(len(a) × len(b)) — مقبول تماماً للكلمات القصيرة.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,   # حذف
                curr[j] + 1,        # إدراج
                prev[j] + (ca != cb),  # استبدال أو مطابقة
            ))
        prev = curr
    return prev[-1]


def is_close(a: str, b: str, threshold: float = 0.90) -> bool:
    """
    هل النصان متقاربان بما يكفي للقبول؟

    المعيار المزدوج:
      • SequenceMatcher ratio ≥ threshold  (يناسب الكلمات الطويلة).
      • أو Levenshtein distance = 1 مع نص مدخَل لا يقل عن 3 أحرف
        (يضبط الأخطاء المفردة في الكلمات القصيرة).
    """
    na, nb = normalize(a), normalize(b)
    if SequenceMatcher(None, na, nb).ratio() >= threshold:
        return True
    if len(na) >= 3 and levenshtein(na, nb) == 1:
        return True
    return False


def best_match(
    text: str,
    pool: List[str],
    threshold: float = 0.90,
) -> Optional[Tuple[str, float]]:
    """
    قارن النص ضد قائمة كلمات وأرجع (أفضل_كلمة، النسبة) إن اجتاز المعيار.

    يستخدم is_close() (SequenceMatcher + Levenshtein) لكل مرشّح.
    يُرجع النسبة من SequenceMatcher للعرض، حتى لو نجح الفحص عبر Levenshtein.
    """
    norm_text = normalize(text)
    best_key: Optional[str] = None
    best_ratio = 0.0

    for candidate in pool:
        norm_cand = normalize(candidate)
        ratio = SequenceMatcher(None, norm_text, norm_cand).ratio()
        close = (
            ratio >= threshold
            or (len(norm_text) >= 3 and levenshtein(norm_text, norm_cand) == 1)
        )
        if close and ratio > best_ratio:
            best_ratio = max(ratio, threshold)  # لا تعرض نسبة أقل من العتبة
            best_key = candidate

    if best_key is not None:
        return best_key, best_ratio
    return None


def build_lookup(
    entries: List[Dict],
    keyword_field: str = "keywords",
) -> Dict[str, int]:
    """
    ابنِ قاموس بحث سريع: {normalized_keyword → فهرس entry في القائمة}.

    يُستخدم لتسريع المطابقة من O(n*k) إلى استعلام مباشر بعد التطبيع.
    """
    lookup: Dict[str, int] = {}
    for idx, entry in enumerate(entries):
        for kw in entry.get(keyword_field, []):
            lookup[normalize(kw)] = idx
    return lookup
