"""
plugins/channel_protect.py — Channel forward protection.

Blocks messages forwarded from Telegram channels that are not on the
per-group whitelist.  Admins can add/remove channels via commands or the
settings panel.

Commands (admin-only):
  /chanprotect on|off          — Toggle protection for this group.
  /allowchan <@username|id>    — Add a channel to the whitelist.
  /removechan <@username|id>   — Remove a channel from the whitelist.
  /chans                       — List whitelisted channels.

The whitelist check is applied in message group 1 (before most plugins)
so that protected content never reaches other handlers.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, delete as sql_delete
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models_extra import ChannelWhitelist, ChannelProtectSettings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _is_protection_enabled(chat_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(ChannelProtectSettings.enabled).where(
                ChannelProtectSettings.chat_id == chat_id
            )
        )
        row = result.scalar_one_or_none()
        return bool(row) if row is not None else False


async def _get_whitelist(chat_id: int) -> list[int]:
    async with get_session() as session:
        result = await session.execute(
            select(ChannelWhitelist.channel_id).where(
                ChannelWhitelist.chat_id == chat_id
            )
        )
        return [r[0] for r in result.all()]


async def _resolve_channel(bot, identifier: str) -> Optional[tuple[int, str]]:
    """
    Resolve a channel @username or numeric ID to (channel_id, title).

    Returns None on failure.
    """
    try:
        if identifier.lstrip("-").isdigit():
            chat = await bot.get_chat(int(identifier))
        else:
            chat = await bot.get_chat(identifier)
        return chat.id, chat.title or identifier
    except Exception as exc:
        log.warning("Could not resolve channel %r: %s", identifier, exc)
        return None


# ---------------------------------------------------------------------------
# Message filter — runs on every message
# ---------------------------------------------------------------------------

async def _enforce_protection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete messages forwarded from non-whitelisted channels."""
    if update.effective_message is None or update.effective_chat is None:
        return

    msg = update.effective_message
    chat_id = update.effective_chat.id

    # Check if message is forwarded from a channel
    forward_origin = msg.forward_origin
    if forward_origin is None:
        return

    # Only care about channel forwards (forward_chat not None)
    fwd_chat_id: Optional[int] = None
    if hasattr(forward_origin, "chat") and forward_origin.chat:
        fwd_chat_id = forward_origin.chat.id
    if fwd_chat_id is None:
        return

    if not await _is_protection_enabled(chat_id):
        return

    # Check admin — admins are exempt
    from core.helpers.chat_status import is_user_admin
    if update.effective_user and await is_user_admin(
        update.effective_chat, update.effective_user.id
    ):
        return

    # Check whitelist
    whitelist = await _get_whitelist(chat_id)
    if fwd_chat_id in whitelist:
        return

    # Block: delete the message and notify
    try:
        await msg.delete()
    except Exception:
        pass

    lang = await get_chat_lang(chat_id)
    try:
        notice = await context.bot.send_message(
            chat_id=chat_id,
            text=t("chanprotect_blocked", lang),
        )
        # Auto-delete notice after 10 seconds
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=chat_id, message_id=notice.message_id),
            when=10,
            name=f"cp_notice_{chat_id}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_chanprotect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chanprotect on|off"""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    args = context.args or []

    if not args or args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text(
            "Usage: /chanprotect on|off"
        )
        return

    enable = args[0].lower() == "on"

    async with get_session() as session:
        result = await session.execute(
            select(ChannelProtectSettings).where(
                ChannelProtectSettings.chat_id == chat_id
            )
        )
        setting = result.scalar_one_or_none()
        if setting is None:
            setting = ChannelProtectSettings(chat_id=chat_id, enabled=enable)
            session.add(setting)
        else:
            setting.enabled = enable

    state = t("enabled", lang) if enable else t("disabled", lang)
    await update.effective_message.reply_text(
        f"📡 Channel protection {state}."
    )


@user_admin
async def cmd_allowchan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/allowchan <@username|id>"""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    args = context.args or []

    # Also accept forwarded messages to identify the channel
    channel_id: Optional[int] = None
    channel_name: str = ""

    if update.effective_message.forward_origin and hasattr(
        update.effective_message.forward_origin, "chat"
    ):
        origin = update.effective_message.forward_origin
        if origin.chat:
            channel_id = origin.chat.id
            channel_name = origin.chat.title or str(channel_id)
    elif args:
        resolved = await _resolve_channel(context.bot, args[0])
        if resolved:
            channel_id, channel_name = resolved

    if channel_id is None:
        await update.effective_message.reply_text(
            "Could not resolve channel. Provide @username, ID, or forward a message from the channel."
        )
        return

    async with get_session() as session:
        # Check if already in whitelist
        existing = await session.execute(
            select(ChannelWhitelist).where(
                ChannelWhitelist.chat_id == chat_id,
                ChannelWhitelist.channel_id == channel_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(ChannelWhitelist(
                chat_id=chat_id,
                channel_id=channel_id,
                channel_title=channel_name,
            ))

    await update.effective_message.reply_html(
        t("chanprotect_added", lang, channel=channel_name)
    )


@user_admin
async def cmd_removechan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removechan <@username|id>"""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    args = context.args or []

    if not args:
        await update.effective_message.reply_text("Usage: /removechan <@username|id>")
        return

    resolved = await _resolve_channel(context.bot, args[0])
    if not resolved:
        await update.effective_message.reply_text("Could not resolve channel.")
        return

    channel_id, _ = resolved
    async with get_session() as session:
        await session.execute(
            sql_delete(ChannelWhitelist).where(
                ChannelWhitelist.chat_id == chat_id,
                ChannelWhitelist.channel_id == channel_id,
            )
        )

    await update.effective_message.reply_html(t("chanprotect_removed", lang))


@user_admin
async def cmd_listchans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chans — List whitelisted channels."""
    if update.effective_chat is None or update.effective_message is None:
        return

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)

    async with get_session() as session:
        result = await session.execute(
            select(ChannelWhitelist).where(ChannelWhitelist.chat_id == chat_id)
        )
        channels = result.scalars().all()

    if not channels:
        await update.effective_message.reply_text(t("chanprotect_list_empty", lang))
        return

    lines = [t("chanprotect_menu_title", lang), ""]
    for ch in channels:
        lines.append(f"• <b>{ch.channel_title}</b> (<code>{ch.channel_id}</code>)")

    await update.effective_message.reply_html("\n".join(lines))


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.FORWARDED & filters.ChatType.GROUPS,
            _enforce_protection,
        ),
        group=1,
    )
    application.add_handler(
        CommandHandler("chanprotect", cmd_chanprotect, filters=filters.ChatType.GROUPS),
        group=10,
    )
    application.add_handler(
        CommandHandler("allowchan", cmd_allowchan, filters=filters.ChatType.GROUPS),
        group=10,
    )
    application.add_handler(
        CommandHandler("removechan", cmd_removechan, filters=filters.ChatType.GROUPS),
        group=10,
    )
    application.add_handler(
        CommandHandler("chans", cmd_listchans, filters=filters.ChatType.GROUPS),
        group=10,
    )
    log.info("Plugin loaded: channel_protect")
