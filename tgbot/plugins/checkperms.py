"""
plugins/checkperms.py — Bot permissions inspector for the current chat.

Commands:
  /checkperms   — Show a complete table of the bot's current Telegram
                  permissions in this group, with ✅/❌ per capability.

Only usable in groups by any member (since knowing what the bot can do
is not sensitive information).  Inspired by nebula8's check_permission.py.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import ChatMemberAdministrator, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission descriptor table
# Each tuple: (attribute_name, friendly_label)
# ---------------------------------------------------------------------------
_PERM_ROWS: list[tuple[str, str]] = [
    ("can_delete_messages",   "Delete Messages"),
    ("can_restrict_members",  "Restrict / Ban Members"),
    ("can_pin_messages",      "Pin Messages"),
    ("can_promote_members",   "Promote Members"),
    ("can_invite_users",      "Invite Users"),
    ("can_change_info",       "Change Group Info"),
    ("can_manage_video_chats","Manage Video Chats"),
    ("can_manage_chat",       "Manage Chat (general)"),
]


def _tick(value: Optional[bool]) -> str:
    """Return ✅ for True, ❌ for False/None."""
    return "✅" if value else "❌"


# ---------------------------------------------------------------------------
# /checkperms handler
# ---------------------------------------------------------------------------

async def checkperms(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Display the bot's current permission set in this group."""
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat:
        return

    try:
        bot_member = await chat.get_member(context.bot.id)
    except (BadRequest, TelegramError) as exc:
        logger.warning("checkperms: get_member failed: %s", exc)
        await msg.reply_text("⚠️ Could not fetch my membership info.")
        return

    if bot_member.status not in ("administrator", "creator"):
        await msg.reply_text(
            "❌ I am <b>not an administrator</b> in this group.\n"
            "Please promote me first so I can work properly.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Build the table
    lines: list[str] = [
        f"🤖 <b>Bot Permissions in {chat.title or 'this chat'}</b>\n"
    ]

    if isinstance(bot_member, ChatMemberAdministrator):
        for attr, label in _PERM_ROWS:
            val = getattr(bot_member, attr, None)
            lines.append(f"{_tick(val)} {label}")

        # Special: anonymous admin flag
        lines.append(f"{_tick(bot_member.is_anonymous)} Anonymous Admin")
    else:
        # Creator — all permissions implicitly granted
        for _, label in _PERM_ROWS:
            lines.append(f"✅ {label}")
        lines.append("✅ Anonymous Admin")
        lines.append("\n<i>(Bot is group creator — all permissions are implicit.)</i>")

    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    application.add_handler(
        CommandHandler(
            "checkperms",
            checkperms,
            filters=filters.ChatType.GROUPS,
        )
    )
    logger.info("checkperms plugin registered.")
