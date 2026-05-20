"""
core/rate_limiter.py — Outgoing rate-limiting helpers for mass Telegram operations.

Telegram's Bot API enforces hard limits:
  • 30 messages/second to different chats (global)
  • 20 messages/minute to the same group
  • 1 message/second to the same chat

When the bot performs bulk operations (fban across 100 groups, gban propagation,
mass kick during a raid) it must pace its API calls or Telegram will respond with
429 RetryAfter errors that cause PTB to enter exponential back-off, effectively
freezing the bot for several seconds.

This module provides:
  - RateLimitedSender — async context manager that throttles per-chat sends.
  - mass_operation() — convenience coroutine for iterating a list of chats
    with built-in pacing, per-item error handling, and progress reporting.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Iterable, Optional, TypeVar

from telegram.error import RetryAfter, TimedOut

log = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum delay (seconds) between consecutive outgoing API calls to ANY chat.
_GLOBAL_INTERVAL: float = 0.05   # 20 msgs/s — safely below the 30/s cap.

# Minimum delay between two consecutive messages to the SAME chat.
_PER_CHAT_INTERVAL: float = 1.05  # 1 msg/s per chat with a small buffer.

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_global_lock = asyncio.Lock()
_last_global_send: float = 0.0
_last_per_chat: dict[int, float] = defaultdict(float)
_per_chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------------------
# Core throttle
# ---------------------------------------------------------------------------

async def _throttle(chat_id: Optional[int] = None) -> None:
    """
    Sleep enough to respect both the global and per-chat rate limits.
    Must be called while holding ``_global_lock``.
    """
    import time

    now = time.monotonic()

    # Global interval
    global _last_global_send
    wait_global = _GLOBAL_INTERVAL - (now - _last_global_send)
    if wait_global > 0:
        await asyncio.sleep(wait_global)

    # Per-chat interval
    if chat_id is not None:
        now = asyncio.get_event_loop().time()
        wait_chat = _PER_CHAT_INTERVAL - (now - _last_per_chat[chat_id])
        if wait_chat > 0:
            await asyncio.sleep(wait_chat)
        _last_per_chat[chat_id] = asyncio.get_event_loop().time()

    _last_global_send = asyncio.get_event_loop().time()


async def safe_send(
    coro: Awaitable[T],
    chat_id: Optional[int] = None,
    *,
    retries: int = 3,
) -> Optional[T]:
    """
    Execute an awaitable (typically a Telegram API call) with throttling and
    automatic retry on RetryAfter / TimedOut errors.

    Args:
        coro:     The awaitable to execute (e.g. ``bot.ban_chat_member(...)``).
        chat_id:  Target chat ID for per-chat rate limiting.  Pass None for
                  calls that don't target a specific chat.
        retries:  Max number of automatic retries on transient errors.

    Returns:
        The result of the awaitable, or None if all retries were exhausted.
    """
    for attempt in range(retries + 1):
        async with _global_lock:
            await _throttle(chat_id)
        try:
            return await coro
        except RetryAfter as e:
            wait = e.retry_after + 0.5
            log.warning("RetryAfter %ss on chat %s — sleeping", wait, chat_id)
            await asyncio.sleep(wait)
        except TimedOut:
            log.warning("TimedOut on chat %s (attempt %d/%d)", chat_id, attempt + 1, retries + 1)
            await asyncio.sleep(2 ** attempt)
        except Exception as exc:
            log.debug("safe_send error on chat %s: %s", chat_id, exc)
            return None
    return None


# ---------------------------------------------------------------------------
# Mass operation helper
# ---------------------------------------------------------------------------

async def mass_operation(
    items: Iterable[Any],
    operation: Callable[[Any], Awaitable[Optional[str]]],
    *,
    description: str = "mass operation",
    batch_size: int = 25,
    batch_delay: float = 1.0,
) -> tuple[int, int]:
    """
    Run ``operation(item)`` for each item in *items* with pacing.

    Items are processed in batches of ``batch_size`` sequentially.
    Between batches, the coroutine sleeps ``batch_delay`` seconds to
    give Telegram's servers time to breathe.

    ``operation`` should return ``None`` on success or an error string on failure.

    Returns:
        (success_count, failure_count)
    """
    ok = 0
    fail = 0
    batch: list[Any] = []

    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            results = await asyncio.gather(
                *[operation(i) for i in batch], return_exceptions=True
            )
            for r in results:
                if r is None or r is True:
                    ok += 1
                else:
                    fail += 1
            log.debug("%s: batch done ok=%d fail=%d", description, ok, fail)
            batch = []
            await asyncio.sleep(batch_delay)

    # Last partial batch
    if batch:
        results = await asyncio.gather(
            *[operation(i) for i in batch], return_exceptions=True
        )
        for r in results:
            if r is None or r is True:
                ok += 1
            else:
                fail += 1

    log.info("%s complete: %d succeeded, %d failed", description, ok, fail)
    return ok, fail
