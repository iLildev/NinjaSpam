"""
plugins/tagall.py — Mention/tag multiple users in a group.

Commands:
  /tagall [reason]    — Tag/mention all current group administrators.
                        Admins can optionally supply a reason message.
  /tagadmins [reason] — Alias for /tagall.

Behaviour:
  • Only group administrators may invoke /tagall (to prevent abuse).
  • The bot builds a list of all current admins (excluding bots) and
    sends a single message mentioning each one via text_mention entities,
    followed by the optional reason text.
  • Anonymous admins (status "creator" with is_anonymous=True) cannot be
    mentioned and are silently skipped.
  • Rate-limited to once every 60 seconds per chat to prevent spam.

Design note:
  Uses HTML mentions (<a href="tg://user?id=…">name</a>) which work even
  when users don't have a @username.
"""

from __future__ import annotations

import logging
import time
from typing import Dict

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin

logger = logging.getLogger(__name__)

# Rate limit: one /tagall per chat per N seconds
_COOLDOWN_SECS: int = 60
_last_call: Dict[int, float] = {}  # chat_id → last invocation timestamp


# ---------------------------------------------------------------------------
# /tagall / /tagadmins
# ---------------------------------------------------------------------------

@user_admin
async def tagall(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Mention all non-bot admins in the current group."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    # --- Rate limit check ---
    now = time.monotonic()
    last = _last_call.get(chat.id, 0.0)
    remaining = _COOLDOWN_SECS - (now - last)
    if remaining > 0:
        await msg.reply_text(
            f"⏳ Please wait <b>{int(remaining) + 1}s</b> before using /tagall again.",
            parse_mode=ParseMode.HTML,
        )
        return
    _last_call[chat.id] = now

    # --- Fetch admins ---
    try:
        admins = await chat.get_administrators()
    except (BadRequest, TelegramError) as exc:
        logger.warning("tagall: get_administrators failed: %s", exc)
        await msg.reply_text("⚠️ Could not fetch the admin list.")
        return

    # Build mention list — skip bots and truly anonymous admins
    mentions: list[str] = []
    for admin in admins:
        m = admin.user
        if m.is_bot:
            continue
        if getattr(admin, "is_anonymous", False):
            continue
        mentions.append(
            f'<a href="tg://user?id={m.id}">{m.full_name}</a>'
        )

    if not mentions:
        await msg.reply_text("No mentionable admins found in this group.")
        return

    # Optional reason from command args
    reason = " ".join(context.args) if context.args else ""
    reason_text = f"\n\n📢 <i>{reason}</i>" if reason else ""

    header = f"📣 <b>Admins tagged by {user.mention_html()}</b>{reason_text}\n\n"
    body = "  ".join(mentions)

    # Telegram message length hard cap is 4096 chars; split if needed
    full = header + body
    if len(full) <= 4096:
        await msg.reply_text(full, parse_mode=ParseMode.HTML)
    else:
        # Send in chunks of ~20 mentions
        await msg.reply_text(header, parse_mode=ParseMode.HTML)
        chunk_size = 20
        for i in range(0, len(mentions), chunk_size):
            chunk = "  ".join(mentions[i : i + chunk_size])
            await msg.reply_text(chunk, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("tagall",    tagall, filters=group_filter))
    application.add_handler(CommandHandler("tagadmins", tagall, filters=group_filter))
    logger.info("tagall plugin registered.")
