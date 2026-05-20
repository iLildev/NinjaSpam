"""
plugins/antiastro.py — Anti-Astroturfing (coordinated spam detection).

Detects when multiple accounts simultaneously send nearly identical messages
— a hallmark of bot networks and coordinated spam campaigns.

Algorithm (sliding window):
  1. For every incoming message, compute a fingerprint (normalised text hash).
  2. Store (fingerprint → list of (user_id, timestamp)) in an in-memory
     sliding window (configurable window seconds, default 30s).
  3. When the same fingerprint appears from ≥ N distinct users within the
     window, trigger the configured action (warn / mute / ban) on all of them.
  4. Log the event and notify group admins via the log channel.

Per-chat toggle stored in ``AstroSettings`` (database).
In-memory state only — does not survive restarts (intentional: windows are
short enough that persistence is not worth the overhead).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models_extra import AstroSettings

log = logging.getLogger(__name__)

# Default thresholds (overridable via AstroSettings in future)
_DEFAULT_WINDOW_SECONDS: int = 30    # Sliding time window
_DEFAULT_MIN_USERS: int = 3          # Minimum distinct users to trigger
_DEFAULT_MIN_MSG_LEN: int = 20       # Ignore very short messages (< chars)
_DEFAULT_ACTION: str = "mute"        # warn | mute | ban


class _Entry(NamedTuple):
    user_id: int
    ts: float


# fingerprint → {chat_id → list[_Entry]}
_windows: dict[str, dict[int, list[_Entry]]] = defaultdict(lambda: defaultdict(list))


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def _fingerprint(text: str) -> str:
    """
    Compute a normalised fingerprint for *text*.

    Normalisation:
      - Strip whitespace and punctuation.
      - Collapse multiple spaces.
      - Lowercase.
      - MD5 of first 200 chars (speed vs. accuracy trade-off).
    """
    normalised = re.sub(r"[^\w\s]", "", text.lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()[:200]
    return hashlib.md5(normalised.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Action executor
# ---------------------------------------------------------------------------

async def _take_action(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_ids: list[int],
    action: str,
    lang: str,
    similarity: int,
) -> None:
    """Apply *action* to all *user_ids* in *chat_id*."""
    succeeded: list[int] = []

    for uid in user_ids:
        try:
            if action == "ban":
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=uid)
            elif action == "mute":
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=uid,
                    permissions=ChatPermissions(
                        can_send_messages=False,
                        can_send_polls=False,
                        can_send_other_messages=False,
                        can_add_web_page_previews=False,
                    ),
                )
            elif action == "warn":
                pass  # Warn system integration left to warn plugin
            succeeded.append(uid)
        except Exception as exc:
            log.debug("Astro action %s failed for user %d: %s", action, uid, exc)

    if not succeeded:
        return

    action_labels = {"ban": "banned", "mute": "muted", "warn": "warned", "ar": "محظور"}
    action_label = action_labels.get(action, action + "d")

    # Notify the group
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=t(
                "astro_detected", lang,
                count=len(succeeded),
                action=action_label,
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Notify admins via log channel if configured
    try:
        from database.models_extra import LogChannelSettings
        async with get_session() as session:
            log_res = await session.execute(
                select(LogChannelSettings).where(
                    LogChannelSettings.chat_id == chat_id
                )
            )
            log_setting = log_res.scalar_one_or_none()

        if log_setting:
            await context.bot.send_message(
                chat_id=log_setting.log_channel_id,
                text=t(
                    "astro_admin_notify", lang,
                    count=len(succeeded),
                    sim=similarity,
                    action=action_label,
                ),
                parse_mode="HTML",
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def _check_astroturfing(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Analyse each message for coordinated spam patterns."""
    if update.effective_message is None or update.effective_chat is None:
        return

    msg = update.effective_message
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user is None or user.is_bot:
        return

    text = msg.text or msg.caption or ""
    if len(text) < _DEFAULT_MIN_MSG_LEN:
        return

    # Per-chat toggle check
    async with get_session() as session:
        res = await session.execute(
            select(AstroSettings).where(AstroSettings.chat_id == chat_id)
        )
        setting = res.scalar_one_or_none()

    if setting is None or not setting.enabled:
        return

    # Admin messages are exempt
    from core.helpers.chat_status import is_user_admin
    if await is_user_admin(update.effective_chat, user.id):
        return

    fp = _fingerprint(text)
    now = time.monotonic()
    window_sec = setting.window_seconds if hasattr(setting, "window_seconds") else _DEFAULT_WINDOW_SECONDS
    min_users = setting.min_users if hasattr(setting, "min_users") else _DEFAULT_MIN_USERS
    action = setting.action if hasattr(setting, "action") else _DEFAULT_ACTION

    # Purge stale entries
    chat_window = _windows[fp][chat_id]
    chat_window = [e for e in chat_window if now - e.ts <= window_sec]
    _windows[fp][chat_id] = chat_window

    # Deduplication: update entry if same user already in window
    existing_user_ids = {e.user_id for e in chat_window}
    if user.id not in existing_user_ids:
        chat_window.append(_Entry(user_id=user.id, ts=now))
        _windows[fp][chat_id] = chat_window

    # Check threshold
    distinct_users = list({e.user_id for e in chat_window})
    if len(distinct_users) < min_users:
        return

    # Triggered — clear window to avoid repeated actions
    _windows[fp][chat_id] = []

    lang = await get_chat_lang(chat_id)
    log.warning(
        "Anti-astroturfing triggered in chat %d: %d users, fp=%s",
        chat_id, len(distinct_users), fp[:8],
    )

    await _take_action(
        context=context,
        chat_id=chat_id,
        user_ids=distinct_users,
        action=action,
        lang=lang,
        similarity=100,  # Same fingerprint = ~100% similarity
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & filters.ChatType.GROUPS & ~filters.COMMAND,
            _check_astroturfing,
        ),
        group=3,
    )
    log.info("Plugin loaded: antiastro")
