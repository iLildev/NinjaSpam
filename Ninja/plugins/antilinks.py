"""
plugins/antilinks.py — URL and invite-link blocking (inspired by Guardy).

Commands:
  /antilinks <off|invite|all>   — Set the blocking mode.
  /antilinkaction <action>      — Set the enforcement action.
  /antilinks                    — Show current settings.

Modes:
  off     — Feature disabled (default).
  invite  — Block Telegram invite links only (t.me/joinchat, t.me/+...).
  all     — Block every URL found in text or captions.

Actions:
  delete            — Delete the message silently.
  delete_warn       — Delete and issue a warning.
  delete_mute       — Delete and temporarily mute (1h).
  delete_ban        — Delete and ban permanently.

Approved users (see approve.py) bypass this filter entirely.
Admin messages are skipped unless apply_to_admins is explicitly enabled.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel, ChatMember as ChatMemberModel
from database.models_extra import (
    AntiLinkAction,
    AntiLinkMode,
    AntiLinkSettings,
    ApprovedUser,
)

log = logging.getLogger(__name__)

ANTILINK_GROUP: int = 7

# ---------------------------------------------------------------------------
# URL detection patterns
# ---------------------------------------------------------------------------

# Matches any HTTP/HTTPS URL or bare domain.
_URL_PATTERN: re.Pattern[str] = re.compile(
    r"(https?://\S+|www\.\S+\.\S+)",
    re.IGNORECASE,
)

# Matches Telegram invite links specifically.
_INVITE_PATTERN: re.Pattern[str] = re.compile(
    r"(https?://)?(t\.me|telegram\.me|telegram\.dog)/(joinchat/|(\+))\S+",
    re.IGNORECASE,
)


def _has_invite_link(text: str) -> bool:
    """Return True if *text* contains a Telegram invite link."""
    return bool(_INVITE_PATTERN.search(text))


def _has_any_url(text: str) -> bool:
    """Return True if *text* contains any URL."""
    return bool(_URL_PATTERN.search(text))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(chat_id: int) -> Optional[AntiLinkSettings]:
    async with get_session() as session:
        return await session.get(AntiLinkSettings, chat_id)


async def _get_or_create_settings(
    session, chat_id: int, chat_title: str = ""
) -> AntiLinkSettings:
    settings = await session.get(AntiLinkSettings, chat_id)
    if settings is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=chat_title))
            await session.flush()
        settings = AntiLinkSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


async def _is_approved(chat_id: int, user_id: int) -> bool:
    """Return True if the user has been approved in this chat."""
    async with get_session() as session:
        result = await session.execute(
            select(ApprovedUser).where(
                ApprovedUser.chat_id == chat_id,
                ApprovedUser.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Enforcement handler
# ---------------------------------------------------------------------------

async def antilinks_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Scan every non-admin message for prohibited links and enforce the
    configured action.

    Skips:
    - Messages from admins (unless apply_to_admins is True).
    - Messages from approved users.
    - Messages with no text or caption.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not chat:
        return

    text: str = message.text or message.caption or ""
    if not text:
        return

    settings = await _get_settings(chat.id)
    if settings is None or settings.mode == AntiLinkMode.OFF:
        return

    if not settings.apply_to_admins and await is_user_admin(chat, user.id):
        return

    if await _is_approved(chat.id, user.id):
        return

    # Check temporary permit (from permit.py)
    try:
        from plugins.permit import is_permitted
        if await is_permitted(chat.id, user.id, consume=True):
            return
    except Exception:
        pass

    triggered: bool = False
    if settings.mode == AntiLinkMode.INVITE:
        triggered = _has_invite_link(text)
    elif settings.mode == AntiLinkMode.ALL:
        triggered = _has_any_url(text)

    if not triggered:
        return

    try:
        await message.delete()
    except BadRequest:
        pass

    action = AntiLinkAction(settings.action) if isinstance(settings.action, str) else settings.action

    if action == AntiLinkAction.DELETE:
        return

    if action == AntiLinkAction.DELETE_WARN:
        # Issue a warning via the warns plugin logic inline.
        await _auto_warn(context, chat, user.id, "Sending prohibited links")
        return

    if action == AntiLinkAction.DELETE_MUTE:
        from datetime import timedelta
        from telegram import ChatPermissions
        until = None
        try:
            import datetime as _dt
            until = _dt.datetime.now(_dt.timezone.utc) + timedelta(hours=1)
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                ),
                until_date=until,
            )
            await context.bot.send_message(
                chat.id,
                f"🔇 <a href='tg://user?id={user.id}'>{user.id}</a> muted 1h for sending a prohibited link.",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as exc:
            log.warning("Anti-link mute failed: %s", exc.message)
        return

    if action == AntiLinkAction.DELETE_BAN:
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat.id,
                f"🚫 <a href='tg://user?id={user.id}'>{user.id}</a> banned for sending a prohibited link.",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as exc:
            log.warning("Anti-link ban failed: %s", exc.message)


async def _auto_warn(context, chat, user_id: int, reason: str) -> None:
    """Issue a single automated warning without going through the full warns plugin."""
    from database.engine import get_session
    from database.models import ChatFeatureSettings, ChatMember, User, WarnEntry, WarnAction
    from sqlalchemy import select

    async with get_session() as session:
        if not await session.get(User, user_id):
            session.add(User(id=user_id, first_name=""))
            await session.flush()

        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat.id,
                ChatMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            member = ChatMember(chat_id=chat.id, user_id=user_id)
            session.add(member)
            await session.flush()

        member.warn_count += 1
        session.add(WarnEntry(
            chat_id=chat.id,
            user_id=user_id,
            reason=reason,
            triggered_by="antilinks",
        ))

        feat = await session.get(ChatFeatureSettings, chat.id)
        limit = feat.warn_limit if feat else 3
        count = member.warn_count

    await context.bot.send_message(
        chat.id,
        f"⚠️ <a href='tg://user?id={user_id}'>{user_id}</a> warned ({count}/{limit}): {reason}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /antilinks command
# ---------------------------------------------------------------------------

@user_admin
async def cmd_antilinks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    View or set the anti-links mode.

    Usage:
        /antilinks           — Show current settings.
        /antilinks off       — Disable link blocking.
        /antilinks invite    — Block Telegram invites only.
        /antilinks all       — Block all URLs.
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    if not args:
        settings = await _get_settings(chat.id)
        mode = settings.mode if settings else AntiLinkMode.OFF
        action = settings.action if settings else AntiLinkAction.DELETE
        await message.reply_html(
            f"<b>Anti-Links — {chat.title}</b>\n\n"
            f"Mode: <code>{mode}</code>\n"
            f"Action: <code>{action}</code>\n\n"
            f"<i>Use /antilinks &lt;off|invite|all&gt; to change mode.\n"
            f"Use /antilinkaction &lt;delete|delete_warn|delete_mute|delete_ban&gt; to set action.</i>"
        )
        return

    raw = args[0].lower()
    valid = {m.value for m in AntiLinkMode}
    if raw not in valid:
        await message.reply_text(f"Invalid mode. Choose from: {', '.join(sorted(valid))}")
        return

    mode = AntiLinkMode(raw)
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.mode = mode

    state_msg = {
        AntiLinkMode.OFF: "Anti-links <b>disabled</b>.",
        AntiLinkMode.INVITE: "Now blocking <b>Telegram invite links</b>.",
        AntiLinkMode.ALL: "Now blocking <b>all URLs</b>.",
    }[mode]
    await message.reply_html(state_msg)


@user_admin
async def cmd_antilinkaction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Set the enforcement action for the anti-links filter.

    Usage:
        /antilinkaction <delete|delete_warn|delete_mute|delete_ban>
    """
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []

    valid = {a.value for a in AntiLinkAction}
    if not args or args[0].lower() not in valid:
        await message.reply_text(
            f"Usage: /antilinkaction <{'|'.join(sorted(valid))}>"
        )
        return

    action = AntiLinkAction(args[0].lower())
    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id, chat.title or "")
        settings.action = action

    await message.reply_html(f"Anti-link action set to <b>{action.value}</b>.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register anti-links enforcement and admin commands."""
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
            antilinks_handler,
        ),
        group=ANTILINK_GROUP,
    )
    application.add_handler(
        CommandHandler("antilinks", cmd_antilinks, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("antilinkaction", cmd_antilinkaction, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: antilinks")
