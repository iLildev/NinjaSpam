"""
plugins/settings_panel.py — Interactive inline-button settings panel.

Provides /settings which opens a multi-level configuration menu entirely
driven by CallbackQueryHandlers.  No text commands are needed for any toggle.

Menu tree:
  /settings → Main (6 categories)
    ├── 🛡️ Spam Protection → bayes / regex / spamwatch / astro / threshold / actions
    ├── 🤖 CAPTCHA         → toggle / type / timeout / mute / kick
    ├── 👋 Welcome         → welcome / goodbye toggles + text editors
    ├── ⚠️ Warns           → limit / action / expiry / reasons
    ├── 🔒 Locks           → 14 content-type toggles
    └── ⚙️ General         → language / log channel / cas / gban

Callback data format:  ``sp:{section}:{action}:{value}``
Conversation state for text-input prompts is tracked in ``context.user_data``.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin
from core.i18n import get_chat_lang, t, set_chat_lang, SUPPORTED_LANGUAGES
from database.engine import get_session
from database.models import ChatFeatureSettings, Chat
from database.models_extra import (
    AntiLinkMode,
    AntiLinkSettings,
    AntiRaidSettings,
    ChatLanguage,
    LockSettings,
    LogChannelSettings,
    ReportSettings,
    WarnReason,
    WelcomeSettings,
)

log = logging.getLogger(__name__)

# ConversationHandler states
_WAITING_THRESHOLD = "sp_threshold"
_WAITING_CAPTCHA_TIMEOUT = "sp_cap_timeout"
_WAITING_WELCOME_TEXT = "sp_welcome_text"
_WAITING_GOODBYE_TEXT = "sp_goodbye_text"
_WAITING_WARN_LIMIT = "sp_warn_limit"
_WAITING_WARN_EXPIRY = "sp_warn_expiry"
_WAITING_WARN_REASON = "sp_warn_reason"
_WAITING_LOG_CHANNEL = "sp_log_channel"
_WAITING_FLOOD_LIMIT = "sp_flood_limit"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_create_feat(
    chat_id: int, session
) -> ChatFeatureSettings:
    """Return ChatFeatureSettings for chat_id, creating it if absent."""
    result = await session.execute(
        select(ChatFeatureSettings).where(ChatFeatureSettings.chat_id == chat_id)
    )
    feat = result.scalar_one_or_none()
    if feat is None:
        feat = ChatFeatureSettings(chat_id=chat_id)
        session.add(feat)
        await session.flush()
    return feat


async def _get_or_create_locks(chat_id: int, session) -> LockSettings:
    result = await session.execute(
        select(LockSettings).where(LockSettings.chat_id == chat_id)
    )
    locks = result.scalar_one_or_none()
    if locks is None:
        locks = LockSettings(chat_id=chat_id)
        session.add(locks)
        await session.flush()
    return locks


def _tog(value: bool) -> str:
    """Return a toggle emoji based on boolean value."""
    return "✅" if value else "✗"


def _action_label(action: str) -> str:
    labels = {
        "delete": "🗑️ Delete",
        "delete_warn": "⚠️ Delete+Warn",
        "delete_mute": "🔇 Delete+Mute",
        "delete_ban": "🔨 Delete+Ban",
    }
    return labels.get(str(action), str(action))


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

async def _main_menu(chat_id: int, chat_title: str, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    text = t("settings_title", lang, chat_title=chat_title)
    text += f"\n\n{t('settings_choose', lang)}"
    keyboard = [
        [
            InlineKeyboardButton(t("settings_spam", lang), callback_data="sp:spam:menu:"),
            InlineKeyboardButton(t("settings_captcha", lang), callback_data="sp:captcha:menu:"),
        ],
        [
            InlineKeyboardButton(t("settings_welcome", lang), callback_data="sp:welcome:menu:"),
            InlineKeyboardButton(t("settings_warns", lang), callback_data="sp:warns:menu:"),
        ],
        [
            InlineKeyboardButton(t("settings_locks", lang), callback_data="sp:locks:menu:"),
            InlineKeyboardButton(t("settings_general", lang), callback_data="sp:general:menu:"),
        ],
        [
            InlineKeyboardButton(t("settings_moderation", lang), callback_data="sp:moderation:menu:"),
        ],
        [InlineKeyboardButton(t("close", lang), callback_data="sp:main:close:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Spam Protection menu
# ---------------------------------------------------------------------------

async def _spam_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        feat = await _get_or_create_feat(chat_id, session)
        bayes_on = feat.bayes_filter_enabled
        regex_on = feat.regex_filter_enabled
        # SpamWatch and astroturfing flags from extra settings
        from database.models_extra import SpamWatchSettings, AstroSettings
        sw_res = await session.execute(
            select(SpamWatchSettings).where(SpamWatchSettings.chat_id == chat_id)
        )
        sw = sw_res.scalar_one_or_none()
        sw_on = sw.enabled if sw else False

        astro_res = await session.execute(
            select(AstroSettings).where(AstroSettings.chat_id == chat_id)
        )
        astro = astro_res.scalar_one_or_none()
        astro_on = astro.enabled if astro else False

        threshold = feat.bayes_spam_threshold_override or 0.90
        bayes_action = str(feat.bayes_spam_action.value) if feat.bayes_spam_action else "delete_warn"
        regex_action = str(feat.regex_spam_action.value) if feat.regex_spam_action else "delete_warn"

    text = t("spam_menu_title", lang)
    keyboard = [
        [
            InlineKeyboardButton(
                f"{_tog(bayes_on)} {t('spam_bayes', lang)}",
                callback_data="sp:spam:toggle_bayes:"
            ),
            InlineKeyboardButton(
                f"{_tog(regex_on)} {t('spam_regex', lang)}",
                callback_data="sp:spam:toggle_regex:"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{_tog(sw_on)} {t('spam_spamwatch', lang)}",
                callback_data="sp:spam:toggle_sw:"
            ),
            InlineKeyboardButton(
                f"{_tog(astro_on)} {t('spam_astro', lang)}",
                callback_data="sp:spam:toggle_astro:"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{t('spam_threshold', lang)}: {threshold:.2f}",
                callback_data="sp:spam:set_threshold:"
            ),
        ],
        [
            InlineKeyboardButton(
                f"AI: {_action_label(bayes_action)}",
                callback_data="sp:spam:cycle_bayes_action:"
            ),
            InlineKeyboardButton(
                f"Regex: {_action_label(regex_action)}",
                callback_data="sp:spam:cycle_regex_action:"
            ),
        ],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# CAPTCHA menu
# ---------------------------------------------------------------------------

async def _captcha_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        feat = await _get_or_create_feat(chat_id, session)
        cap_on = feat.captcha_enabled
        cap_type = str(feat.captcha_type.value) if feat.captcha_type else "button"
        timeout = feat.captcha_timeout_override or 120
        mute_on = feat.captcha_mute_until_verified
        kick_on = feat.captcha_kick_on_timeout

    type_labels = {
        "button": t("captcha_type_btn", lang),
        "math": t("captcha_type_math", lang),
        "text": t("captcha_type_text", lang),
        "adaptive": t("captcha_type_adaptive", lang),
    }
    type_cycle = {"button": "math", "math": "text", "text": "adaptive", "adaptive": "button"}

    text = t("captcha_menu_title", lang)
    keyboard = [
        [InlineKeyboardButton(
            f"{_tog(cap_on)} {t('captcha_toggle', lang)}",
            callback_data="sp:captcha:toggle:"
        )],
        [InlineKeyboardButton(
            f"📋 {type_labels.get(cap_type, cap_type)}",
            callback_data=f"sp:captcha:set_type:{type_cycle.get(cap_type, 'button')}"
        )],
        [
            InlineKeyboardButton(
                f"⏱️ {timeout}s",
                callback_data="sp:captcha:set_timeout:"
            ),
            InlineKeyboardButton(
                f"{_tog(mute_on)} {t('captcha_mute_btn', lang)}",
                callback_data="sp:captcha:toggle_mute:"
            ),
        ],
        [InlineKeyboardButton(
            f"{_tog(kick_on)} {t('captcha_kick_btn', lang)}",
            callback_data="sp:captcha:toggle_kick:"
        )],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Welcome menu
# ---------------------------------------------------------------------------

async def _welcome_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        from database.models_extra import WelcomeSettings
        ws_res = await session.execute(
            select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
        )
        ws = ws_res.scalar_one_or_none()
        welcome_on = ws.welcome_enabled if ws else False
        goodbye_on = ws.goodbye_enabled if ws else False
        clean_on = ws.clean_welcome if ws else False

    text = t("welcome_menu_title", lang)
    keyboard = [
        [
            InlineKeyboardButton(
                f"{_tog(welcome_on)} {t('welcome_toggle', lang)}",
                callback_data="sp:welcome:toggle_welcome:"
            ),
            InlineKeyboardButton(
                f"{_tog(goodbye_on)} {t('goodbye_toggle', lang)}",
                callback_data="sp:welcome:toggle_goodbye:"
            ),
        ],
        [InlineKeyboardButton(
            f"{_tog(clean_on)} {t('clean_welcome_btn', lang)}",
            callback_data="sp:welcome:toggle_clean:"
        )],
        [
            InlineKeyboardButton(t("welcome_set_btn", lang), callback_data="sp:welcome:set_text:welcome"),
            InlineKeyboardButton(t("goodbye_set_btn", lang), callback_data="sp:welcome:set_text:goodbye"),
        ],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Warns menu
# ---------------------------------------------------------------------------

async def _warns_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        feat = await _get_or_create_feat(chat_id, session)
        limit = feat.warn_limit
        action = str(feat.warn_action.value) if feat.warn_action else "ban"
        expiry = feat.warn_expiry_days

    action_cycle = {"nothing": "mute", "mute": "kick", "kick": "ban", "ban": "nothing"}
    action_emojis = {"nothing": "🚫", "mute": "🔇", "kick": "👢", "ban": "🔨"}

    text = t("warns_menu_title", lang)
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔢 {t('warn_limit_btn', lang)}: {limit}",
                callback_data="sp:warns:set_limit:"
            ),
            InlineKeyboardButton(
                f"{action_emojis.get(action, '⚡')} {action.title()}",
                callback_data=f"sp:warns:cycle_action:{action_cycle.get(action, 'ban')}"
            ),
        ],
        [InlineKeyboardButton(
            f"⏳ {t('warn_expiry_btn', lang)}: {expiry}d",
            callback_data="sp:warns:set_expiry:"
        )],
        [InlineKeyboardButton(
            t("warn_reasons_btn", lang),
            callback_data="sp:warns:reasons_menu:"
        )],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Locks menu
# ---------------------------------------------------------------------------

async def _locks_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        locks = await _get_or_create_locks(chat_id, session)

    lock_map = [
        ("sticker", t("lock_sticker", lang), locks.sticker),
        ("gif", t("lock_gif", lang), locks.gif),
        ("photo", t("lock_photo", lang), locks.photo),
        ("video", t("lock_video", lang), locks.video),
        ("audio", t("lock_audio", lang), locks.audio),
        ("document", t("lock_document", lang), locks.document),
        ("voice", t("lock_voice", lang), locks.voice),
        ("videonote", t("lock_videonote", lang), locks.videonote),
        ("contact", t("lock_contact", lang), locks.contact),
        ("location", t("lock_location", lang), locks.location),
        ("poll", t("lock_poll", lang), locks.poll),
        ("forward", t("lock_forward", lang), locks.forward),
        ("url", t("lock_link", lang), locks.url),
        ("game", t("lock_game", lang), locks.game),
    ]

    text = t("locks_menu_title", lang)
    keyboard = []
    # Build 2-column grid
    row: list[InlineKeyboardButton] = []
    for key, label, state in lock_map:
        row.append(InlineKeyboardButton(
            f"{_tog(state)} {label}",
            callback_data=f"sp:locks:toggle:{key}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")])
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# General menu
# ---------------------------------------------------------------------------

async def _general_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        feat = await _get_or_create_feat(chat_id, session)
        cas_on = feat.cas_enabled

        log_res = await session.execute(
            select(LogChannelSettings).where(LogChannelSettings.chat_id == chat_id)
        )
        log_setting = log_res.scalar_one_or_none()
        has_log = log_setting is not None

        gban_res = await session.execute(
            select(LogChannelSettings).where(LogChannelSettings.chat_id == chat_id)
        )
        from database.models_extra import ChatGbanToggle
        gban_res2 = await session.execute(
            select(ChatGbanToggle).where(ChatGbanToggle.chat_id == chat_id)
        )
        gban_row = gban_res2.scalar_one_or_none()
        gban_on = gban_row.gban_enabled if gban_row else True

    text = t("general_menu_title", lang)
    keyboard = [
        [InlineKeyboardButton(t("general_language", lang), callback_data="sp:general:lang_menu:")],
        [
            InlineKeyboardButton(
                f"{_tog(cas_on)} {t('general_cas', lang)}",
                callback_data="sp:general:toggle_cas:"
            ),
            InlineKeyboardButton(
                f"{_tog(gban_on)} {t('general_gban', lang)}",
                callback_data="sp:general:toggle_gban:"
            ),
        ],
        [InlineKeyboardButton(
            f"{'✅' if has_log else '✗'} {t('general_log_channel', lang)}",
            callback_data="sp:general:set_log:"
        )],
        [InlineKeyboardButton(
            t("general_rules", lang),
            callback_data="sp:general:rules:"
        )],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def _moderation_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Flood control, anti-links, anti-raid, and reports settings."""
    async with get_session() as session:
        feat = await session.get(ChatFeatureSettings, chat_id)
        flood_on = bool(feat.flood_control_enabled) if feat else False
        flood_limit = feat.flood_messages_limit if feat else 5

        al_row = await session.get(AntiLinkSettings, chat_id)
        al_mode = al_row.mode if al_row else AntiLinkMode.OFF

        raid_row = await session.get(AntiRaidSettings, chat_id)
        raid_on = bool(raid_row.enabled) if raid_row else False

        rep_row = await session.get(ReportSettings, chat_id)
        rep_on = bool(rep_row.enabled) if rep_row else False

    # Anti-link mode cycles: OFF → INVITE → ALL
    mode_labels = {
        AntiLinkMode.OFF:    t("antilinks_mode_off",    lang),
        AntiLinkMode.INVITE: t("antilinks_mode_invite", lang),
        AntiLinkMode.ALL:    t("antilinks_mode_all",    lang),
    }
    mode_cycle = {
        AntiLinkMode.OFF:    AntiLinkMode.INVITE,
        AntiLinkMode.INVITE: AntiLinkMode.ALL,
        AntiLinkMode.ALL:    AntiLinkMode.OFF,
    }

    text = t("mod_menu_title", lang)
    keyboard = [
        [
            InlineKeyboardButton(
                f"{_tog(flood_on)} {t('mod_flood', lang)}",
                callback_data="sp:moderation:toggle_flood:",
            ),
            InlineKeyboardButton(
                t("mod_flood_limit_btn", lang, limit=flood_limit or 5),
                callback_data="sp:moderation:set_flood_limit:",
            ),
        ],
        [
            InlineKeyboardButton(
                mode_labels.get(al_mode, str(al_mode)),
                callback_data=f"sp:moderation:cycle_links:{mode_cycle.get(al_mode, AntiLinkMode.INVITE).value}",
            ),
            InlineKeyboardButton(
                f"{_tog(raid_on)} {t('mod_antiraid', lang)}",
                callback_data="sp:moderation:toggle_raid:",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{_tog(rep_on)} {t('mod_reports', lang)}",
                callback_data="sp:moderation:toggle_reports:",
            ),
        ],
        [InlineKeyboardButton(t("back", lang), callback_data="sp:main:menu:")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def _lang_menu(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    text = t("lang_select", lang)
    flags = [
        ("en", t("lang_flag_en", lang)),
        ("ar", t("lang_flag_ar", lang)),
        ("fa", t("lang_flag_fa", lang)),
        ("tr", t("lang_flag_tr", lang)),
        ("ru", t("lang_flag_ru", lang)),
        ("id", t("lang_flag_id", lang)),
    ]
    keyboard = []
    row = []
    for code, label in flags:
        row.append(InlineKeyboardButton(label, callback_data=f"sp:general:set_lang:{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(t("back", lang), callback_data="sp:general:menu:")])
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# /settings command handler
# ---------------------------------------------------------------------------

@user_admin
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_message is None:
        return
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text(t("group_only", "en"))
        return

    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)
    text, markup = await _main_menu(chat.id, chat.title or "", lang)
    await update.effective_message.reply_html(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Callback query dispatcher
# ---------------------------------------------------------------------------

async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """
    Central dispatcher for all sp: callback queries.

    Returns a ConversationHandler state string when a text-input prompt is
    sent, so that the next message from the user is captured.
    """
    query = update.callback_query
    if query is None or update.effective_chat is None or update.effective_user is None:
        return None

    await query.answer()

    # Permission check — only admins can use the panel
    from core.helpers.chat_status import is_user_admin
    if not await is_user_admin(update.effective_chat, update.effective_user.id):
        await query.answer(t("admin_only", "en"), show_alert=True)
        return None

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)

    data = query.data or ""
    parts = data.split(":", 3)
    if len(parts) < 4:
        return None

    _, section, action, value = parts

    # ── MAIN ──────────────────────────────────────────────────────────────────
    if section == "main":
        if action == "close":
            await query.message.delete()
        elif action == "menu":
            text, markup = await _main_menu(
                chat_id, update.effective_chat.title or "", lang
            )
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        return None

    # ── SPAM ──────────────────────────────────────────────────────────────────
    if section == "spam":
        if action == "menu":
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_bayes":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.bayes_filter_enabled = not feat.bayes_filter_enabled
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_regex":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.regex_filter_enabled = not feat.regex_filter_enabled
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_sw":
            from database.models_extra import SpamWatchSettings
            async with get_session() as session:
                res = await session.execute(
                    select(SpamWatchSettings).where(SpamWatchSettings.chat_id == chat_id)
                )
                sw = res.scalar_one_or_none()
                if sw is None:
                    sw = SpamWatchSettings(chat_id=chat_id, enabled=True)
                    session.add(sw)
                else:
                    sw.enabled = not sw.enabled
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_astro":
            from database.models_extra import AstroSettings
            async with get_session() as session:
                res = await session.execute(
                    select(AstroSettings).where(AstroSettings.chat_id == chat_id)
                )
                astro = res.scalar_one_or_none()
                if astro is None:
                    astro = AstroSettings(chat_id=chat_id, enabled=True)
                    session.add(astro)
                else:
                    astro.enabled = not astro.enabled
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_threshold":
            prompt = t("spam_threshold_current", lang, value="0.90")
            context.user_data["sp_state"] = _WAITING_THRESHOLD
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(prompt, parse_mode="HTML")
            return _WAITING_THRESHOLD

        elif action == "cycle_bayes_action":
            _actions = ["delete", "delete_warn", "delete_mute", "delete_ban"]
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                from database.models import SpamAction
                current = str(feat.bayes_spam_action.value)
                idx = _actions.index(current) if current in _actions else 0
                feat.bayes_spam_action = SpamAction(_actions[(idx + 1) % len(_actions)])
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "cycle_regex_action":
            _actions = ["delete", "delete_warn", "delete_mute", "delete_ban"]
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                from database.models import SpamAction
                current = str(feat.regex_spam_action.value)
                idx = _actions.index(current) if current in _actions else 0
                feat.regex_spam_action = SpamAction(_actions[(idx + 1) % len(_actions)])
            text, markup = await _spam_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        return None

    # ── CAPTCHA ───────────────────────────────────────────────────────────────
    if section == "captcha":
        if action == "menu":
            text, markup = await _captcha_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.captcha_enabled = not feat.captcha_enabled
            text, markup = await _captcha_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_type" and value:
            from database.models import CaptchaType
            valid = {t.value for t in CaptchaType} | {"adaptive"}
            if value in valid:
                async with get_session() as session:
                    feat = await _get_or_create_feat(chat_id, session)
                    if value != "adaptive":
                        feat.captcha_type = CaptchaType(value)
                    # adaptive is handled by adaptive_captcha plugin
                    from database.models_extra import AdaptiveCaptchaSettings
                    ac_res = await session.execute(
                        select(AdaptiveCaptchaSettings).where(
                            AdaptiveCaptchaSettings.chat_id == chat_id
                        )
                    )
                    ac = ac_res.scalar_one_or_none()
                    if ac is None:
                        ac = AdaptiveCaptchaSettings(chat_id=chat_id)
                        session.add(ac)
                    ac.adaptive_mode = (value == "adaptive")
            text, markup = await _captcha_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_timeout":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                current_t = feat.captcha_timeout_override or 120
            prompt = t("captcha_timeout_prompt", lang, value=str(current_t))
            context.user_data["sp_state"] = _WAITING_CAPTCHA_TIMEOUT
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(prompt, parse_mode="HTML")
            return _WAITING_CAPTCHA_TIMEOUT

        elif action == "toggle_mute":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.captcha_mute_until_verified = not feat.captcha_mute_until_verified
            text, markup = await _captcha_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_kick":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.captcha_kick_on_timeout = not feat.captcha_kick_on_timeout
            text, markup = await _captcha_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        return None

    # ── WELCOME ───────────────────────────────────────────────────────────────
    if section == "welcome":
        if action == "menu":
            text, markup = await _welcome_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_welcome":
            from database.models_extra import WelcomeSettings
            async with get_session() as session:
                res = await session.execute(
                    select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
                )
                ws = res.scalar_one_or_none()
                if ws is None:
                    ws = WelcomeSettings(chat_id=chat_id, welcome_enabled=True)
                    session.add(ws)
                else:
                    ws.welcome_enabled = not ws.welcome_enabled
            text, markup = await _welcome_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_goodbye":
            from database.models_extra import WelcomeSettings
            async with get_session() as session:
                res = await session.execute(
                    select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
                )
                ws = res.scalar_one_or_none()
                if ws is None:
                    ws = WelcomeSettings(chat_id=chat_id, goodbye_enabled=True)
                    session.add(ws)
                else:
                    ws.goodbye_enabled = not ws.goodbye_enabled
            text, markup = await _welcome_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_clean":
            from database.models_extra import WelcomeSettings
            async with get_session() as session:
                res = await session.execute(
                    select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
                )
                ws = res.scalar_one_or_none()
                if ws is None:
                    ws = WelcomeSettings(chat_id=chat_id, clean_welcome=True)
                    session.add(ws)
                else:
                    ws.clean_welcome = not ws.clean_welcome
            text, markup = await _welcome_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_text":
            kind = value  # "welcome" or "goodbye"
            prompt_key = "welcome_prompt" if kind == "welcome" else "goodbye_prompt"
            state_key = _WAITING_WELCOME_TEXT if kind == "welcome" else _WAITING_GOODBYE_TEXT
            context.user_data["sp_state"] = state_key
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(t(prompt_key, lang), parse_mode="HTML")
            return state_key

        return None

    # ── WARNS ─────────────────────────────────────────────────────────────────
    if section == "warns":
        if action == "menu":
            text, markup = await _warns_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_limit":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                current = feat.warn_limit
            context.user_data["sp_state"] = _WAITING_WARN_LIMIT
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(
                t("warn_limit_prompt", lang, value=str(current)), parse_mode="HTML"
            )
            return _WAITING_WARN_LIMIT

        elif action == "cycle_action" and value:
            from database.models import WarnAction
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.warn_action = WarnAction(value)
            text, markup = await _warns_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_expiry":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                current = feat.warn_expiry_days
            context.user_data["sp_state"] = _WAITING_WARN_EXPIRY
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(
                t("warn_expiry_prompt", lang, value=str(current)), parse_mode="HTML"
            )
            return _WAITING_WARN_EXPIRY

        elif action == "reasons_menu":
            text, markup = await _warn_reasons_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        return None

    # ── LOCKS ─────────────────────────────────────────────────────────────────
    if section == "locks":
        if action == "menu":
            text, markup = await _locks_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle" and value:
            lock_field = value  # e.g. "sticker", "gif", ...
            async with get_session() as session:
                locks = await _get_or_create_locks(chat_id, session)
                current = getattr(locks, lock_field, False)
                setattr(locks, lock_field, not current)
            text, markup = await _locks_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        return None

    # ── GENERAL ───────────────────────────────────────────────────────────────
    if section == "general":
        if action == "menu":
            text, markup = await _general_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "lang_menu":
            text, markup = _lang_menu(lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_lang" and value:
            if value in SUPPORTED_LANGUAGES:
                await set_chat_lang(chat_id, value)
                new_lang = value
            else:
                new_lang = lang
            # Refresh general menu in new language
            text, markup = await _general_menu(chat_id, new_lang)
            await query.edit_message_text(
                t("lang_changed", new_lang) + "\n\n" + text,
                reply_markup=markup,
                parse_mode="HTML"
            )

        elif action == "toggle_cas":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.cas_enabled = not feat.cas_enabled
            text, markup = await _general_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_gban":
            from database.models_extra import ChatGbanToggle
            async with get_session() as session:
                res = await session.execute(
                    select(ChatGbanToggle).where(ChatGbanToggle.chat_id == chat_id)
                )
                gban = res.scalar_one_or_none()
                if gban is None:
                    gban = ChatGbanToggle(chat_id=chat_id, gban_enabled=False)
                    session.add(gban)
                else:
                    gban.gban_enabled = not gban.gban_enabled
            text, markup = await _general_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_log":
            context.user_data["sp_state"] = _WAITING_LOG_CHANNEL
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(t("log_channel_prompt", lang), parse_mode="HTML")
            return _WAITING_LOG_CHANNEL

        elif action == "rules":
            await query.answer("Use /setrules <text> to set group rules.", show_alert=True)

        return None

    # ── MODERATION ────────────────────────────────────────────────────────────
    if section == "moderation":
        if action == "menu":
            text, markup = await _moderation_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_flood":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                feat.flood_control_enabled = not feat.flood_control_enabled
                if feat.flood_control_enabled and not feat.flood_messages_limit:
                    feat.flood_messages_limit = 5
            text, markup = await _moderation_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "set_flood_limit":
            async with get_session() as session:
                feat = await _get_or_create_feat(chat_id, session)
                current_limit = feat.flood_messages_limit or 5
            context.user_data["sp_state"] = _WAITING_FLOOD_LIMIT
            context.user_data["sp_chat_id"] = chat_id
            context.user_data["sp_msg_id"] = query.message.message_id
            await query.edit_message_text(
                t("mod_flood_limit_prompt", lang, value=str(current_limit)),
                parse_mode="HTML",
            )
            return _WAITING_FLOOD_LIMIT

        elif action == "cycle_links" and value:
            async with get_session() as session:
                al = await session.get(AntiLinkSettings, chat_id)
                if al is None:
                    al = AntiLinkSettings(chat_id=chat_id, mode=AntiLinkMode(value))
                    session.add(al)
                else:
                    try:
                        al.mode = AntiLinkMode(value)
                    except ValueError:
                        al.mode = AntiLinkMode.OFF
            text, markup = await _moderation_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_raid":
            async with get_session() as session:
                raid = await session.get(AntiRaidSettings, chat_id)
                if raid is None:
                    raid = AntiRaidSettings(chat_id=chat_id, enabled=True)
                    session.add(raid)
                else:
                    raid.enabled = not raid.enabled
            text, markup = await _moderation_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        elif action == "toggle_reports":
            async with get_session() as session:
                rep = await session.get(ReportSettings, chat_id)
                if rep is None:
                    rep = ReportSettings(chat_id=chat_id, enabled=True)
                    session.add(rep)
                else:
                    rep.enabled = not rep.enabled
            text, markup = await _moderation_menu(chat_id, lang)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

        return None

    return None


# ---------------------------------------------------------------------------
# Warn reasons sub-menu
# ---------------------------------------------------------------------------

async def _warn_reasons_menu(chat_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        res = await session.execute(
            select(WarnReason).where(WarnReason.chat_id == chat_id).order_by(WarnReason.id)
        )
        reasons = res.scalars().all()

    text = t("warn_reasons_title", lang)
    keyboard = []
    if not reasons:
        text += f"\n\n{t('warn_reasons_empty', lang)}"
    else:
        for r in reasons:
            keyboard.append([
                InlineKeyboardButton(f"📝 {r.reason}", callback_data=f"sp:warns:noop:"),
                InlineKeyboardButton("🗑️", callback_data=f"sp:warns:del_reason:{r.id}"),
            ])

    keyboard.append([
        InlineKeyboardButton("➕ Add Reason", callback_data="sp:warns:add_reason:"),
    ])
    keyboard.append([InlineKeyboardButton(t("back", lang), callback_data="sp:warns:menu:")])
    return text, InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Text-input state handlers
# ---------------------------------------------------------------------------

async def _handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Handle free-text replies for settings that require text input."""
    if update.effective_message is None:
        return ConversationHandler.END

    state = context.user_data.get("sp_state")
    chat_id = context.user_data.get("sp_chat_id")
    text = (update.effective_message.text or "").strip()

    if not state or not chat_id:
        return ConversationHandler.END

    lang = await get_chat_lang(chat_id)

    if state == _WAITING_THRESHOLD:
        try:
            val = float(text)
            assert 0.5 <= val <= 0.99
        except (ValueError, AssertionError):
            await update.effective_message.reply_text(t("spam_threshold_invalid", lang))
            return _WAITING_THRESHOLD
        async with get_session() as session:
            feat = await _get_or_create_feat(chat_id, session)
            feat.bayes_spam_threshold_override = val
        await update.effective_message.reply_text(t("spam_threshold_set", lang, value=f"{val:.2f}"))

    elif state == _WAITING_CAPTCHA_TIMEOUT:
        try:
            val = int(text)
            assert 30 <= val <= 3600
        except (ValueError, AssertionError):
            await update.effective_message.reply_text(t("captcha_timeout_invalid", lang))
            return _WAITING_CAPTCHA_TIMEOUT
        async with get_session() as session:
            feat = await _get_or_create_feat(chat_id, session)
            feat.captcha_timeout_override = val
        await update.effective_message.reply_text(t("captcha_timeout_set", lang, value=str(val)))

    elif state == _WAITING_WELCOME_TEXT:
        from database.models_extra import WelcomeSettings
        async with get_session() as session:
            res = await session.execute(
                select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
            )
            ws = res.scalar_one_or_none()
            if ws is None:
                ws = WelcomeSettings(chat_id=chat_id)
                session.add(ws)
            ws.welcome_text = text
        await update.effective_message.reply_text(t("welcome_set", lang))

    elif state == _WAITING_GOODBYE_TEXT:
        from database.models_extra import WelcomeSettings
        async with get_session() as session:
            res = await session.execute(
                select(WelcomeSettings).where(WelcomeSettings.chat_id == chat_id)
            )
            ws = res.scalar_one_or_none()
            if ws is None:
                ws = WelcomeSettings(chat_id=chat_id)
                session.add(ws)
            ws.goodbye_text = text
        await update.effective_message.reply_text(t("goodbye_set", lang))

    elif state == _WAITING_WARN_LIMIT:
        try:
            val = int(text)
            assert 1 <= val <= 10
        except (ValueError, AssertionError):
            await update.effective_message.reply_text(t("warn_limit_invalid", lang))
            return _WAITING_WARN_LIMIT
        async with get_session() as session:
            feat = await _get_or_create_feat(chat_id, session)
            feat.warn_limit = val
        await update.effective_message.reply_text(t("warn_limit_set", lang, value=str(val)))

    elif state == _WAITING_WARN_EXPIRY:
        try:
            val = int(text)
            assert 0 <= val <= 365
        except (ValueError, AssertionError):
            await update.effective_message.reply_text("Invalid. Send 0–365.")
            return _WAITING_WARN_EXPIRY
        async with get_session() as session:
            feat = await _get_or_create_feat(chat_id, session)
            feat.warn_expiry_days = val
        await update.effective_message.reply_text(t("warn_expiry_set", lang, value=str(val)))

    elif state == _WAITING_WARN_REASON:
        if len(text) > 128:
            await update.effective_message.reply_text("Reason too long (max 128 chars).")
            return _WAITING_WARN_REASON
        async with get_session() as session:
            reason = WarnReason(chat_id=chat_id, reason=text)
            session.add(reason)
        await update.effective_message.reply_text(t("warn_reason_added", lang, reason=text))

    elif state == _WAITING_FLOOD_LIMIT:
        try:
            val = int(text)
            assert 3 <= val <= 50
        except (ValueError, AssertionError):
            await update.effective_message.reply_text(t("mod_flood_limit_invalid", lang))
            return _WAITING_FLOOD_LIMIT
        async with get_session() as session:
            feat = await _get_or_create_feat(chat_id, session)
            feat.flood_messages_limit = val
            if not feat.flood_control_enabled:
                feat.flood_control_enabled = True
        await update.effective_message.reply_text(t("mod_flood_limit_set", lang, value=str(val)))

    elif state == _WAITING_LOG_CHANNEL:
        # Accept forwarded message from channel or numeric ID
        log_id: Optional[int] = None
        if update.effective_message.forward_origin:
            origin = update.effective_message.forward_origin
            if hasattr(origin, "chat") and origin.chat:
                log_id = origin.chat.id
        if log_id is None:
            try:
                log_id = int(text)
            except ValueError:
                await update.effective_message.reply_text("Invalid channel ID.")
                return _WAITING_LOG_CHANNEL

        async with get_session() as session:
            res = await session.execute(
                select(LogChannelSettings).where(LogChannelSettings.chat_id == chat_id)
            )
            ls = res.scalar_one_or_none()
            if ls is None:
                ls = LogChannelSettings(chat_id=chat_id, log_channel_id=log_id)
                session.add(ls)
            else:
                ls.log_channel_id = log_id
        await update.effective_message.reply_text(
            t("log_channel_set", lang, channel=str(log_id))
        )

    context.user_data.pop("sp_state", None)
    context.user_data.pop("sp_chat_id", None)
    context.user_data.pop("sp_msg_id", None)
    return ConversationHandler.END


async def _handle_reason_callbacks(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle warn reason add/delete callbacks."""
    query = update.callback_query
    if query is None or update.effective_chat is None:
        return
    await query.answer()

    chat_id = update.effective_chat.id
    lang = await get_chat_lang(chat_id)
    data = query.data or ""
    parts = data.split(":", 3)
    action = parts[2] if len(parts) > 2 else ""
    value = parts[3] if len(parts) > 3 else ""

    if action == "add_reason":
        context.user_data["sp_state"] = _WAITING_WARN_REASON
        context.user_data["sp_chat_id"] = chat_id
        await query.edit_message_text(t("warn_reason_add_prompt", lang), parse_mode="HTML")

    elif action == "del_reason" and value:
        try:
            reason_id = int(value)
            async with get_session() as session:
                res = await session.execute(
                    select(WarnReason).where(
                        WarnReason.id == reason_id,
                        WarnReason.chat_id == chat_id,
                    )
                )
                row = res.scalar_one_or_none()
                if row:
                    await session.delete(row)
        except Exception:
            pass
        text, markup = await _warn_reasons_menu(chat_id, lang)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register the settings panel handlers."""
    # Main command
    application.add_handler(
        CommandHandler("settings", cmd_settings, filters=filters.ChatType.GROUPS),
        group=10,
    )
    # Callback dispatcher for sp: prefix queries (exclude reason callbacks handled separately)
    application.add_handler(
        CallbackQueryHandler(
            _handle_callback,
            pattern=r"^sp:(?!warns:(add_reason|del_reason|noop))",
        ),
        group=10,
    )
    # Reason-specific callbacks
    application.add_handler(
        CallbackQueryHandler(
            _handle_reason_callbacks,
            pattern=r"^sp:warns:(add_reason|del_reason|noop):",
        ),
        group=10,
    )
    # Text-input conversation (lower priority group so normal commands take precedence)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            _handle_text_input,
        ),
        group=11,
    )

    log.info("Plugin loaded: settings_panel")
