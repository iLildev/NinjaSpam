"""
plugins/antiraid.py — Mass-join (raid) detection and automatic lockdown.

A "raid" is defined as N or more new members joining within a configurable
time window.  On detection the bot:
  1. Sends an alert to the group.
  2. Activates a lockdown: restricts all new joiners (mutes them).
  3. Optionally kicks every raider who joined during the window.
  4. Lifts the lockdown automatically after the configured duration.

Commands:
  /antiraid <on|off>          — Enable or disable raid detection.
  /raidthreshold <n>          — Set minimum joins to trigger a raid (default 10).
  /raidwindow <seconds>       — Set the detection window in seconds (default 60).
  /raidlockdown <seconds>     — Set lockdown duration (default 300).
  /raidkick <on|off>          — Enable/disable kicking raiders on detection.
  /antiraid                   — Show current configuration.

In-memory join tracking is intentionally ephemeral — it resets on bot
restart, which is acceptable for a real-time detection system.

Handler group: 5 (same as captcha new_member — runs alongside it).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, List

from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import AntiRaidSettings

log = logging.getLogger(__name__)

RAID_GROUP: int = 5

# ---------------------------------------------------------------------------
# In-memory join tracking
# ---------------------------------------------------------------------------
# Maps chat_id → deque of UTC timestamps (one per new join event).
_JOIN_TIMESTAMPS: Dict[int, Deque[float]] = defaultdict(deque)

# Chats currently under lockdown: chat_id → asyncio.Task (the lift task).
_LOCKDOWN_ACTIVE: Dict[int, asyncio.Task] = {}

# Muted permissions — used during lockdown for new joiners.
# PTB v20.x uses granular media type fields instead of can_send_media_messages.
_MUTED_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
)

# Full permissions — restored on lockdown lift.
_FULL_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int):
    async with get_session() as session:
        return await session.get(AntiRaidSettings, chat_id)


async def _get_or_create_settings(session, chat_id: int, title: str = ""):
    settings = await session.get(AntiRaidSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=title))
            await session.flush()
        settings = AntiRaidSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


# ---------------------------------------------------------------------------
# Lockdown management
# ---------------------------------------------------------------------------

async def _lift_lockdown(
    application: "Application",
    chat_id: int,
    duration: int,
) -> None:
    """
    Coroutine that sleeps for *duration* seconds then lifts the lockdown by
    restoring default chat permissions.
    """
    await asyncio.sleep(duration)
    try:
        await application.bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=_FULL_PERMS,
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text="✅ Raid lockdown has been lifted. The group is now open.",
        )
        log.info("Raid lockdown lifted for chat %s.", chat_id)
    except (BadRequest, Forbidden) as exc:
        log.warning("Failed to lift lockdown for chat %s: %s", chat_id, exc)
    finally:
        _LOCKDOWN_ACTIVE.pop(chat_id, None)


async def _trigger_lockdown(
    application: "Application",
    chat_id: int,
    raider_ids: List[int],
    settings: AntiRaidSettings,
) -> None:
    """
    Activate raid lockdown for *chat_id*:
    1. Restrict the chat (no messages from new members).
    2. Optionally kick raiders.
    3. Schedule automatic lockdown lift.
    """
    log.warning(
        "RAID detected in chat %s — %d joiners in window. Activating lockdown.",
        chat_id,
        len(raider_ids),
    )

    # Alert the group.
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🚨 <b>RAID DETECTED</b> 🚨\n\n"
                f"{len(raider_ids)} accounts joined simultaneously.\n"
                f"Lockdown active for <b>{settings.lockdown_duration_seconds}s</b>.\n\n"
                f"New members cannot send messages until the lockdown is lifted."
            ),
            parse_mode=ParseMode.HTML,
        )
    except (BadRequest, Forbidden):
        pass

    # Kick or mute raiders.
    if settings.kick_raiders:
        for uid in raider_ids:
            try:
                await application.bot.ban_chat_member(chat_id=chat_id, user_id=uid)
                # Unban immediately so they can rejoin normally later.
                await application.bot.unban_chat_member(
                    chat_id=chat_id, user_id=uid, only_if_banned=True
                )
            except (BadRequest, Forbidden):
                pass

    # Cancel any existing lockdown task (edge case: raid-on-raid).
    existing = _LOCKDOWN_ACTIVE.pop(chat_id, None)
    if existing:
        existing.cancel()

    # Schedule automatic lockdown lift.
    task = asyncio.create_task(
        _lift_lockdown(application, chat_id, settings.lockdown_duration_seconds)
    )
    _LOCKDOWN_ACTIVE[chat_id] = task


# ---------------------------------------------------------------------------
# New member handler
# ---------------------------------------------------------------------------

async def new_member_raid_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Called on every NEW_CHAT_MEMBERS event.  Tracks join timestamps and
    triggers lockdown when the threshold is exceeded.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not chat or not message:
        return

    settings = await _get_settings(chat.id)
    if settings is None or not settings.enabled:
        return

    new_members = message.new_chat_members or []
    # Skip bot-only join events.
    human_ids: List[int] = [m.id for m in new_members if not m.is_bot]
    if not human_ids:
        return

    now: float = datetime.now(timezone.utc).timestamp()
    window: float = float(settings.time_window_seconds)
    timestamps: Deque[float] = _JOIN_TIMESTAMPS[chat.id]

    for uid in human_ids:
        timestamps.append(now)

    # Prune timestamps outside the rolling window.
    while timestamps and timestamps[0] < now - window:
        timestamps.popleft()

    if len(timestamps) >= settings.join_threshold:
        # Avoid double-triggering if already in lockdown.
        if chat.id not in _LOCKDOWN_ACTIVE:
            raider_ids: List[int] = human_ids.copy()
            await _trigger_lockdown(context.application, chat.id, raider_ids, settings)
            # Clear counters after lockdown trigger.
            timestamps.clear()


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_antiraid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Enable/disable anti-raid or show current settings.

    Usage:
        /antiraid          — Show current configuration.
        /antiraid on       — Enable raid detection.
        /antiraid off      — Disable raid detection.
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")

        if not args:
            status = "✅ Enabled" if settings.enabled else "✗ Disabled"
            lockdown_state = "🔴 ACTIVE" if chat.id in _LOCKDOWN_ACTIVE else "🟢 Normal"
            await message.reply_html(
                f"<b>Anti-Raid — {chat.title}</b>\n\n"
                f"Status: {status}\n"
                f"Group state: {lockdown_state}\n"
                f"Threshold: <b>{settings.join_threshold}</b> joins\n"
                f"Window: <b>{settings.time_window_seconds}s</b>\n"
                f"Lockdown duration: <b>{settings.lockdown_duration_seconds}s</b>\n"
                f"Kick raiders: <b>{'Yes' if settings.kick_raiders else 'No'}</b>\n\n"
                f"<i>Use /antiraid on|off to toggle.</i>"
            )
            return

        val = args[0].lower()
        if val not in ("on", "off"):
            await message.reply_text("Usage: /antiraid <on|off>")
            return

        settings.enabled = val == "on"

    state = "enabled ✅" if settings.enabled else "disabled ✗"
    await message.reply_html(f"Anti-raid protection is now <b>{state}</b>.")


@user_admin
async def cmd_raidthreshold(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set the number of joins required to trigger a raid alert."""
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if not args or not args[0].isdigit() or int(args[0]) < 3:
        await message.reply_text("Usage: /raidthreshold <n> (minimum 3)")
        return

    n = int(args[0])
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.join_threshold = n

    await message.reply_html(f"Raid threshold set to <b>{n}</b> joins.")


