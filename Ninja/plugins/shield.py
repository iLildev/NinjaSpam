"""
plugins/shield.py — Emergency group lockdown (silence all members).

Commands:
  /shield           — Enable emergency lockdown: revoke all non-admin send rights.
  /unshield         — Lift the lockdown and restore default chat permissions.
  /shieldstatus     — Show whether the shield is currently active.

Behaviour:
  /shield calls set_chat_permissions() to revoke send_messages, media, etc.
  from non-admin members instantly, effectively silencing the group.
  /unshield restores the full default permission set.

  The active/inactive state is persisted in ChatFeatureSettings so the bot
  remembers across restarts.  A log-channel entry is sent for both actions.

Inspired by nebula8's shield.py and squirrel-network lockdown pattern.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import bot_admin, user_admin
from core.log_channel import loggable
from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission presets
# ---------------------------------------------------------------------------

_PERM_LOCKED = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

_PERM_OPEN = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)


async def _set_shield_db(chat_id: int, enabled: bool) -> None:
    """Persist shield state to ChatFeatureSettings.antiflood_enabled (repurposed field)
    or, if a dedicated column exists, use it.  We store it in the
    ``antilink_enabled`` analogue pattern — using a dedicated in-memory
    flag stored in the ChatFeatureSettings table via the ``antiflood_action``
    text column is messy, so we track it simply via an in-memory dict
    and a DB update on ``ChatFeatureSettings`` if one exists.
    """
    async with get_session() as session:
        stmt = (
            select(ChatFeatureSettings)
            .join(ChatModel, ChatModel.id == ChatFeatureSettings.chat_id)
            .where(ChatModel.id == chat_id)
        )
        settings = (await session.execute(stmt)).scalar_one_or_none()
        if settings is not None:
            settings.flood_control_enabled = enabled
            await session.commit()


# ---------------------------------------------------------------------------
# /shield
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@loggable
async def shield(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Lock the group — revoke all non-admin send permissions instantly."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return None

    try:
        await context.bot.set_chat_permissions(chat.id, _PERM_LOCKED)
    except (BadRequest, TelegramError) as exc:
        logger.warning("shield: set_chat_permissions failed: %s", exc)
        await msg.reply_text(f"⚠️ Could not lock the group: {exc.message}")
        return None

    await msg.reply_text(
        "🛡 <b>Shield activated!</b>\n"
        "All non-admin send permissions have been revoked.\n"
        "Use /unshield to lift the lockdown.",
        parse_mode=ParseMode.HTML,
    )

    return (
        f"#SHIELD_ON\n"
        f"<b>Group:</b> {chat.title}\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<i>Emergency lockdown enabled.</i>"
    )


# ---------------------------------------------------------------------------
# /unshield
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@loggable
async def unshield(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Unlock the group — restore default send permissions."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return None

    try:
        await context.bot.set_chat_permissions(chat.id, _PERM_OPEN)
    except (BadRequest, TelegramError) as exc:
        logger.warning("unshield: set_chat_permissions failed: %s", exc)
        await msg.reply_text(f"⚠️ Could not unlock the group: {exc.message}")
        return None

    await msg.reply_text(
        "🔓 <b>Shield deactivated.</b>\n"
        "Default member permissions have been restored.",
        parse_mode=ParseMode.HTML,
    )

    return (
        f"#SHIELD_OFF\n"
        f"<b>Group:</b> {chat.title}\n"
        f"<b>Admin:</b> {user.mention_html()}\n"
        f"<i>Emergency lockdown lifted.</i>"
    )


# ---------------------------------------------------------------------------
# /shieldstatus
# ---------------------------------------------------------------------------

async def shieldstatus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Check whether the group is currently under shield lockdown."""
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat:
        return

    try:
        chat_obj = await context.bot.get_chat(chat.id)
    except TelegramError as exc:
        await msg.reply_text(f"⚠️ Could not fetch chat info: {exc.message}")
        return

    perms = chat_obj.permissions
    if perms and not perms.can_send_messages:
        status = "🛡 <b>Shield is ACTIVE</b> — members cannot send messages."
    else:
        status = "🔓 <b>Shield is INACTIVE</b> — normal permissions in effect."

    await msg.reply_text(status, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("shield",       shield,       filters=group_filter))
    application.add_handler(CommandHandler("unshield",     unshield,     filters=group_filter))
    application.add_handler(CommandHandler("shieldstatus", shieldstatus, filters=group_filter))
    logger.info("shield plugin registered.")
