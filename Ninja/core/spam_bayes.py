"""
core/spam_bayes.py — Per-chat naive Bayes spam classifier (Task 6).

Architectural decisions:
- Raw token counts are stored in the ``BayesianToken`` table rather than a
  serialised classifier object.  This keeps training data portable, inspectable,
  and compatible with any Python version.
- An in-process TTLCache (5 min) reduces DB round-trips for high-traffic chats.
- The classifier abstains (returns None) when the corpus is smaller than
  ``BAYES_MIN_CORPUS_SIZE`` to avoid false-positive storms on fresh installs.
- Laplace add-1 smoothing prevents zero-probability tokens from dominating.
- All log-space arithmetic uses the log-sum-exp trick to prevent underflow.
"""

from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING, Any, Optional

from cachetools import TTLCache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache: chat_id → {token → (ham_count, spam_count)}
# ---------------------------------------------------------------------------

_TOKEN_CACHE: TTLCache[int, dict[str, tuple[int, int]]] = TTLCache(
    maxsize=256, ttl=300
)

# ---------------------------------------------------------------------------
# Stop words excluded from tokenization (common English function words)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "may", "might", "must", "can", "could", "this",
    "that", "these", "those", "i", "me", "my", "we", "our", "you", "your",
    "he", "she", "it", "they", "them", "his", "her", "its", "their",
})


# ---------------------------------------------------------------------------
# Public: tokenize
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """
    Normalise *text* into a list of lowercase word tokens.

    Processing pipeline:
    1. Strip HTTP/S URLs (noise for the classifier).
    2. Lowercase the remaining text.
    3. Remove all non-alphanumeric characters.
    4. Split on whitespace.
    5. Drop stop words and tokens shorter than 2 characters.

    Returns:
        List of normalised tokens; may be empty if the text has no content
        after stripping.
    """
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [
        tok for tok in text.split()
        if len(tok) >= 2 and tok not in _STOPWORDS
    ]


# ---------------------------------------------------------------------------
# Internal: cache management
# ---------------------------------------------------------------------------

def invalidate_cache(chat_id: int) -> None:
    """Remove *chat_id* token data from the in-memory cache after training."""
    _TOKEN_CACHE.pop(chat_id, None)


async def _load_tokens(
    chat_id: int,
    session: "AsyncSession",
) -> dict[str, tuple[int, int]]:
    """
    Return token frequency data for *chat_id*.

    Serves from the TTLCache when possible; otherwise fetches from the DB
    and populates the cache.

    Returns:
        Dict mapping token string → (ham_count, spam_count).
    """
    if chat_id in _TOKEN_CACHE:
        return _TOKEN_CACHE[chat_id]

    from sqlalchemy import select

    from database.models import BayesianToken

    result = await session.execute(
        select(BayesianToken).where(BayesianToken.chat_id == chat_id)
    )
    rows = result.scalars().all()
    data: dict[str, tuple[int, int]] = {
        row.token: (row.ham_count, row.spam_count) for row in rows
    }
    _TOKEN_CACHE[chat_id] = data
    return data


# ---------------------------------------------------------------------------
# Public: classify
# ---------------------------------------------------------------------------

