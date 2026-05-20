"""
economy/loans.py — نظام الدين والسجن.

الأوامر:
  /loan <amount>  — اقتراض (يتطلب حساباً بنكياً، قرض واحد فقط)
  /repay <amount> — سداد جزئي أو كلي (أو /repay all)
  /myloan         — عرض حالة القرض الحالي
  /debtors        — قائمة المتأخرين في السداد
  /bail           — دفع كفالة للخروج من السجن
  /bailout        — دفع كفالة شخص آخر (رد على رسالته)
  /myjail         — عرض حالة السجن
"""

from __future__ import annotations

import logging
from datetime import timedelta, timezone, datetime

from sqlalchemy import select, desc
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.game_wallet import add_coins, deduct_coins, get_wallet
from database.engine import get_session
from economy.helpers import (
    auto_jail_if_overdue,
    check_jailed_and_reply,
    fmt_coins,
    fmt_user,
    get_active_loan,
    get_bank_account_by_user,
    get_jail,
    is_jailed,
    jail_user,
    release_user,
)
from economy.models import LoanRecord

logger = logging.getLogger(__name__)

LOAN_MAX        = 3000
LOAN_INTEREST   = 0.10
LOAN_HOURS      = 24
BAIL_AMOUNT     = 300


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# /loan
# ---------------------------------------------------------------------------

