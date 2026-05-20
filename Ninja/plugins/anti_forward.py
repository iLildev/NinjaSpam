"""
plugins/anti_forward.py — Anti-Forwarded Messages Filter (GroupHelp feature).

Prevents non-admins from forwarding messages into the group from external
chats.  Optionally, forwarding from specific whitelisted channels/groups
can be allowed.

Commands (admin only):
  /antiforward on|off        — Enable or disable the filter.
  /antiforward status        — Show current configuration.
  /antiforward allow <chat>  — Whitelist a channel/group (@username or ID).
  /antiforward deny  <chat>  — Remove a channel/group from the whitelist.
  /antiforward list          — List all whitelisted sources.

When enabled, any message that is a forward from an external chat is
automatically deleted.  Users from approved chats are exempt.
Admins are always exempt.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import delete, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models_extra import AntiForwardSettings, AntiForwardWhitelist

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[AntiForwardSettings]:
    async with get_session() as session:
        return await session.get(AntiForwardSettings, chat_id)


async def _get_whitelist(chat_id: int) -> list[int]:
    async with get_session() as session:
        result = await session.execute(
            select(AntiForwardWhitelist.source_chat_id).where(
                AntiForwardWhitelist.chat_id == chat_id
            )
        )
        return [r[0] for r in result.all()]


# ---------------------------------------------------------------------------
# /antiforward
# ---------------------------------------------------------------------------

@user_admin
async def antiforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main /antiforward command dispatcher."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args:
        await message.reply_text(
            "Usage:\n"
            "/antiforward on|off — toggle\n"
            "/antiforward status — show config\n"
            "/antiforward allow <@channel or id> — whitelist\n"
            "/antiforward deny <@channel or id> — remove whitelist\n"
            "/antiforward list — show whitelist"
        )
        return

    sub = args[0].lower()

    if sub in ("on", "off"):
        enabled = sub == "on"
        async with get_session() as session:
            row = await session.get(AntiForwardSettings, chat.id)
            if row is None:
                row = AntiForwardSettings(chat_id=chat.id, enabled=enabled)
                session.add(row)
            else:
                row.enabled = enabled
            await session.commit()
        state = "✅ enabled" if enabled else "❌ disabled"
        await message.reply_text(f"Anti-forward filter is now {state}.")

    elif sub == "status":
        settings = await _get_settings(chat.id)
        whitelist = await _get_whitelist(chat.id)
        if not settings:
            await message.reply_text("Anti-forward filter: ❌ Not configured. Use /antiforward on to enable.")
            return
        state = "✅ Active" if settings.enabled else "❌ Disabled"
        wl = "\n".join(f"• <code>{cid}</code>" for cid in whitelist) or "None"
        await message.reply_html(
            f"<b>Anti-Forward Filter</b>\n\n"
            f"<b>Status:</b> {state}\n"
            f"<b>Whitelisted sources:</b>\n{wl}"
        )

    elif sub == "allow" and len(args) >= 2:
        ref = args[1]
        try:
            target = await context.bot.get_chat(ref)
        except BadRequest as exc:
            await message.reply_text(f"Couldn't find that chat: {exc.message}")
            return
        async with get_session() as session:
            exists = await session.execute(
                select(AntiForwardWhitelist).where(
                    AntiForwardWhitelist.chat_id == chat.id,
                    AntiForwardWhitelist.source_chat_id == target.id,
                )
            )
            if exists.scalar_one_or_none():
                await message.reply_text("That chat is already whitelisted.")
                return
            session.add(AntiForwardWhitelist(
                chat_id=chat.id,
                source_chat_id=target.id,
                source_title=target.title or str(target.id),
            ))
            await session.commit()
        await message.reply_html(
            f"✅ Forwards from <b>{target.title or target.id}</b> are now allowed."
        )

    elif sub == "deny" and len(args) >= 2:
        ref = args[1]
        try:
            target = await context.bot.get_chat(ref)
            source_id = target.id
        except BadRequest:
            # Allow numeric IDs directly.
            try:
                source_id = int(ref)
            except ValueError:
                await message.reply_text("Provide a @username or numeric chat ID.")
                return

        async with get_session() as session:
            result = await session.execute(
                delete(AntiForwardWhitelist).where(
                    AntiForwardWhitelist.chat_id == chat.id,
                    AntiForwardWhitelist.source_chat_id == source_id,
                ).returning(AntiForwardWhitelist.source_chat_id)
            )
            await session.commit()
        if result.rowcount:
            await message.reply_text(f"✅ Removed {source_id} from the whitelist.")
        else:
            await message.reply_text("That chat wasn't in the whitelist.")

    elif sub == "list":
        whitelist = await _get_whitelist(chat.id)
        if not whitelist:
            await message.reply_text("No chats are whitelisted — all forwards are blocked (when enabled).")
        else:
            lines = "\n".join(f"• <code>{cid}</code>" for cid in whitelist)
            await message.reply_html(f"<b>Whitelisted forward sources:</b>\n{lines}")

    else:
        await message.reply_text("Unknown sub-command. Use /antiforward for usage info.")


# ---------------------------------------------------------------------------
# Enforcement handler
# ---------------------------------------------------------------------------

async def enforce_antiforward(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Delete forwarded messages from non-whitelisted sources."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not user or not message or not chat:
        return
    if user.is_bot:
        return

    # Only act on forwarded messages.
    is_forward = bool(
        message.forward_origin
        or message.forward_from
        or message.forward_from_chat
        or message.forward_sender_name
    )
    if not is_forward:
        return

    # Admins are exempt.
    if await is_user_admin(chat, user.id):
        return

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        return

    # Check whitelist.
    forward_chat_id: Optional[int] = None
    if message.forward_from_chat:
        forward_chat_id = message.forward_from_chat.id

    if forward_chat_id is not None:
        whitelist = await _get_whitelist(chat.id)
        if forward_chat_id in whitelist:
            return

    # Delete the forwarded message.
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    try:
        notice = await context.bot.send_message(
            chat_id=chat.id,
            text=f"🚫 {user.mention_html()}, forwarding messages is not allowed here.",
            parse_mode=ParseMode.HTML,
        )
        import asyncio
        await asyncio.sleep(5)
        await notice.delete()
    except (BadRequest, Forbidden):
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("antiforward", antiforward_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.FORWARDED,
            enforce_antiforward,
        ),
        group=7,
    )
    log.info("Plugin loaded: anti_forward")
