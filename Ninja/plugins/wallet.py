"""
plugins/wallet.py — Global currency system for all games.

Coins are completely independent of Castle resources (CastleResources.gold).

Commands:
  /wallet         — View wallet balance
  /daily          — Daily reward (10 coins every 24 hours)

Internal API for other plugins:
  get_wallet(session, user_id)               → Wallet
  add_coins(session, user_id, amount)        → Wallet
  deduct_coins(session, user_id, amount)     → Wallet | None  (None = insufficient balance)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from database.engine import get_session
from core.game_wallet import add_coins, deduct_coins, get_wallet  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)

DAILY_REWARD   = 10
DAILY_COOLDOWN = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        wallet = await get_wallet(session, user.id)
        last_daily = wallet.last_daily_at
        now = datetime.now(tz=timezone.utc)

        if last_daily and (now - last_daily) < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last_daily)
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            mins = rem // 60
            daily_line = f"⏳ Daily reward in <b>{hours}h {mins}m</b>"
        else:
            daily_line = "✅ Daily reward ready — use /daily"

        text = (
            f"👛 <b>{user.first_name}'s Wallet</b>\n\n"
            f"💰 Current Balance: <b>{wallet.coins:,} coins</b>\n"
            f"📈 Total Earned: <b>{wallet.total_earned:,} coins</b>\n\n"
            f"{daily_line}"
        )
    await update.message.reply_text(text, parse_mode="HTML")


async def _cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        wallet = await get_wallet(session, user.id)
        now = datetime.now(tz=timezone.utc)

        if wallet.last_daily_at and (now - wallet.last_daily_at) < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - wallet.last_daily_at)
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            mins = rem // 60
            await update.message.reply_text(
                f"⏳ You have already received your reward today.\n"
                f"Come back in <b>{hours} hours and {mins} minutes</b>.",
                parse_mode="HTML",
            )
            return

        wallet.coins        += DAILY_REWARD
        wallet.total_earned += DAILY_REWARD
        wallet.last_daily_at = now

    await update.message.reply_text(
        f"🎁 <b>Your Daily Reward!</b>\n\n"
        f"You received <b>{DAILY_REWARD} coins</b> 💰\n"
        f"Your balance is now: <b>{wallet.coins:,} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("wallet", _cmd_wallet))
    application.add_handler(CommandHandler("daily",  _cmd_daily))
    logger.info("wallet plugin registered.")
