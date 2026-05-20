"""
plugins/wallet.py — نظام المال العام لجميع الألعاب.

العملات (coins) مستقلة تماماً عن ذهب القلعة (CastleResources.gold).

الأوامر:
  /wallet         — عرض رصيد المحفظة
  /daily          — مكافأة يومية (10 عملات كل 24 ساعة)

واجهة برمجية داخلية للإضافات الأخرى:
  get_wallet(session, user_id)               → Wallet
  add_coins(session, user_id, amount)        → Wallet
  deduct_coins(session, user_id, amount)     → Wallet | None  (None = رصيد غير كافٍ)
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
            daily_line = f"⏳ المكافأة اليومية بعد <b>{hours}س {mins}د</b>"
        else:
            daily_line = "✅ المكافأة اليومية جاهزة — استخدم /daily"

        text = (
            f"👛 <b>محفظة {user.first_name}</b>\n\n"
            f"💰 الرصيد الحالي: <b>{wallet.coins:,} عملة</b>\n"
            f"📈 إجمالي المكتسب: <b>{wallet.total_earned:,} عملة</b>\n\n"
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
                f"⏳ لقد استلمت مكافأتك اليوم.\n"
                f"عُد بعد <b>{hours} ساعة و{mins} دقيقة</b>.",
                parse_mode="HTML",
            )
            return

        wallet.coins        += DAILY_REWARD
        wallet.total_earned += DAILY_REWARD
        wallet.last_daily_at = now

    await update.message.reply_text(
        f"🎁 <b>مكافأتك اليومية!</b>\n\n"
        f"حصلت على <b>{DAILY_REWARD} عملات</b> 💰\n"
        f"رصيدك الآن: <b>{wallet.coins:,} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("wallet", _cmd_wallet))
    application.add_handler(CommandHandler("daily",  _cmd_daily))
    logger.info("wallet plugin registered.")
