"""
plugins/cas_check.py — Combot Anti-Spam (CAS) integration.

CAS (https://cas.chat) is a community-maintained database of confirmed
Telegram spammers built by the team behind Combot.  It currently tracks
over 4 million known spam accounts.  Checking a user against CAS on join
is the single highest-impact spam prevention measure available for free.

How it works:
  1. Every new member is silently checked against the CAS API.
  2. If the user is in the CAS database → immediate ban + notification.
  3. The check is non-blocking (aiohttp, 3s timeout).
  4. CAS bans are logged to the group's log channel.
  5. Per-group toggle: /cas on|off

CAS API endpoint:
  GET https://api.cas.chat/check?user_id=<user_id>
  Response: {"ok": true, "result": {"offenses": N, "messages": [...]}}
  ok=true  → user IS banned in CAS (spam confirmed)
  ok=false → user is clean

Commands:
  /cas <on|off>   — Enable or disable CAS checking for this group.
  /cas            — Show current CAS status.
  /cascheck [@u]  — Manually check a user against CAS.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp
from sqlalchemy import select
from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from core.log_channel import loggable
from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings

log = logging.getLogger(__name__)

_CAS_API = "https://api.cas.chat/check"
_TIMEOUT = aiohttp.ClientTimeout(total=3)

# ---------------------------------------------------------------------------
# CAS API client
# ---------------------------------------------------------------------------

async def _is_cas_banned(user_id: int) -> tuple[bool, int]:
    """
    Query the CAS API for *user_id*.

    Returns:
        (is_banned, offenses_count)
        On network error → (False, 0) so we fail open (don't block clean users).
    """
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(_CAS_API, params={"user_id": user_id}) as resp:
                if resp.status != 200:
                    return False, 0
                data = await resp.json()
                if data.get("ok"):
                    offenses = data.get("result", {}).get("offenses", 1)
                    return True, offenses
                return False, 0
    except Exception as exc:
        log.debug("CAS API error (fail-open): %s", exc)
        return False, 0


# ---------------------------------------------------------------------------
# Feature toggle helpers
# ---------------------------------------------------------------------------

async def _is_cas_enabled(chat_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(ChatFeatureSettings.cas_enabled)
            .where(ChatFeatureSettings.chat_id == chat_id)
        )
        row = result.scalar_one_or_none()
        return bool(row) if row is not None else True  # default: ON


async def _set_cas_enabled(chat_id: int, enabled: bool) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(ChatFeatureSettings).where(ChatFeatureSettings.chat_id == chat_id)
        )
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = ChatFeatureSettings(chat_id=chat_id, cas_enabled=enabled)
            session.add(settings)
        else:
            settings.cas_enabled = enabled


# ---------------------------------------------------------------------------
# Join handler — auto-ban CAS spammers
# ---------------------------------------------------------------------------

@loggable
async def _handle_new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Check every new member against CAS on join."""
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat or not msg.new_chat_members:
        return None

    if not await _is_cas_enabled(chat.id):
        return None

    for new_member in msg.new_chat_members:
        if new_member.is_bot:
            continue

        banned, offenses = await _is_cas_banned(new_member.id)
        if not banned:
            continue

        # Ban the spammer.
        try:
            await context.bot.ban_chat_member(chat.id, new_member.id)
        except (BadRequest, Forbidden) as exc:
            log.warning("CAS ban failed for %d in %d: %s", new_member.id, chat.id, exc)
            continue

        # Notify the group.
        name = new_member.full_name or str(new_member.id)
        try:
            await msg.reply_text(
                f"🚫 <b>CAS Ban</b>\n\n"
                f"User <a href='tg://user?id={new_member.id}'>{name}</a> "
                f"was automatically banned — found in the Combot Anti-Spam "
                f"database with <b>{offenses}</b> confirmed spam offense(s).\n\n"
                f"<i>Powered by <a href='https://cas.chat'>CAS</a></i>",
                parse_mode="HTML",
            )
        except (BadRequest, Forbidden):
            pass

        log_msg = (
            f"#CAS_BAN\n"
            f"User: <a href='tg://user?id={new_member.id}'>{name}</a> "
            f"(ID: <code>{new_member.id}</code>)\n"
            f"Offenses: {offenses}\n"
            f"Chat: {chat.title}"
        )
        return log_msg

    return None


# ---------------------------------------------------------------------------
# /cas command
# ---------------------------------------------------------------------------

@user_admin
async def cas_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Toggle CAS checking or show status."""
    chat = update.effective_chat
    msg = update.effective_message
    args = context.args or []

    if not args:
        enabled = await _is_cas_enabled(chat.id)
        status = "✅ Enabled" if enabled else "❌ Disabled"
        await msg.reply_text(
            f"🛡 <b>CAS (Combot Anti-Spam)</b>\n\n"
            f"Status: {status}\n\n"
            f"<i>CAS automatically bans users with confirmed spam history "
            f"across Telegram (4M+ known spammers).</i>\n\n"
            f"Use /cas on or /cas off to toggle.",
            parse_mode="HTML",
        )
        return

    arg = args[0].lower()
    if arg not in ("on", "off"):
        await msg.reply_text("Usage: /cas <on|off>")
        return

    enabled = arg == "on"
    await _set_cas_enabled(chat.id, enabled)
    icon = "✅" if enabled else "❌"
    await msg.reply_text(
        f"{icon} CAS protection <b>{'enabled' if enabled else 'disabled'}</b>.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /cascheck — manual lookup
# ---------------------------------------------------------------------------

async def cascheck_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Manually check a user ID or @username against CAS."""
    msg = update.effective_message
    args = context.args or []

    target_id: Optional[int] = None

    # From reply
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_id = msg.reply_to_message.from_user.id
    elif args:
        raw = args[0].lstrip("@")
        try:
            target_id = int(raw)
        except ValueError:
            # Try resolving username
            try:
                target_chat = await context.bot.get_chat(f"@{raw}")
                target_id = target_chat.id
            except Exception:
                await msg.reply_text("Could not resolve that user. Reply to a message or provide a numeric ID.")
                return

    if not target_id:
        await msg.reply_text("Reply to a user's message or provide their ID: /cascheck <id>")
        return

    sent = await msg.reply_text("🔍 Checking CAS database…")
    banned, offenses = await _is_cas_banned(target_id)

    if banned:
        text = (
            f"⚠️ <b>CAS Result: BANNED</b>\n\n"
            f"User ID: <code>{target_id}</code>\n"
            f"Offenses: <b>{offenses}</b>\n\n"
            f"This user is in the Combot Anti-Spam database.\n"
            f"<a href='https://cas.chat'>View profile on CAS</a>"
        )
    else:
        text = (
            f"✅ <b>CAS Result: CLEAN</b>\n\n"
            f"User ID: <code>{target_id}</code>\n"
            f"Not found in the CAS database."
        )

    try:
        await sent.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register CAS join handler and commands."""
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            _handle_new_member,
        ),
        group=0,  # Runs first — before CAPTCHA, before anything else.
    )
    application.add_handler(
        CommandHandler("cas", cas_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("cascheck", cascheck_cmd)
    )
    log.info("Plugin loaded: cas_check")
