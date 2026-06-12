"""
plugins/account_age.py — Account age gate for new members.

Estimates a joining user's account age using Telegram user ID milestones
(IDs are assigned sequentially so they correlate with creation date).
New accounts below the configured minimum are kicked or muted.

Commands (admins only):
  /setage <days> [kick|restrict]  — Enable with min age + action
  /setage off                     — Disable
  /setage status                  — Show current config

Supported actions:
  kick      — Remove the user from the group immediately (they can rejoin later)
  restrict  — Mute the user (send-message forbidden) until manually lifted
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import bot_admin, user_admin
from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import AccountAgeSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ID → approximate creation date milestones
# Source: community-verified Telegram ID observatory data
# ---------------------------------------------------------------------------
_ID_MILESTONES: list[tuple[int, date]] = [
    (100_000_000,  date(2013, 8,  1)),
    (200_000_000,  date(2014, 12, 1)),
    (300_000_000,  date(2016, 1,  1)),
    (400_000_000,  date(2016, 11, 1)),
    (500_000_000,  date(2017, 8,  1)),
    (600_000_000,  date(2018, 5,  1)),
    (700_000_000,  date(2018, 11, 1)),
    (800_000_000,  date(2019, 4,  1)),
    (900_000_000,  date(2019, 8,  1)),
    (1_000_000_000, date(2019, 11, 1)),
    (1_200_000_000, date(2020, 5,  1)),
    (1_500_000_000, date(2020, 12, 1)),
    (2_000_000_000, date(2021, 7,  1)),
    (2_500_000_000, date(2021, 12, 1)),
    (3_000_000_000, date(2022, 5,  1)),
    (4_000_000_000, date(2022, 12, 1)),
    (5_000_000_000, date(2023, 6,  1)),
    (6_000_000_000, date(2023, 12, 1)),
    (7_000_000_000, date(2024, 8,  1)),
    (8_000_000_000, date(2025, 2,  1)),
    (9_000_000_000, date(2025, 10, 1)),
]


def estimate_account_age_days(user_id: int) -> int:
    """
    Estimate how many days old a Telegram account is using ID milestones.

    Linear interpolation between neighbouring milestone points gives a
    reasonable ±2-month accuracy for most real accounts.
    Returns 0 for bots or extremely new accounts.
    """
    today = date.today()
    prev_id: int = 0
    prev_dt: date = date(2013, 1, 1)

    for threshold_id, threshold_dt in _ID_MILESTONES:
        if user_id < threshold_id:
            id_span = threshold_id - prev_id
            day_span = (threshold_dt - prev_dt).days
            if id_span <= 0:
                return (today - prev_dt).days
            frac = (user_id - prev_id) / id_span
            est = prev_dt + timedelta(days=int(frac * day_span))
            return max(0, (today - est).days)
        prev_id, prev_dt = threshold_id, threshold_dt

    # Beyond last milestone — very recent account
    last_dt = _ID_MILESTONES[-1][1]
    return max(0, (today - last_dt).days)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> AccountAgeSettings:
    row = await session.get(AccountAgeSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = AccountAgeSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def cmd_setage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    lang = await get_chat_lang(chat.id)
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(t("age_gate_off", lang), parse_mode=ParseMode.HTML)
            return

        if args and args[0].lower() == "status":
            state = t("enabled", lang) if cfg.enabled else t("disabled", lang)
            await update.message.reply_text(
                t("age_gate_status", lang,
                  state=state,
                  min_days=cfg.min_days,
                  action=cfg.action),
                parse_mode=ParseMode.HTML,
            )
            return

        # /setage <days> [kick|restrict]
        if not args or not args[0].isdigit():
            await update.message.reply_text(t("age_gate_usage", lang), parse_mode=ParseMode.HTML)
            return

        days = int(args[0])
        if not (1 <= days <= 365):
            await update.message.reply_text(t("age_gate_invalid", lang), parse_mode=ParseMode.HTML)
            return

        action = "kick"
        if len(args) >= 2 and args[1].lower() in ("kick", "restrict"):
            action = args[1].lower()

        cfg.enabled = True
        cfg.min_days = days
        cfg.action = action
        await session.commit()

    await update.message.reply_text(
        t("age_gate_on", lang, min_days=days, action=action),
        parse_mode=ParseMode.HTML,
    )


async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check age of every new member; act if they're too new."""
    message = update.message
    if not message or not message.new_chat_members:
        return

    chat = update.effective_chat

    async with get_session() as session:
        cfg = await session.get(AccountAgeSettings, chat.id)
        if not cfg or not cfg.enabled:
            return

        lang = await get_chat_lang(chat.id)

        for user in message.new_chat_members:
            if user.is_bot:
                continue

            age_days = estimate_account_age_days(user.id)
            if age_days >= cfg.min_days:
                continue

            mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

            try:
                if cfg.action == "kick":
                    # ban removes the user from the group; unban allows them to rejoin later
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.unban_chat_member(chat.id, user.id)
                    await message.reply_text(
                        t("age_gate_kicked", lang,
                          mention=mention,
                          age=age_days,
                          min_days=cfg.min_days),
                        parse_mode=ParseMode.HTML,
                    )
                    logger.info(
                        "account_age: kicked user %d (age≈%d days) from chat %d",
                        user.id, age_days, chat.id,
                    )
                else:  # restrict
                    await chat.restrict_member(
                        user.id,
                        ChatPermissions(can_send_messages=False),
                    )
                    await message.reply_text(
                        t("age_gate_kicked", lang,
                          mention=mention,
                          age=age_days,
                          min_days=cfg.min_days),
                        parse_mode=ParseMode.HTML,
                    )
                    logger.info(
                        "account_age: restricted user %d (age≈%d days) in chat %d",
                        user.id, age_days, chat.id,
                    )
            except (BadRequest, TelegramError) as exc:
                logger.warning("account_age: failed action on user %d: %s", user.id, exc)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("setage", cmd_setage, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            handle_new_member,
        )
    )
    logger.info("Plugin loaded: account_age")