async def cmd_loan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args

    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            f"🏦 <b>تسليف</b>\n\n"
            f"الاستخدام: /loan <المبلغ>\n"
            f"مثال: /loan 1000\n\n"
            f"• الحد الأقصى: <b>{fmt_coins(LOAN_MAX)} عملة</b>\n"
            f"• الفائدة: <b>10%</b>\n"
            f"• مهلة السداد: <b>24 ساعة</b>\n"
            f"• التأخر = سجن فوري 🔒",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    if amount > LOAN_MAX:
        await update.message.reply_text(
            f"❌ الحد الأقصى للقرض هو <b>{fmt_coins(LOAN_MAX)} عملة</b>.",
            parse_mode="HTML",
        )
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ يجب أن يكون لديك حساب بنكي لاستخدام خدمة التسليف.\n"
                "افتح حساباً بـ /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await check_jailed_and_reply(update, session, user.id)
            return

        existing = await get_active_loan(session, user.id)
        if existing:
            await update.message.reply_text(
                f"❌ لديك قرض قائم بالفعل!\n\n"
                f"المبلغ المتبقي: <b>{fmt_coins(existing.remaining)} عملة</b>\n"
                f"موعد السداد: {existing.deadline.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
                f"اسدد قرضك أولاً بـ /repay",
                parse_mode="HTML",
            )
            return

        total_due = int(amount * (1 + LOAN_INTEREST))
        deadline  = _utcnow() + timedelta(hours=LOAN_HOURS)
        loan = LoanRecord(
            user_id=user.id,
            principal=amount,
            total_due=total_due,
            amount_repaid=0,
            deadline=deadline,
            is_repaid=False,
        )
        session.add(loan)
        wallet = await add_coins(session, user.id, amount)

    await update.message.reply_text(
        f"💸 <b>تم التسليف!</b>\n\n"
        f"حصلت على: <b>{fmt_coins(amount)} عملة</b>\n"
        f"إجمالي الدين (مع فائدة 10%): <b>{fmt_coins(total_due)} عملة</b>\n"
        f"آخر موعد للسداد: <b>{deadline.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>\n\n"
        f"⚠️ التأخر في السداد يعني السجن الفوري!",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /repay
# ---------------------------------------------------------------------------

async def cmd_repay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args

    async with get_session() as session:
        loan = await get_active_loan(session, user.id)
        if not loan:
            await update.message.reply_text("✅ ليس لديك أي قرض قائم!")
            return

        if not args:
            await update.message.reply_text(
                f"💳 <b>سداد القرض</b>\n\n"
                f"الاستخدام: /repay <المبلغ> أو /repay all\n\n"
                f"المبلغ المتبقي: <b>{fmt_coins(loan.remaining)} عملة</b>",
                parse_mode="HTML",
            )
            return

        wallet = await get_wallet(session, user.id)

        if args[0].lower() == "all":
            pay_amount = loan.remaining
        elif args[0].isdigit() and int(args[0]) > 0:
            pay_amount = int(args[0])
        else:
            await update.message.reply_text("❌ أدخل مبلغاً صحيحاً أو اكتب /repay all")
            return

        pay_amount = min(pay_amount, loan.remaining)

        result = await deduct_coins(session, user.id, pay_amount)
        if result is None:
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"رصيدك: <b>{fmt_coins(wallet.coins)} عملة</b>\n"
                f"المطلوب: <b>{fmt_coins(pay_amount)} عملة</b>",
                parse_mode="HTML",
            )
            return

        loan.amount_repaid += pay_amount
        if loan.remaining <= 0:
            loan.is_repaid = True
            paid_off = True
        else:
            paid_off = False

        wallet = await get_wallet(session, user.id)

    if paid_off:
        await update.message.reply_text(
            f"✅ <b>تم سداد القرض بالكامل!</b>\n\n"
            f"دفعت: <b>{fmt_coins(pay_amount)} عملة</b>\n"
            f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"💳 <b>تم الدفع الجزئي</b>\n\n"
            f"دفعت: <b>{fmt_coins(pay_amount)} عملة</b>\n"
            f"المتبقي: <b>{fmt_coins(loan.remaining)} عملة</b>\n"
            f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# /myloan
# ---------------------------------------------------------------------------

async def cmd_myloan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        loan = await get_active_loan(session, user.id)

    if not loan:
        await update.message.reply_text("✅ ليس لديك أي قرض قائم. أنت خالٍ من الديون!")
        return

    status = "⏳ قيد السداد"
    if loan.is_overdue:
        status = "🚨 <b>متأخر — ستُسجن في أول أمر!</b>"

    await update.message.reply_text(
        f"📋 <b>قرضي</b>\n\n"
        f"المبلغ الأصلي: <b>{fmt_coins(loan.principal)} عملة</b>\n"
        f"إجمالي الدين: <b>{fmt_coins(loan.total_due)} عملة</b>\n"
        f"المسدّد: <b>{fmt_coins(loan.amount_repaid)} عملة</b>\n"
        f"المتبقي: <b>{fmt_coins(loan.remaining)} عملة</b>\n"
        f"آخر موعد: <b>{loan.deadline.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
        f"الحالة: {status}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /debtors
# ---------------------------------------------------------------------------

async def cmd_debtors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        from economy.models import EconomyStats
        rows = (await session.execute(
            select(LoanRecord, EconomyStats.first_name, EconomyStats.username)
            .join(EconomyStats, EconomyStats.user_id == LoanRecord.user_id, isouter=True)
            .where(LoanRecord.is_repaid == False)
            .order_by(LoanRecord.deadline)
            .limit(15)
        )).all()

    if not rows:
        await update.message.reply_text("✅ لا يوجد مديونون حالياً!")
        return

    now = _utcnow()
    lines = []
    for loan, fname, uname in rows:
        name = fmt_user(fname or f"user_{loan.user_id}", uname)
        overdue = "🚨 متأخر" if loan.is_overdue else "⏳"
        lines.append(f"{overdue} {name} — <b>{fmt_coins(loan.remaining)}</b> عملة")

    await update.message.reply_text(
        f"💳 <b>قائمة المديونين</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /myjail
# ---------------------------------------------------------------------------

async def cmd_myjail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        jail = await get_jail(session, user.id)

    if not jail or not jail.is_active:
        await update.message.reply_text("✅ أنت حر — لست في السجن!")
        return

    await update.message.reply_text(
        f"🔒 <b>أنت في السجن</b>\n\n"
        f"السبب: {jail.reason}\n"
        f"الإفراج التلقائي بعد: <b>{jail.time_left_str}</b>\n\n"
        f"أو ادفع كفالة <b>{fmt_coins(jail.bail_amount)} عملة</b> بـ /bail",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /bail
# ---------------------------------------------------------------------------

async def cmd_bail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        jail = await get_jail(session, user.id)
        if not jail or not jail.is_active:
            await update.message.reply_text("✅ أنت لست في السجن!")
            return

        wallet = await deduct_coins(session, user.id, jail.bail_amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ لدفع الكفالة!\n"
                f"الكفالة: <b>{fmt_coins(jail.bail_amount)} عملة</b>\n"
                f"رصيدك: <b>{fmt_coins(current.coins)} عملة</b>\n\n"
                f"انتظر الإفراج التلقائي بعد: <b>{jail.time_left_str}</b>",
                parse_mode="HTML",
            )
            return

        await release_user(session, user.id)

    await update.message.reply_text(
        f"🔓 <b>خرجت من السجن!</b>\n\n"
        f"دفعت كفالة: <b>{fmt_coins(jail.bail_amount)} عملة</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /bailout
# ---------------------------------------------------------------------------

async def cmd_bailout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await update.message.reply_text(
            "👮 ردّ على رسالة الشخص الذي تريد إخراجه من السجن واكتب /bailout"
        )
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("😅 استخدم /bail لإخراج نفسك!")
        return

    async with get_session() as session:
        jail = await get_jail(session, target.id)
        if not jail or not jail.is_active:
            await update.message.reply_text(
                f"✅ {target.first_name} ليس في السجن!"
            )
            return

        wallet = await deduct_coins(session, user.id, jail.bail_amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"الكفالة: <b>{fmt_coins(jail.bail_amount)} عملة</b>\n"
                f"رصيدك: <b>{fmt_coins(current.coins)} عملة</b>",
                parse_mode="HTML",
            )
            return

        await release_user(session, target.id)

    target_name = fmt_user(target.first_name, target.username)
    await update.message.reply_text(
        f"🔓 <b>{user.first_name} أخرج {target_name} من السجن!</b>\n\n"
        f"دفع كفالة: <b>{fmt_coins(jail.bail_amount)} عملة</b>\n"
        f"💰 رصيده الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("loan",     cmd_loan))
    application.add_handler(CommandHandler("repay",    cmd_repay))
    application.add_handler(CommandHandler("myloan",   cmd_myloan))
    application.add_handler(CommandHandler("debtors",  cmd_debtors))
    application.add_handler(CommandHandler("myjail",   cmd_myjail))
    application.add_handler(CommandHandler("bail",     cmd_bail))
    application.add_handler(CommandHandler("bailout",  cmd_bailout))
