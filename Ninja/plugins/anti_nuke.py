"""
plugins/anti_nuke.py — Anti-Nuke protection against group takeover.

Monitors ChatMemberUpdated events for rapid admin promotions.
If the configured threshold is reached within the time window, the bot
alerts admins and optionally demotes or restricts the newly promoted users.

This is the signature feature of bots like Wick — it catches the pattern
of a compromised account mass-promoting unknown users to admins before the
legitimate admins can react.

Commands (admins only):
  /antinuke on|off                — Enable / disable
  /antinuke threshold <n>         — Promotions in window before triggering (def: 3)
  /antinuke window <seconds>      — Time window in seconds (def: 60)
  /antinuke action alert|demote   — Response action (def: alert)
  /antinuke status                — Show current config

Actions:
  alert   — Send an alert to the group (no automatic demotion)
  demote  — Alert + strip admin rights from all users promoted during the event
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Tuple

from sqlalchemy import select
from telegram import ChatMember, ChatMemberAdministrator, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import AntiNukeSettings

logger = logging.getLogger(__name__)

# In-memory window: chat_id → deque of (timestamp_utc, promoted_user_id, promoter_id)
_NUKE_WINDOW: Dict[int, Deque[Tuple[float, int, int]]] = {}


def _utcnow() -> float:
    return datetime.now(timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> AntiNukeSettings:
    row = await session.get(AntiNukeSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = AntiNukeSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_antinuke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        if not args:
            await update.message.reply_text(t("nuke_usage", lang), parse_mode=ParseMode.HTML)
            return

        sub = args[0].lower()

        if sub == "on":
            cfg.enabled = True
            await session.commit()
            await update.message.reply_text(
                t("nuke_on", lang, threshold=cfg.threshold, window=cfg.window_seconds),
                parse_mode=ParseMode.HTML,
            )

        elif sub == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(t("nuke_off", lang), parse_mode=ParseMode.HTML)

        elif sub == "threshold":
            if len(args) < 2 or not args[1].isdigit():
                await update.message.reply_text(
                    "Usage: /antinuke threshold <number 2-10>", parse_mode=ParseMode.HTML
                )
                return
            n = int(args[1])
            if not (2 <= n <= 10):
                await update.message.reply_text("Threshold must be between 2 and 10.")
                return
            cfg.threshold = n
            await session.commit()
            await update.message.reply_text(
                t("nuke_on", lang, threshold=n, window=cfg.window_seconds),
                parse_mode=ParseMode.HTML,
            )

        elif sub == "window":
            if len(args) < 2 or not args[1].isdigit():
                await update.message.reply_text(
                    "Usage: /antinuke window <seconds 10-300>", parse_mode=ParseMode.HTML
                )
                return
            secs = int(args[1])
            if not (10 <= secs <= 300):
                await update.message.reply_text("Window must be between 10 and 300 seconds.")
                return
            cfg.window_seconds = secs
            await session.commit()
            await update.message.reply_text(
                t("nuke_on", lang, threshold=cfg.threshold, window=secs),
                parse_mode=ParseMode.HTML,
            )

        elif sub == "action":
            if len(args) < 2 or args[1].lower() not in ("alert", "demote"):
                await update.message.reply_text(
                    "Usage: /antinuke action alert|demote", parse_mode=ParseMode.HTML
                )
                return
            cfg.action = args[1].lower()
            await session.commit()
            await update.message.reply_text(
                t("nuke_on", lang, threshold=cfg.threshold, window=cfg.window_seconds),
                parse_mode=ParseMode.HTML,
            )

        elif sub == "status":
            state = t("enabled", lang) if cfg.enabled else t("disabled", lang)
            await update.message.reply_text(
                t("nuke_status", lang,
                  state=state,
                  threshold=cfg.threshold,
                  window=cfg.window_seconds,
                  action=cfg.action),
                parse_mode=ParseMode.HTML,
            )

        else:
            await update.message.reply_text(t("nuke_usage", lang), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# ChatMember monitor
# ---------------------------------------------------------------------------

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fired when any member's status changes in a tracked chat."""
    result = update.chat_member
    if not result:
        return

    chat = result.chat
    new_member = result.new_chat_member
    old_member = result.old_chat_member

    # Only care about promotions TO admin (from non-admin status)
    was_admin = isinstance(old_member, ChatMemberAdministrator) or old_member.status == ChatMember.OWNER
    is_admin  = isinstance(new_member, ChatMemberAdministrator) or new_member.status == ChatMember.OWNER
    if was_admin or not is_admin:
        return

    # Skip bot's own promotions
    if new_member.user.is_bot:
        return

    async with get_session() as session:
        cfg = await session.get(AntiNukeSettings, chat.id)
        if not cfg or not cfg.enabled:
            return

    promoted_id = new_member.user.id
    promoter_id = result.from_user.id if result.from_user else 0
    now = _utcnow()

    if chat.id not in _NUKE_WINDOW:
        _NUKE_WINDOW[chat.id] = deque()

    window: Deque[Tuple[float, int, int]] = _NUKE_WINDOW[chat.id]
    window.append((now, promoted_id, promoter_id))

    # Prune events older than window_seconds
    async with get_session() as session:
        cfg = await session.get(AntiNukeSettings, chat.id)

    cutoff = now - cfg.window_seconds
    while window and window[0][0] < cutoff:
        window.popleft()

    count = len(window)
    if count < cfg.threshold:
        return

    # ── NUKE TRIGGERED ──────────────────────────────────────────────────────
    logger.warning(
        "anti_nuke: TRIGGERED in chat %d — %d promotions in %ds",
        chat.id, count, cfg.window_seconds,
    )

    lang = await get_chat_lang(chat.id)
    affected_ids = list({ev[1] for ev in window})
    user_mentions = ", ".join(f'<a href="tg://user?id={uid}">{uid}</a>' for uid in affected_ids)

    alert_text = t(
        "nuke_alert", lang,
        count=count,
        window=cfg.window_seconds,
        users=user_mentions,
    )

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=alert_text,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as exc:
        logger.warning("anti_nuke: failed to send alert: %s", exc)

    if cfg.action == "demote":
        for uid in affected_ids:
            try:
                await context.bot.promote_chat_member(
                    chat_id=chat.id,
                    user_id=uid,
                    can_manage_chat=False,
                    can_delete_messages=False,
                    can_manage_video_chats=False,
                    can_restrict_members=False,
                    can_promote_members=False,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False,
                )
                mention = f'<a href="tg://user?id={uid}">{uid}</a>'
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=t("nuke_reverted", lang, user=mention),
                    parse_mode=ParseMode.HTML,
                )
                logger.info("anti_nuke: demoted user %d in chat %d", uid, chat.id)
            except (BadRequest, TelegramError) as exc:
                logger.warning("anti_nuke: failed to demote %d: %s", uid, exc)

    # Clear the window so we don't fire repeatedly for the same event
    window.clear()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("antinuke", cmd_antinuke, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER)
    )
    logger.info("Plugin loaded: anti_nuke")
