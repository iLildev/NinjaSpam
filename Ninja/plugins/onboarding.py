"""
plugins/onboarding.py — First-run setup card when the bot joins a group.

Triggered automatically when the bot is added to a group.  Sends a rich
interactive card so admins can enable the 5 most important features with
a single tap — no documentation required.

Features configurable from the card:
  ✅ CAPTCHA         — Verify new members automatically.
  🛡 AI Anti-Spam   — Bayes-based spam detection.
  🌊 Flood Control  — Auto-ban rapid message flooding.
  ⚔️ Anti-Raid      — Protect against mass-join attacks.
  📢 Reports        — Let members report violations.
  👋 Welcome        — Greet new members.

The card stays dismissable and links directly to /settings for deeper
configuration.
"""

from __future__ import annotations

import html
import logging

from telegram import (
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
)

from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings

log = logging.getLogger(__name__)

_CB = "ob"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _ensure_chat(session, chat_id: int, title: str = "") -> None:
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title))
        await session.flush()


async def _get_or_create_feat(session, chat_id: int) -> ChatFeatureSettings:
    feat = await session.get(ChatFeatureSettings, chat_id)
    if feat is None:
        feat = ChatFeatureSettings(chat_id=chat_id)
        session.add(feat)
        await session.flush()
    return feat


# ---------------------------------------------------------------------------
# Feature state snapshot
# ---------------------------------------------------------------------------

async def _states(chat_id: int) -> dict:
    """Return current on/off state for each quick-setup feature."""
    from database.models_extra import AntiRaidSettings, ReportSettings, WelcomeSettings

    async with get_session() as session:
        feat = await session.get(ChatFeatureSettings, chat_id)
        captcha = bool(feat.captcha_enabled) if feat else False
        spam    = bool(feat.bayes_filter_enabled) if feat else False
        flood   = bool(feat.flood_control_enabled) if feat else False

        raid_row = await session.get(AntiRaidSettings, chat_id)
        raid = bool(raid_row.enabled) if raid_row else False

        rep_row = await session.get(ReportSettings, chat_id)
        reports = bool(rep_row.enabled) if rep_row else False

        ws_row = await session.get(WelcomeSettings, chat_id)
        welcome = bool(ws_row.welcome_enabled) if ws_row else False

    return {
        "captcha": captcha,
        "spam":    spam,
        "flood":   flood,
        "raid":    raid,
        "reports": reports,
        "welcome": welcome,
    }


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------

def _tog(v: bool) -> str:
    return "✅" if v else "☐"


def _card_text(chat_title: str) -> str:
    return (
        f"🥷 <b>Thanks for adding me to {html.escape(chat_title)}!</b>\n\n"
        f"Tap a button below to quickly enable or disable each feature. "
        f"You can adjust all details from <b>⚙️ Full Settings</b>.\n\n"
        f"<i>All changes take effect immediately.</i>"
    )


def _card_keyboard(chat_id: int, s: dict) -> InlineKeyboardMarkup:
    cid = chat_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{_tog(s['captcha'])} CAPTCHA",      callback_data=f"{_CB}:t:captcha:{cid}"),
            InlineKeyboardButton(f"{_tog(s['spam'])} AI Anti-Spam",    callback_data=f"{_CB}:t:spam:{cid}"),
        ],
        [
            InlineKeyboardButton(f"{_tog(s['flood'])} Flood Control",  callback_data=f"{_CB}:t:flood:{cid}"),
            InlineKeyboardButton(f"{_tog(s['raid'])} Anti-Raid",       callback_data=f"{_CB}:t:raid:{cid}"),
        ],
        [
            InlineKeyboardButton(f"{_tog(s['reports'])} Reports",      callback_data=f"{_CB}:t:reports:{cid}"),
            InlineKeyboardButton(f"{_tog(s['welcome'])} Welcome Msgs", callback_data=f"{_CB}:t:welcome:{cid}"),
        ],
        [
            InlineKeyboardButton("⚙️ Full Settings", callback_data="sp:main:menu:"),
            InlineKeyboardButton("📖 Commands",       callback_data="help:main"),
        ],
        [InlineKeyboardButton("✕ Dismiss",            callback_data=f"{_CB}:dismiss:{cid}:")],
    ])


