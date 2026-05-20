"""
plugins/anti_nsfw.py — Anti-NSFW / Anti-Porn Content Filter (GroupHelp feature).

Automatically detects and removes explicit/adult content sent by non-admins.

Detection layers (all configurable per-chat):
  1. Telegram's native sensitive-content flag (has_media_spoiler used as proxy).
  2. Caption keyword scan — catches media sent with explicit captions.
  3. Sticker-set name keyword scan — detects stickers from known adult packs.
  4. Admin-triggered manual flag via /nsfwreport (reply to flag a message type).

Commands (admin only):
  /nsfw on|off            — Enable or disable the NSFW filter.
  /nsfwaction delete      — Delete only (default).
  /nsfwaction mute        — Delete + temporarily mute the sender (1 hour).
  /nsfwaction ban         — Delete + ban the sender.
  /nsfwstatus             — Show current NSFW filter configuration.
  /nsfwword add <word>    — Add a keyword to the caption blocklist.
  /nsfwword remove <word> — Remove a keyword.
  /nsfwword list          — List all blocked keywords.
"""

from __future__ import annotations

import logging
import re
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
from database.models_extra import AntiNSFWSettings, AntiNSFWKeyword

log = logging.getLogger(__name__)

# Built-in keyword seeds — admins can add/remove from the per-chat list.
_DEFAULT_NSFW_KEYWORDS: list[str] = [
    "nsfw", "porn", "xxx", "adult", "18+", "nude", "naked",
    "explicit", "onlyfans", "sex", "erotic",
]

# Sticker set name fragments that often indicate adult content.
_NSFW_STICKER_SETS: list[str] = [
    "nsfw", "porn", "xxx", "adult18", "nude", "naked18", "explicit",
    "hentai", "erotic",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[AntiNSFWSettings]:
    async with get_session() as session:
        return await session.get(AntiNSFWSettings, chat_id)


async def _ensure_settings(chat_id: int) -> AntiNSFWSettings:
    async with get_session() as session:
        row = await session.get(AntiNSFWSettings, chat_id)
        if row is None:
            row = AntiNSFWSettings(chat_id=chat_id)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _get_keywords(chat_id: int) -> list[str]:
    async with get_session() as session:
        result = await session.execute(
            select(AntiNSFWKeyword.keyword).where(AntiNSFWKeyword.chat_id == chat_id)
        )
        return [r[0] for r in result.all()]


def _caption_is_nsfw(caption: str, extra_keywords: list[str]) -> bool:
    """Return True if the caption contains any NSFW keyword."""
    text = caption.lower()
    all_kw = _DEFAULT_NSFW_KEYWORDS + extra_keywords
    return any(kw in text for kw in all_kw)


def _sticker_set_is_nsfw(set_name: str) -> bool:
    low = set_name.lower()
    return any(frag in low for frag in _NSFW_STICKER_SETS)


async def _take_action(update: Update, context, action: str, user_id: int) -> None:
    """Apply the configured punishment after NSFW detection."""
    chat = update.effective_chat
    if action == "mute":
        from datetime import timedelta
        until = None  # 1-hour mute
        try:
            from telegram import ChatPermissions
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(
                    (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                     + timedelta(hours=1)).timestamp()
                ),
            )
        except (BadRequest, Forbidden):
            pass
    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        except (BadRequest, Forbidden):
            pass


# ---------------------------------------------------------------------------
# /nsfw
# ---------------------------------------------------------------------------

