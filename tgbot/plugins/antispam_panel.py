"""
plugins/antispam_panel.py — Unified inline-keyboard settings panel.

Provides a single /settings command that shows the complete bot configuration
for the current group as an interactive inline keyboard.  Admins can toggle
any feature on or off with a single tap — no need to remember individual
command names.

Panel sections:
  🛡 Protection  — Bayes filter, Regex filter, CAPTCHA, Anti-links, Anti-raid
  🚫 Restrictions — Anti-flood, Locks (content type overview)
  👋 Welcome     — Welcome message, Goodbye message, Clean welcome
  📋 Moderation  — Warns (show limit), Reports, Global ban enforcement

The panel is ephemeral — it is sent as a message and auto-deleted after
the admin presses a toggle to keep the chat clean.

Commands:
  /settings    — Open the settings panel (group admins only).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import user_admin, is_user_admin
from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings
from database.models_extra import (
    AntiLinkMode,
    AntiLinkSettings,
    AntiRaidSettings,
    ReportSettings,
    WelcomeSettings,
)

log = logging.getLogger(__name__)

_CB_PREFIX = "cfg"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

async def _get_or_create_feat(session, chat_id: int, title: str = "") -> ChatFeatureSettings:
    feat = await session.get(ChatFeatureSettings, chat_id)
    if feat is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=title))
            await session.flush()
        feat = ChatFeatureSettings(chat_id=chat_id)
        session.add(feat)
        await session.flush()
    return feat


async def _load_panel_data(chat_id: int, title: str = "") -> dict:
    """Load all toggleable settings for a chat into a flat dict."""
    async with get_session() as session:
        feat = await _get_or_create_feat(session, chat_id, title)

        antilink = await session.get(AntiLinkSettings, chat_id)
        antiraid = await session.get(AntiRaidSettings, chat_id)
        report = await session.get(ReportSettings, chat_id)
        welcome = await session.get(WelcomeSettings, chat_id)

    return {
        "bayes": feat.bayes_filter_enabled,
        "regex": feat.regex_filter_enabled,
        "captcha": feat.captcha_enabled,
        "flood": feat.flood_control_enabled,
        "antilink": (antilink.mode != AntiLinkMode.OFF) if antilink else False,
        "antiraid": antiraid.enabled if antiraid else False,
        "welcome": welcome.welcome_enabled if welcome else False,
        "goodbye": welcome.goodbye_enabled if welcome else False,
        "clean_welcome": welcome.clean_welcome if welcome else False,
        "reports": report.enabled if report else True,
        "warn_limit": feat.warn_limit,
    }


def _bool_icon(val: bool) -> str:
    return "✅" if val else "✗"


def _build_keyboard(data: dict, chat_id: int) -> InlineKeyboardMarkup:
    """Build the settings inline keyboard from the current data dict."""

    def btn(label: str, key: str) -> InlineKeyboardButton:
        icon = _bool_icon(data.get(key, False))
        return InlineKeyboardButton(
            f"{icon} {label}",
            callback_data=f"{_CB_PREFIX}:{chat_id}:{key}",
        )

    keyboard = [
        # Row: section header (non-clickable, just informational buttons with no cb)
        [InlineKeyboardButton("━━ 🛡 Spam Protection ━━", callback_data="noop")],
        [
            btn("Bayes AI Filter", "bayes"),
            btn("Regex Filter", "regex"),
        ],
        [
            btn("CAPTCHA", "captcha"),
            btn("Anti-Links", "antilink"),
        ],
        [
            btn("Anti-Raid", "antiraid"),
            btn("Anti-Flood", "flood"),
        ],
        [InlineKeyboardButton("━━ 👋 Welcome ━━", callback_data="noop")],
        [
            btn("Welcome Msg", "welcome"),
            btn("Goodbye Msg", "goodbye"),
        ],
        [
            btn("Clean Welcome", "clean_welcome"),
        ],
        [InlineKeyboardButton("━━ 📋 Moderation ━━", callback_data="noop")],
        [
            btn("Reports", "reports"),
            InlineKeyboardButton(
                f"⚠️ Warn Limit: {data.get('warn_limit', 3)}",
                callback_data="noop",
            ),
        ],
        [InlineKeyboardButton("✖ Close", callback_data=f"{_CB_PREFIX}:{chat_id}:close")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# /settings command
# ---------------------------------------------------------------------------

@user_admin
async def settings_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Open the interactive settings panel for this group."""
    chat = update.effective_chat
    message = update.effective_message

    data = await _load_panel_data(chat.id, chat.title or "")
    keyboard = _build_keyboard(data, chat.id)

    await message.reply_html(
        f"<b>⚙️ Settings — {chat.title}</b>\n\n"
        f"Tap any button to toggle that feature on or off.",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle inline keyboard button presses from the settings panel."""
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return

    _, chat_id_str, key = parts[0], parts[1], parts[2]

    if key == "noop":
        return

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return

    # Verify the button-presser is still an admin in the target chat.
    try:
        chat_obj = await context.bot.get_chat(chat_id)
        if not await is_user_admin(chat_obj, user.id):
            await query.answer("Only group admins can change settings.", show_alert=True)
            return
    except BadRequest:
        return

    if key == "close":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        return

    # Apply the toggle.
    async with get_session() as session:
        feat = await _get_or_create_feat(session, chat_id)

        if key == "bayes":
            feat.bayes_filter_enabled = not feat.bayes_filter_enabled
        elif key == "regex":
            feat.regex_filter_enabled = not feat.regex_filter_enabled
        elif key == "captcha":
            feat.captcha_enabled = not feat.captcha_enabled
        elif key == "flood":
            feat.flood_control_enabled = not feat.flood_control_enabled

        elif key == "antilink":
            antilink = await session.get(AntiLinkSettings, chat_id)
            if antilink is None:
                if not await session.get(ChatModel, chat_id):
                    session.add(ChatModel(id=chat_id, title=""))
                    await session.flush()
                antilink = AntiLinkSettings(chat_id=chat_id)
                session.add(antilink)
                await session.flush()
            # Toggle: off → invite, invite/all → off
            antilink.mode = (
                AntiLinkMode.OFF
                if antilink.mode != AntiLinkMode.OFF
                else AntiLinkMode.INVITE
            )

        elif key == "antiraid":
            antiraid = await session.get(AntiRaidSettings, chat_id)
            if antiraid is None:
                if not await session.get(ChatModel, chat_id):
                    session.add(ChatModel(id=chat_id, title=""))
                    await session.flush()
                antiraid = AntiRaidSettings(chat_id=chat_id)
                session.add(antiraid)
                await session.flush()
            antiraid.enabled = not antiraid.enabled

        elif key in ("welcome", "goodbye", "clean_welcome"):
            welcome = await session.get(WelcomeSettings, chat_id)
            if welcome is None:
                if not await session.get(ChatModel, chat_id):
                    session.add(ChatModel(id=chat_id, title=""))
                    await session.flush()
                welcome = WelcomeSettings(chat_id=chat_id)
                session.add(welcome)
                await session.flush()
            if key == "welcome":
                welcome.welcome_enabled = not welcome.welcome_enabled
            elif key == "goodbye":
                welcome.goodbye_enabled = not welcome.goodbye_enabled
            elif key == "clean_welcome":
                welcome.clean_welcome = not welcome.clean_welcome

        elif key == "reports":
            report = await session.get(ReportSettings, chat_id)
            if report is None:
                if not await session.get(ChatModel, chat_id):
                    session.add(ChatModel(id=chat_id, title=""))
                    await session.flush()
                report = ReportSettings(chat_id=chat_id, enabled=True)
                session.add(report)
                await session.flush()
            report.enabled = not report.enabled

    # Reload and redraw the keyboard.
    data = await _load_panel_data(chat_id)
    keyboard = _build_keyboard(data, chat_id)

    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register the settings panel callback handler only.
    The /settings command is registered exclusively by settings_panel.py
    to avoid duplicate responses.
    """
    application.add_handler(
        CallbackQueryHandler(settings_callback, pattern=rf"^{_CB_PREFIX}:")
    )
    log.info("Plugin loaded: antispam_panel (settings)")