# ---------------------------------------------------------------------------
# Handler: bot added to group
# ---------------------------------------------------------------------------

async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires when the bot's own membership status changes."""
    change: ChatMemberUpdated | None = update.my_chat_member
    if not change:
        return

    bot_id = context.bot.id

    # Only care about events involving this bot
    if change.new_chat_member.user.id != bot_id:
        return

    new_status = change.new_chat_member.status
    old_status = change.old_chat_member.status

    # Fire only when bot transitions FROM absent/banned → member/admin
    was_present = old_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    )
    is_present = new_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
    )

    if was_present or not is_present:
        return  # removal, re-add of self, or irrelevant change

    chat: Chat = change.chat
    if chat.type == "private":
        return

    # Register the chat
    async with get_session() as session:
        await _ensure_chat(session, chat.id, chat.title or "")

    s = await _states(chat.id)

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=_card_text(chat.title or "this group"),
            parse_mode=ParseMode.HTML,
            reply_markup=_card_keyboard(chat.id, s),
        )
    except (Forbidden, BadRequest) as exc:
        log.debug("Onboarding card not sent in %s: %s", chat.id, exc)


# ---------------------------------------------------------------------------
# Callback: toggle + dismiss
# ---------------------------------------------------------------------------

async def onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button presses on the setup card."""
    query: CallbackQuery | None = update.callback_query
    if not query:
        return
    await query.answer()

    admin = update.effective_user
    if not admin:
        return

    # ob:t:{feature}:{chat_id}  or  ob:dismiss:{chat_id}:
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return

    action = parts[1]

    # ── Dismiss ──────────────────────────────────────────────────────────────
    if action == "dismiss":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return

    if action != "t" or len(parts) < 4:
        return

    feature = parts[2]
    try:
        chat_id = int(parts[3])
    except ValueError:
        return

    # Permission check
    try:
        member = await context.bot.get_chat_member(chat_id, admin.id)
        if member.status not in ("administrator", "creator"):
            await query.answer("⛔ Only group admins can change these settings.", show_alert=True)
            return
    except TelegramError:
        await query.answer("⚠️ Could not verify your admin status.", show_alert=True)
        return

    # Apply toggle
    from database.models_extra import AntiRaidSettings, ReportSettings, WelcomeSettings

    async with get_session() as session:
        await _ensure_chat(session, chat_id)

        if feature == "captcha":
            feat = await _get_or_create_feat(session, chat_id)
            feat.captcha_enabled = not feat.captcha_enabled

        elif feature == "spam":
            feat = await _get_or_create_feat(session, chat_id)
            feat.bayes_filter_enabled = not feat.bayes_filter_enabled

        elif feature == "flood":
            feat = await _get_or_create_feat(session, chat_id)
            feat.flood_control_enabled = not feat.flood_control_enabled
            if feat.flood_control_enabled and not feat.flood_messages_limit:
                feat.flood_messages_limit = 5

        elif feature == "raid":
            row = await session.get(AntiRaidSettings, chat_id)
            if row is None:
                row = AntiRaidSettings(chat_id=chat_id, enabled=True)
                session.add(row)
            else:
                row.enabled = not row.enabled

        elif feature == "reports":
            row = await session.get(ReportSettings, chat_id)
            if row is None:
                row = ReportSettings(chat_id=chat_id, enabled=True)
                session.add(row)
            else:
                row.enabled = not row.enabled

        elif feature == "welcome":
            row = await session.get(WelcomeSettings, chat_id)
            if row is None:
                row = WelcomeSettings(chat_id=chat_id, welcome_enabled=True)
                session.add(row)
            else:
                row.welcome_enabled = not row.welcome_enabled

    # Refresh the card
    try:
        chat_obj = await context.bot.get_chat(chat_id)
        title = chat_obj.title or ""
    except TelegramError:
        title = ""

    s = await _states(chat_id)
    try:
        await query.edit_message_text(
            _card_text(title),
            parse_mode=ParseMode.HTML,
            reply_markup=_card_keyboard(chat_id, s),
        )
    except TelegramError:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(ChatMemberHandler(on_bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CallbackQueryHandler(onboarding_callback, pattern=rf"^{_CB}:"))
    log.info("Plugin loaded: onboarding")