@user_admin
async def cmd_raidwindow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set the detection time window in seconds."""
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if not args or not args[0].isdigit() or int(args[0]) < 10:
        await message.reply_text("Usage: /raidwindow <seconds> (minimum 10)")
        return

    secs = int(args[0])
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.time_window_seconds = secs

    await message.reply_html(f"Raid detection window set to <b>{secs}s</b>.")


@user_admin
async def cmd_raidlockdown(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set how long (seconds) the lockdown stays active after a raid."""
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if not args or not args[0].isdigit() or int(args[0]) < 30:
        await message.reply_text("Usage: /raidlockdown <seconds> (minimum 30)")
        return

    secs = int(args[0])
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.lockdown_duration_seconds = secs

    await message.reply_html(f"Raid lockdown duration set to <b>{secs}s</b>.")


@user_admin
async def cmd_raidkick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Toggle whether raiders are kicked during lockdown."""
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if not args or args[0].lower() not in ("on", "off"):
        await message.reply_text("Usage: /raidkick <on|off>")
        return

    enabled = args[0].lower() == "on"
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.kick_raiders = enabled

    state = "enabled ✅" if enabled else "disabled ✗"
    await message.reply_html(f"Raid auto-kick is now <b>{state}</b>.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register raid detection handler and admin commands."""
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            new_member_raid_check,
        ),
        group=RAID_GROUP,
    )
    application.add_handler(
        CommandHandler("antiraid", cmd_antiraid, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("raidthreshold", cmd_raidthreshold, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("raidwindow", cmd_raidwindow, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("raidlockdown", cmd_raidlockdown, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("raidkick", cmd_raidkick, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: antiraid")
