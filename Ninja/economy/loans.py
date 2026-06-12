"""
economy/loans.py — Debt and Jail system.

Commands:
  /loan <amount>  — Borrow (requires bank account, only one loan at a time)
  /repay <amount> — Partial or full repayment (or /repay all)
  /myloan         — Show current loan status
  /debtors        — List of overdue debtors
  /bail           — Pay bail to get out of jail
  /bailout        — Pay bail for someone else (reply to their message)
  /myjail         — Show jail status
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
            f"🏦 <b>Loan</b>\n\n"
            f"Usage: /loan <amount>\n"
            f"Example: /loan 1000\n\n"
            f"• Maximum: <b>{fmt_coins(LOAN_MAX)} coins</b>\n"
            f"• Interest: <b>10%</b>\n"
            f"• Repayment period: <b>24 hours</b>\n"
            f"• Overdue = Immediate jail 🔒",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    if amount > LOAN_MAX:
        await update.message.reply_text(
            f"❌ Maximum loan amount is <b>{fmt_coins(LOAN_MAX)} coins</b>.",
            parse_mode="HTML",
        )
        return

    async with get_session() as session:
        bank = await get_bank_account_by_user(session, user.id)
        if not bank:
            await update.message.reply_text(
                "❌ You must have a bank account to use the loan service.\n"
                "Open an account with /openbank"
            )
            return

        if await is_jailed(session, user.id):
            await check_jailed_and_reply(update, session, user.id)
            return

        existing = await get_active_loan(session, user.id)
        if existing:
            await update.message.reply_text(
                f"❌ You already have an active loan!\n\n"
                f"Remaining amount: <b>{fmt_coins(existing.remaining)} coins</b>\n"
                f"Repayment deadline: {existing.deadline.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
                f"Repay your loan first with /repay",
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
        f"💸 <b>Loan granted!</b>\n\n"
        f"You received: <b>{fmt_coins(amount)} coins</b>\n"
        f"Total debt (with 10% interest): <b>{fmt_coins(total_due)} coins</b>\n"
        f"Repayment deadline: <b>{deadline.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>\n\n"
        f"⚠️ Late repayment means immediate jail!",
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
            await update.message.reply_text("✅ You don't have any active loan!")
            return

        if not args:
            await update.message.reply_text(
                f"💳 <b>Repay Loan</b>\n\n"
                f"Usage: /repay <amount> or /repay all\n\n"
                f"Remaining amount: <b>{fmt_coins(loan.remaining)} coins</b>",
                parse_mode="HTML",
            )
            return

        wallet = await get_wallet(session, user.id)

        if args[0].lower() == "all":
            pay_amount = loan.remaining
        elif args[0].isdigit() and int(args[0]) > 0:
            pay_amount = int(args[0])
        else:
            await update.message.reply_text("❌ Enter a valid amount or type /repay all")
            return

        pay_amount = min(pay_amount, loan.remaining)

        result = await deduct_coins(session, user.id, pay_amount)
        if result is None:
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Balance: <b>{fmt_coins(wallet.coins)} coins</b>\n"
                f"Required: <b>{fmt_coins(pay_amount)} coins</b>",
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
            f"✅ <b>Loan fully repaid!</b>\n\n"
            f"You paid: <b>{fmt_coins(pay_amount)} coins</b>\n"
            f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"💳 <b>Partial payment made</b>\n\n"
            f"You paid: <b>{fmt_coins(pay_amount)} coins</b>\n"
            f"Remaining: <b>{fmt_coins(loan.remaining)} coins</b>\n"
            f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
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
        await update.message.reply_text("✅ You don't have any active loan. You are debt-free!")
        return

    status = "⏳ Repayment in progress"
    if loan.is_overdue:
        status = "🚨 <b>Overdue — you will be jailed on your first command!</b>"

    await update.message.reply_text(
        f"📋 <b>My Loan</b>\n\n"
        f"Principal: <b>{fmt_coins(loan.principal)} coins</b>\n"
        f"Total Debt: <b>{fmt_coins(loan.total_due)} coins</b>\n"
        f"Repaid: <b>{fmt_coins(loan.amount_repaid)} coins</b>\n"
        f"Remaining: <b>{fmt_coins(loan.remaining)} coins</b>\n"
        f"Deadline: <b>{loan.deadline.strftime('%Y-%m-%d %H:%M')} UTC</b>\n"
        f"Status: {status}",
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
        await update.message.reply_text("✅ No active debtors!")
        return

    now = _utcnow()
    lines = []
    for loan, fname, uname in rows:
        name = fmt_user(fname or f"user_{loan.user_id}", uname)
        overdue = "🚨 Overdue" if loan.is_overdue else "⏳"
        lines.append(f"{overdue} {name} — <b>{fmt_coins(loan.remaining)}</b> coins")

    await update.message.reply_text(
        f"💳 <b>Debtors List</b>\n\n" + "\n".join(lines),
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
        await update.message.reply_text("✅ You are free — not in jail!")
        return

    await update.message.reply_text(
        f"🔒 <b>You are in jail</b>\n\n"
        f"Reason: {jail.reason}\n"
        f"Automatic release in: <b>{jail.time_left_str}</b>\n\n"
        f"Or pay bail <b>{fmt_coins(jail.bail_amount)} coins</b> with /bail",
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
            await update.message.reply_text("✅ You are not in jail!")
            return

        wallet = await deduct_coins(session, user.id, jail.bail_amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance to pay bail!\n"
                f"Bail: <b>{fmt_coins(jail.bail_amount)} coins</b>\n"
                f"Balance: <b>{fmt_coins(current.coins)} coins</b>\n\n"
                f"Wait for automatic release in: <b>{jail.time_left_str}</b>",
                parse_mode="HTML",
            )
            return

        await release_user(session, user.id)

    await update.message.reply_text(
        f"🔓 <b>You've been released from jail!</b>\n\n"
        f"Paid bail: <b>{fmt_coins(jail.bail_amount)} coins</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
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
            "👮 Reply to the message of the person you want to bail out and type /bailout"
        )
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await update.message.reply_text("😅 Use /bail to bail yourself out!")
        return

    async with get_session() as session:
        jail = await get_jail(session, target.id)
        if not jail or not jail.is_active:
            await update.message.reply_text(
                f"✅ {target.first_name} is not in jail!"
            )
            return

        wallet = await deduct_coins(session, user.id, jail.bail_amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Bail: <b>{fmt_coins(jail.bail_amount)} coins</b>\n"
                f"Balance: <b>{fmt_coins(current.coins)} coins</b>",
                parse_mode="HTML",
            )
            return

        await release_user(session, target.id)

    target_name = fmt_user(target.first_name, target.username)
    await update.message.reply_text(
        f"🔓 <b>{user.first_name} bailed {target_name} out of jail!</b>\n\n"
        f"Paid bail: <b>{fmt_coins(jail.bail_amount)} coins</b>\n"
        f"💰 Your balance: <b>{fmt_coins(wallet.coins)} coins</b>",
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