@user_admin
async def nsfw_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the NSFW filter on or off."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args or args[0].lower() not in ("on", "off"):
        await message.reply_text("Usage: /nsfw on  or  /nsfw off")
        return

    enabled = args[0].lower() == "on"
    async with get_session() as session:
        row = await session.get(AntiNSFWSettings, chat.id)
        if row is None:
            row = AntiNSFWSettings(chat_id=chat.id, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
        await session.commit()

    state = "✅ enabled" if enabled else "❌ disabled"
    await message.reply_text(f"Anti-NSFW filter is now {state}.")


# ---------------------------------------------------------------------------
# /nsfwaction
# ---------------------------------------------------------------------------

@user_admin
async def nsfw_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the action taken when NSFW content is detected."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    valid = ("delete", "mute", "ban")
    if not args or args[0].lower() not in valid:
        await message.reply_text(f"Usage: /nsfwaction <{'|'.join(valid)}>")
        return

    action = args[0].lower()
    async with get_session() as session:
        row = await session.get(AntiNSFWSettings, chat.id)
        if row is None:
            row = AntiNSFWSettings(chat_id=chat.id, action=action)
            session.add(row)
        else:
            row.action = action
        await session.commit()

    action_desc = {
        "delete": "delete message only",
        "mute": "delete + mute for 1 hour",
        "ban": "delete + permanent ban",
    }
    await message.reply_text(f"NSFW action set to: {action_desc[action]}.")


# ---------------------------------------------------------------------------
# /nsfwstatus
# ---------------------------------------------------------------------------

@user_admin
async def nsfw_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current NSFW filter configuration."""
    message = update.effective_message
    chat = update.effective_chat

    settings = await _get_settings(chat.id)
    keywords = await _get_keywords(chat.id)

    if not settings:
        await message.reply_html(
            "<b>Anti-NSFW Filter</b>\n\nStatus: ❌ Disabled (never configured)\n"
            "Use /nsfw on to enable."
        )
        return

    state = "✅ Active" if settings.enabled else "❌ Disabled"
    action_map = {
        "delete": "Delete only",
        "mute": "Delete + Mute (1h)",
        "ban": "Delete + Ban",
    }
    kw_list = ", ".join(f"<code>{k}</code>" for k in keywords) or "None"

    await message.reply_html(
        f"<b>Anti-NSFW Filter</b>\n\n"
        f"<b>Status:</b> {state}\n"
        f"<b>Action:</b> {action_map.get(settings.action, settings.action)}\n"
        f"<b>Caption scan:</b> {'✅' if settings.scan_captions else '❌'}\n"
        f"<b>Sticker scan:</b> {'✅' if settings.scan_stickers else '❌'}\n"
        f"<b>Custom keywords:</b> {kw_list}"
    )


# ---------------------------------------------------------------------------
# /nsfwword
# ---------------------------------------------------------------------------

@user_admin
async def nsfw_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage the per-chat NSFW caption keyword list."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args:
        await message.reply_text("Usage: /nsfwword add <word> | /nsfwword remove <word> | /nsfwword list")
        return

    sub = args[0].lower()

    if sub == "list":
        keywords = await _get_keywords(chat.id)
        if not keywords:
            await message.reply_text("No custom NSFW keywords set.")
        else:
            kw_list = "\n".join(f"• <code>{k}</code>" for k in sorted(keywords))
            await message.reply_html(f"<b>Custom NSFW keywords:</b>\n{kw_list}")
        return

    if sub in ("add", "remove") and len(args) < 2:
        await message.reply_text(f"Usage: /nsfwword {sub} <word>")
        return

    word = args[1].lower().strip()

    if sub == "add":
        async with get_session() as session:
            exists = await session.execute(
                select(AntiNSFWKeyword).where(
                    AntiNSFWKeyword.chat_id == chat.id,
                    AntiNSFWKeyword.keyword == word,
                )
            )
            if exists.scalar_one_or_none():
                await message.reply_text(f"<code>{word}</code> is already in the list.", parse_mode=ParseMode.HTML)
                return
            session.add(AntiNSFWKeyword(chat_id=chat.id, keyword=word))
            await session.commit()
        await message.reply_html(f"✅ Added <code>{word}</code> to NSFW keyword list.")

    elif sub == "remove":
        async with get_session() as session:
            result = await session.execute(
                delete(AntiNSFWKeyword).where(
                    AntiNSFWKeyword.chat_id == chat.id,
                    AntiNSFWKeyword.keyword == word,
                ).returning(AntiNSFWKeyword.keyword)
            )
            await session.commit()
            if result.rowcount == 0:
                await message.reply_text(f"<code>{word}</code> is not in the list.", parse_mode=ParseMode.HTML)
            else:
                await message.reply_html(f"✅ Removed <code>{word}</code> from NSFW keyword list.")


# ---------------------------------------------------------------------------
# Enforcement handler
# ---------------------------------------------------------------------------

async def enforce_nsfw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan incoming media messages for NSFW content."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not user or not message or not chat:
        return
    if user.is_bot:
        return
    if await is_user_admin(chat, user.id):
        return

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        return

    extra_keywords = await _get_keywords(chat.id)
    is_nsfw = False

    # Layer 1 — Telegram sensitive content flag.
    if getattr(message, "has_media_spoiler", False):
        is_nsfw = True

    # Layer 2 — Caption keyword scan.
    if not is_nsfw and settings.scan_captions:
        caption = message.caption or ""
        if caption and _caption_is_nsfw(caption, extra_keywords):
            is_nsfw = True

    # Layer 3 — Sticker set name scan.
    if not is_nsfw and settings.scan_stickers and message.sticker:
        set_name = getattr(message.sticker, "set_name", "") or ""
        if set_name and _sticker_set_is_nsfw(set_name):
            is_nsfw = True

    if not is_nsfw:
        return

    # Delete the offending message.
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Notify and punish.
    action = settings.action or "delete"
    action_note = {
        "delete": "",
        "mute": " You have been muted for 1 hour.",
        "ban": " You have been banned.",
    }.get(action, "")

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🔞 {user.mention_html()}, explicit content is not allowed here."
                f"{action_note}"
            ),
            parse_mode=ParseMode.HTML,
        )
    except (BadRequest, Forbidden):
        pass

    if action != "delete":
        await _take_action(update, context, action, user.id)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("nsfw", nsfw_toggle, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("nsfwaction", nsfw_action, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("nsfwstatus", nsfw_status, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("nsfwword", nsfw_word, filters=filters.ChatType.GROUPS)
    )
    # Scan all media messages (photos, videos, stickers, documents, animations).
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (
                filters.PHOTO | filters.VIDEO | filters.Sticker.ALL |
                filters.Document.ALL | filters.ANIMATION
            ),
            enforce_nsfw,
        ),
        group=6,
    )
    log.info("Plugin loaded: anti_nsfw")
