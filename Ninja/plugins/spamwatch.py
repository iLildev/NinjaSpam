"""
plugins/spamwatch.py — SpamWatch global spam database integration.

SpamWatch (https://spamwat.ch) is a community-maintained ban list.  When a
user joins a group with SpamWatch enabled, their ID is checked against the
API.  If they appear in the database they are automatically banned.

Configuration:
  SPAMWATCH_TOKEN — API token from @SpamWatchBot (set in .env)

Per-chat toggle is stored in ``SpamWatchSettings.enabled`` (database).
The API result is cached in a TTLCache (1 hour) to avoid repeated requests.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from cachetools import TTLCache
from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models_extra import SpamWatchSettings

log = logging.getLogger(__name__)

_SPAMWATCH_API = "https://notapi.spamwat.ch"  # Community mirror
_TOKEN: Optional[str] = os.getenv("SPAMWATCH_TOKEN")

# Cache: user_id → (is_banned: bool, reason: str)  — 1 hour TTL
_sw_cache: TTLCache[int, tuple[bool, str]] = TTLCache(maxsize=4096, ttl=3600)


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

async def _check_spamwatch(user_id: int) -> tuple[bool, str]:
    """
    Check whether *user_id* is in the SpamWatch database.

    Returns:
        (is_banned, reason) — reason is empty string when not banned.
    """
    if user_id in _sw_cache:
        return _sw_cache[user_id]

    if not _TOKEN:
        return False, ""

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_SPAMWATCH_API}/banlist/{user_id}",
                headers={"Authorization": f"Bearer {_TOKEN}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            reason = data.get("reason", "Listed in SpamWatch database")
            result = (True, reason)
        elif resp.status_code == 404:
            result = (False, "")
        else:
            log.warning("SpamWatch API returned status %d for user %d", resp.status_code, user_id)
            result = (False, "")
    except Exception as exc:
        log.warning("SpamWatch API error: %s", exc)
        result = (False, "")

    _sw_cache[user_id] = result
    return result


# ---------------------------------------------------------------------------
# New member handler
# ---------------------------------------------------------------------------

async def _check_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Check each new member against SpamWatch on join.

    Skips:
    - Chats with SpamWatch disabled.
    - Bot accounts.
    - The bot itself.
    """
    if update.message is None or update.effective_chat is None:
        return

    chat_id = update.effective_chat.id

    # Check per-chat toggle
    async with get_session() as session:
        result = await session.execute(
            select(SpamWatchSettings).where(SpamWatchSettings.chat_id == chat_id)
        )
        sw_setting = result.scalar_one_or_none()

    if sw_setting is None or not sw_setting.enabled:
        return

    if not _TOKEN:
        return

    bot_id = context.bot.id
    lang = await get_chat_lang(chat_id)

    for tg_user in update.message.new_chat_members:
        if tg_user.is_bot or tg_user.id == bot_id:
            continue

        is_banned, reason = await _check_spamwatch(tg_user.id)
        if not is_banned:
            continue

        # Ban the user
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=tg_user.id)
            log.info(
                "SpamWatch: banned user %d in chat %d (reason: %s)",
                tg_user.id, chat_id, reason,
            )
        except Exception as exc:
            log.warning("SpamWatch: could not ban user %d: %s", tg_user.id, exc)
            continue

        # Notify the group
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=t(
                    "spamwatch_banned", lang,
                    user_id=tg_user.id,
                    name=tg_user.full_name,
                    reason=reason,
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.ChatType.GROUPS,
            _check_new_member,
        ),
        group=2,  # Runs early — before welcome message
    )
    log.info("Plugin loaded: spamwatch (token=%s)", "configured" if _TOKEN else "NOT SET")