async def classify(
    chat_id: int,
    text: str,
    total_ham: int,
    total_spam: int,
    threshold: float,
    min_corpus_size: int,
    session: "AsyncSession",
) -> Optional[float]:
    """
    Return the spam probability for *text* in the context of *chat_id*.

    The classifier abstains (returns ``None``) when:
    - The corpus has fewer than *min_corpus_size* labelled samples.
    - Either the spam or ham class has zero samples (impossible to build
      a prior for both classes).
    - The chat has no tokens in the database (untrained).

    Args:
        chat_id:        Telegram group chat ID.
        text:           Raw message text to evaluate.
        total_ham:      Total ham-labelled messages in this chat's corpus.
        total_spam:     Total spam-labelled messages in this chat's corpus.
        threshold:      Probability threshold (0.0–1.0) above which the
                        result is considered spam.  Not used here (caller
                        applies it) but logged for debugging.
        min_corpus_size: Minimum (ham + spam) count before predictions begin.
        session:        Active async SQLAlchemy session.

    Returns:
        Float probability in [0.0, 1.0], or ``None`` to abstain.
    """
    corpus_size = total_ham + total_spam
    if corpus_size < min_corpus_size:
        return None
    if total_spam == 0 or total_ham == 0:
        return None

    tokens = tokenize(text)
    if not tokens:
        return None

    token_data = await _load_tokens(chat_id, session)
    vocab_size = len(token_data)
    if vocab_size == 0:
        return None

    log_prior_spam = math.log(total_spam / corpus_size)
    log_prior_ham = math.log(total_ham / corpus_size)

    log_like_spam = 0.0
    log_like_ham = 0.0

    for token in tokens:
        ham_cnt, spam_cnt = token_data.get(token, (0, 0))
        p_token_spam = (spam_cnt + 1) / (total_spam + vocab_size)
        p_token_ham = (ham_cnt + 1) / (total_ham + vocab_size)
        log_like_spam += math.log(p_token_spam)
        log_like_ham += math.log(p_token_ham)

    log_spam = log_prior_spam + log_like_spam
    log_ham = log_prior_ham + log_like_ham

    # Log-sum-exp trick for numerical stability
    log_max = max(log_spam, log_ham)
    exp_spam = math.exp(log_spam - log_max)
    exp_ham = math.exp(log_ham - log_max)
    p_spam = exp_spam / (exp_spam + exp_ham)

    log.debug(
        "Bayes classify chat=%d p_spam=%.4f threshold=%.2f tokens=%d",
        chat_id, p_spam, threshold, len(tokens),
    )
    return p_spam


# ---------------------------------------------------------------------------
# Public: train
# ---------------------------------------------------------------------------

async def train(
    chat_id: int,
    text: str,
    is_spam: bool,
    session: "AsyncSession",
) -> int:
    """
    Update per-chat token frequency counts for a training sample.

    Each unique token in *text* has its ``spam_count`` or ``ham_count``
    incremented by 1.  The ``ChatFeatureSettings`` corpus counters are also
    bumped so that ``classify`` can track the global prior.

    After training, the in-memory cache for *chat_id* is invalidated so that
    the next classification uses fresh data from the database.

    Args:
        chat_id:  Telegram group chat ID.
        text:     Raw message text of the training sample.
        is_spam:  True to label the sample as spam; False for ham.
        session:  Active async SQLAlchemy session.

    Returns:
        Number of unique tokens processed from *text*.
    """
    from sqlalchemy import select

    from database.models import BayesianToken, ChatFeatureSettings

    tokens = tokenize(text)
    if not tokens:
        return 0

    unique_tokens = set(tokens)

    for token in unique_tokens:
        result = await session.execute(
            select(BayesianToken).where(
                BayesianToken.chat_id == chat_id,
                BayesianToken.token == token,
            )
        )
        row: Optional[BayesianToken] = result.scalar_one_or_none()
        if row is None:
            row = BayesianToken(chat_id=chat_id, token=token)
            session.add(row)

        if is_spam:
            row.spam_count += 1
        else:
            row.ham_count += 1

    feat_result = await session.execute(
        select(ChatFeatureSettings).where(
            ChatFeatureSettings.chat_id == chat_id
        )
    )
    feat: Optional[ChatFeatureSettings] = feat_result.scalar_one_or_none()
    if feat is not None:
        if is_spam:
            feat.bayes_spam_count += 1
        else:
            feat.bayes_ham_count += 1

    await session.commit()
    invalidate_cache(chat_id)
    log.info(
        "Bayes train chat=%d label=%s tokens=%d",
        chat_id, "spam" if is_spam else "ham", len(unique_tokens),
    )
    return len(unique_tokens)
