"""
core/helpers/fuzzy.py — Fuzzy matching engine for Arabic text.

Provides:
  normalize(text)            — Normalize text (remove diacritics, unify characters).
  similarity(a, b)           — Similarity ratio between two strings (0.0 – 1.0).
  levenshtein(a, b)          — Levenshtein distance (number of edits).
  is_close(a, b, threshold)  — Are the two strings close enough to accept?
  best_match(text, pool)     — Best match from a list of words.

Two-level matching methodology:
  1. SequenceMatcher ≥ 90%  → Direct match (comfortable for long words).
  2. Levenshtein = 1        → Match with exactly one error (comfortable for short words).
     Condition: Input text must be at least 3 characters to avoid random matches.

Normalization:
  - Removes diacritics (harakat) and tatweel.
  - Unifies Hamza shapes (أ/إ/آ/ء → ا).
  - Unifies Taa Marbuta (ة → ه) and Alif Maqsura (ى → ي).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

_DIACRITICS = re.compile(
    r"[\u0610-\u061A"   # Quranic marks
    r"\u064B-\u065F"    # Diacritics (fatha, damma, kasra...)
    r"\u0670"           # Superscript Alef
    r"\u06D6-\u06DC"    # Tajweed marks
    r"\u06DF-\u06E4"    # Tajweed marks
    r"\u06E7\u06E8"
    r"\u06EA-\u06ED]"
)

_HAMZA = re.compile(r"[أإآء]")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    Normalize Arabic text for flexible and spelling-error-insensitive comparison.

    Normalization steps:
      1. Remove diacritics and marks.
      2. Unify Alif Hamzas (أ/إ/آ/ء) → ا.
      3. Unify Taa Marbuta (ة) → ه.
      4. Unify Alif Maqsura (ى) → ي.
      5. Compress whitespace and strip edges.
      6. Convert to lowercase (for Latin text).
    """
    text = _DIACRITICS.sub("", text)
    text = _HAMZA.sub("ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text


def similarity(a: str, b: str) -> float:
    """Return similarity ratio (0.0 – 1.0) between two strings after normalization."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def levenshtein(a: str, b: str) -> int:
    """
    Return Levenshtein distance (minimum insertions/deletions/substitutions)
    between two strings. Operates on raw text (before or after normalization).

    Complexity: O(len(a) × len(b)) — perfectly acceptable for short words.
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
                prev[j + 1] + 1,   # deletion
                curr[j] + 1,        # insertion
                prev[j] + (ca != cb),  # substitution or match
            ))
        prev = curr
    return prev[-1]


def is_close(a: str, b: str, threshold: float = 0.90) -> bool:
    """
    Are the two strings close enough to accept?

    Double criterion:
      • SequenceMatcher ratio ≥ threshold  (suits long words).
      • Or Levenshtein distance = 1 with input text at least 3 characters
        (catches single errors in short words).
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
    Compare text against a list of words and return (best_word, ratio) if it passes the criterion.

    Uses is_close() (SequenceMatcher + Levenshtein) for each candidate.
    Returns the ratio from SequenceMatcher for display, even if it passed via Levenshtein.
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
            best_ratio = max(ratio, threshold)  # do not show ratio below threshold
            best_key = candidate

    if best_key is not None:
        return best_key, best_ratio
    return None


def build_lookup(
    entries: List[Dict],
    keyword_field: str = "keywords",
) -> Dict[str, int]:
    """
    Build a fast lookup dictionary: {normalized_keyword → index of entry in list}.

    Used to speed up matching from O(n*k) to direct lookup after normalization.
    """
    lookup: Dict[str, int] = {}
    for idx, entry in enumerate(entries):
        for kw in entry.get(keyword_field, []):
            lookup[normalize(kw)] = idx
    return lookup
