"""
economy/plugin.py — Full virtual economy system.

Commands:
  /richlist    — Top 20 wealthiest people
  /openbank    — Open bank account
  /closebank   — Delete bank account
  /transfer    — Transfer (two-step conversation)
  /mybank      — Show my bank account
  /balance     — My balance
  /checkbal    — Someone else's balance (reply or mention)
  /salary      — Salary (every 20 minutes)
  /bonus       — Bonus (every 10 minutes)
  /steal       — Steal (every 10 minutes, reply to someone)
  /thieftop    — Thief Top
  /invest      — Invest (0-9% profit)
  /luck        — Luck (50% double / 50% lose)
  /trade       — Trade (-90% to +90%)
  /top         — Wealth Top + Thief Top
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, desc
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.game_wallet import get_wallet, add_coins, deduct_coins
from database.engine import get_session
from database.game_models import Wallet
from economy.helpers import (
    check_jailed_and_reply,
    create_bank_account,
    fmt_coins,
    fmt_user,
    get_bank_account_by_number,
    get_bank_account_by_user,
    get_stats,
)
from economy.models import BankAccount, EconomyStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SALARY_AMOUNT   = 500
SALARY_COOLDOWN = timedelta(minutes=20)

BONUS_AMOUNT    = 200
BONUS_COOLDOWN  = timedelta(minutes=10)

STEAL_COOLDOWN  = timedelta(minutes=10)
STEAL_MIN_PCT   = 5
STEAL_MAX_PCT   = 20

INVEST_MIN_PCT  = 0
INVEST_MAX_PCT  = 9

TRANSFER_WAIT_ACCOUNT = 1   # ConversationHandler state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _remaining(last_at: Optional[datetime], cooldown: timedelta) -> Optional[str]:
    """Return remaining time string if cooldown hasn't expired, else None."""
    if last_at is None:
        return None
    diff = cooldown - (_utcnow() - last_at)
    if diff.total_seconds() <= 0:
        return None
    total = int(diff.total_seconds())
    mins, secs = divmod(total, 60)
    return f"{mins}m {secs}s"


async def _resolve_target(update: Update) -> Optional[int]:
    """Extract target user_id from reply or mention in text."""
    msg: Message = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user.id
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention" and msg.text:
                # @username mention — cannot be resolved to user_id without DB search
                return None
            if entity.type == "text_mention" and entity.user:
                return entity.user.id
    return None


# ---------------------------------------------------------------------------
# /richlist  — Wealth Top
# ---------------------------------------------------------------------------

async def cmd_richlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        rows = (await session.execute(
            select(EconomyStats.first_name, EconomyStats.username, Wallet.coins)
            .join(Wallet, Wallet.user_id == EconomyStats.user_id)
            .order_by(desc(Wallet.coins))
            .limit(20)
        )).all()

    if not rows:
        await update.message.reply_text("🏦 No one is on the list yet!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (fname, uname, coins) in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = fmt_user(fname, uname)
        lines.append(f"{medal} {name} — <b>{fmt_coins(coins)}</b> 💰")

    text = "✯ <b>Wealth Top</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /openbank  — Create Bank Account
# ---------------------------------------------------------------------------

async def cmd_openbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        existing = await get_bank_account_by_user(session, user.id)
        if existing:
            await update.message.reply_text(
                f"🏦 You already have a bank account!\n\n"
                f"Account Number: <code>{existing.account_number}</code>",
                parse_mode="HTML",
            )
            return
        account = await create_bank_account(session, user.id, user.first_name, user.username)

    await update.message.reply_text(
        f"✅ <b>Bank account opened!</b>\n\n"
        f"👤 Name: {user.first_name}\n"
        f"🔢 Account Number: <code>{account.account_number}</code>\n\n"
        f"Save your account number and share it with those who want to transfer money to you 💳",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /closebank  — Delete Bank Account
# ---------------------------------------------------------------------------

async def cmd_closebank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        account = await get_bank_account_by_user(session, user.id)
        if not account:
            await update.message.reply_text("❌ You don't have a bank account to delete.")
            return
        await session.delete(account)

    await update.message.reply_text(
        "🗑️ Your bank account has been deleted.\n"
        "You can open a new account anytime with /openbank"
    )


# ---------------------------------------------------------------------------
# /mybank  — My Account
# ---------------------------------------------------------------------------

async def cmd_mybank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        account = await get_bank_account_by_user(session, user.id)
        if not account:
            await update.message.reply_text(
                "❌ You don't have a bank account.\n"
                "Open one with /openbank"
            )
            return
        wallet = await get_wallet(session, user.id)

    await update.message.reply_text(
        f"🏦 <b>My Bank Account</b>\n\n"
        f"👤 Name: {account.owner_first_name}\n"
        f"🔢 Account Number: <code>{account.account_number}</code>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>\n"
        f"📅 Date Opened: {account.created_at.strftime('%Y-%m-%d')}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /transfer  — Transfer (ConversationHandler)
# ---------------------------------------------------------------------------

async def cmd_transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📤 <b>Transfer</b>\n\n"
            "Usage: /transfer <amount>\n"
            "Example: /transfer 500",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    amount = int(args[0])
    context.user_data["transfer_amount"] = amount
    await update.message.reply_text(
        f"💸 Preparing to transfer <b>{fmt_coins(amount)} coins</b>\n\n"
        f"Now send the recipient's <b>account number</b>:",
        parse_mode="HTML",
    )
    return TRANSFER_WAIT_ACCOUNT


async def cmd_transfer_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    account_number = (update.message.text or "").strip()
    amount = context.user_data.get("transfer_amount", 0)

    if not account_number.isdigit() or len(account_number) != 10:
        await update.message.reply_text(
            "❌ Account number must be 10 digits. Try again or /cancel to abort."
        )
        return TRANSFER_WAIT_ACCOUNT

    async with get_session() as session:
        sender_account = await get_bank_account_by_user(session, user.id)
        if not sender_account:
            await update.message.reply_text(
                "❌ You must have a bank account to transfer.\n"
                "Open one with /openbank"
            )
            return ConversationHandler.END

        receiver_account = await get_bank_account_by_number(session, account_number)
        if not receiver_account:
            await update.message.reply_text("❌ Account number not found. Check the number and try again.")
            return TRANSFER_WAIT_ACCOUNT

        if receiver_account.user_id == user.id:
            await update.message.reply_text("❌ You cannot transfer to yourself!")
            return ConversationHandler.END

        sender_wallet = await deduct_coins(session, user.id, amount)
        if sender_wallet is None:
            sender_wallet = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Current balance: <b>{fmt_coins(sender_wallet.coins)} coins</b>",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        receiver_wallet = await add_coins(session, receiver_account.user_id, amount)
        receiver_name = fmt_user(receiver_account.owner_first_name, receiver_account.owner_username)

    await update.message.reply_text(
        f"✅ <b>Transfer successful!</b>\n\n"
        f"📤 Transferred: <b>{fmt_coins(amount)} coins</b>\n"
        f"👤 To: {receiver_name}\n"
        f"💳 Account Number: <code>{account_number}</code>\n"
        f"💰 Your balance: <b>{fmt_coins(sender_wallet.coins)} coins</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cmd_transfer_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("transfer_amount", None)
    await update.message.reply_text("❌ Transfer cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /balance  — My Balance
# ---------------------------------------------------------------------------

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        wallet = await get_wallet(session, user.id)

    await update.message.reply_text(
        f"💰 <b>Your balance, {user.first_name}</b>\n\n"
        f"<b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /checkbal  — Check Balance (reply or mention)
# ---------------------------------------------------------------------------

async def cmd_checkbal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_id = await _resolve_target(update)
    if not target_id:
        await update.message.reply_text(
            "ℹ️ Use this command by replying to someone's message to see their balance.\n"
            "Example: Reply to their message and type /checkbal"
        )
        return

    target_user = update.effective_message.reply_to_message.from_user
    async with get_session() as session:
        wallet = await get_wallet(session, target_id)

    name = fmt_user(target_user.first_name, target_user.username)
    await update.message.reply_text(
        f"💰 <b>Balance of {name}</b>\n\n"
        f"<b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /salary  — Salary (every 20 min)
# ---------------------------------------------------------------------------

async def cmd_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        if await check_jailed_and_reply(update, session, user.id):
            return
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_salary_at, SALARY_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ Next salary in <b>{wait}</b>",
                parse_mode="HTML",
            )
            return
        wallet = await add_coins(session, user.id, SALARY_AMOUNT)
        stats.last_salary_at = _utcnow()

    await update.message.reply_text(
        f"💵 <b>Salary received!</b>\n\n"
        f"<b>+{fmt_coins(SALARY_AMOUNT)} coins</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /bonus  — Bonus (every 10 min)
# ---------------------------------------------------------------------------

async def cmd_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        if await check_jailed_and_reply(update, session, user.id):
            return
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_bonus_at, BONUS_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ Next bonus in <b>{wait}</b>",
                parse_mode="HTML",
            )
            return
        wallet = await add_coins(session, user.id, BONUS_AMOUNT)
        stats.last_bonus_at = _utcnow()

    await update.message.reply_text(
        f"🎁 <b>Your bonus!</b>\n\n"
        f"<b>+{fmt_coins(BONUS_AMOUNT)} coins</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /steal  — Steal (every 10 min, reply to person)
# ---------------------------------------------------------------------------

async def cmd_steal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target_id = await _resolve_target(update)

    if not target_id:
        await update.message.reply_text(
            "🦹 Reply to someone's message and type /steal to rob them!"
        )
        return

    async with get_session() as _chk:
        if await check_jailed_and_reply(update, _chk, user.id):
            return

    if target_id == user.id:
        await update.message.reply_text("😂 You cannot steal from yourself!")
        return

    target_user = update.effective_message.reply_to_message.from_user

    async with get_session() as session:
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_steal_at, STEAL_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ You cannot steal now, wait <b>{wait}</b>",
                parse_mode="HTML",
            )
            return

        victim_wallet = await get_wallet(session, target_id)
        if victim_wallet.coins < 10:
            await update.message.reply_text(
                f"😅 {target_user.first_name} has nothing worth stealing!"
            )
            stats.last_steal_at = _utcnow()
            return

        steal_pct = random.randint(STEAL_MIN_PCT, STEAL_MAX_PCT)
        steal_amount = max(1, int(victim_wallet.coins * steal_pct / 100))

        # 70% success chance
        success = random.random() < 0.70

        if success:
            victim_result = await deduct_coins(session, target_id, steal_amount)
            if victim_result is None:
                steal_amount = victim_wallet.coins
                await deduct_coins(session, target_id, steal_amount)
            thief_wallet = await add_coins(session, user.id, steal_amount)
            stats.total_stolen += steal_amount
            stats.steal_count  += 1
            stats.last_steal_at = _utcnow()
            victim_name = fmt_user(target_user.first_name, target_user.username)
            await update.message.reply_text(
                f"🦹 <b>Successful robbery!</b>\n\n"
                f"You stole <b>{fmt_coins(steal_amount)} coins</b> from {victim_name}\n"
                f"💰 Balance: <b>{fmt_coins(thief_wallet.coins)} coins</b>",
                parse_mode="HTML",
            )
        else:
            # Failure — pay a fine to the victim
            fine = max(1, steal_amount // 2)
            thief_wallet = await deduct_coins(session, user.id, fine)
            stats.last_steal_at = _utcnow()
            if thief_wallet is None:
                thief_wallet = await get_wallet(session, user.id)
            victim_name = fmt_user(target_user.first_name, target_user.username)
            await update.message.reply_text(
                f"🚔 <b>Caught!</b>\n\n"
                f"You tried to rob {victim_name} but failed!\n"
                f"Paid a fine of <b>{fmt_coins(fine)} coins</b>\n"
                f"💰 Balance: <b>{fmt_coins(thief_wallet.coins)} coins</b>",
                parse_mode="HTML",
            )


# ---------------------------------------------------------------------------
# /thieftop  — Thief Top
# ---------------------------------------------------------------------------

async def cmd_thieftop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        rows = (await session.execute(
            select(EconomyStats.first_name, EconomyStats.username,
                   EconomyStats.total_stolen, EconomyStats.steal_count)
            .where(EconomyStats.steal_count > 0)
            .order_by(desc(EconomyStats.total_stolen))
            .limit(10)
        )).all()

    if not rows:
        await update.message.reply_text("🦹 No thieves yet!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (fname, uname, stolen, count) in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = fmt_user(fname, uname)
        lines.append(
            f"{medal} {name}\n"
            f"   Stole: <b>{fmt_coins(stolen)}</b> 💰 | Jobs: {count}"
        )

    text = "🦹 <b>Thief Top</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /invest  — Invest (guaranteed profit 0-9%)
# ---------------------------------------------------------------------------

async def cmd_invest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📈 <b>Invest</b>\n\n"
            "Usage: /invest <amount>\n"
            "Example: /invest 1000\n\n"
            "Guaranteed profit rate: 0% - 9%",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        if await check_jailed_and_reply(update, session, user.id):
            return
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Balance: <b>{fmt_coins(current.coins)} coins</b>",
                parse_mode="HTML",
            )
            return
        profit_pct = random.randint(INVEST_MIN_PCT, INVEST_MAX_PCT)
        profit = int(amount * profit_pct / 100)
        total_return = amount + profit
        wallet = await add_coins(session, user.id, total_return)

    await update.message.reply_text(
        f"📈 <b>Investment Result</b>\n\n"
        f"💵 Invested: <b>{fmt_coins(amount)} coins</b>\n"
        f"📊 Profit rate: <b>{profit_pct}%</b>\n"
        f"✅ Earned: <b>{fmt_coins(profit)} coins</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /luck  — Luck (50% double / 50% lose all)
# ---------------------------------------------------------------------------

async def cmd_luck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "🎲 <b>Luck</b>\n\n"
            "Usage: /luck <amount>\n"
            "Example: /luck 500\n\n"
            "50% double your amount — 50% lose it",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        if await check_jailed_and_reply(update, session, user.id):
            return
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Balance: <b>{fmt_coins(current.coins)} coins</b>",
                parse_mode="HTML",
            )
            return

        won = random.random() < 0.5
        if won:
            wallet = await add_coins(session, user.id, amount * 2)
            result_text = (
                f"🎉 <b>You won!</b>\n\n"
                f"You doubled <b>{fmt_coins(amount)} coins</b>\n"
                f"Profit: <b>+{fmt_coins(amount)} coins</b>\n"
                f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>"
            )
        else:
            result_text = (
                f"💔 <b>You lost!</b>\n\n"
                f"You lost <b>{fmt_coins(amount)} coins</b>\n"
                f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>"
            )

    await update.message.reply_text(result_text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /trade  — Trade (-90% to +90%)
# ---------------------------------------------------------------------------

async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📉📈 <b>Trade</b>\n\n"
            "Usage: /trade <amount>\n"
            "Example: /trade 1000\n\n"
            "Result is random from -90% to +90%",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        if await check_jailed_and_reply(update, session, user.id):
            return
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Balance: <b>{fmt_coins(current.coins)} coins</b>",
                parse_mode="HTML",
            )
            return

        pct = random.randint(-90, 90)
        change = int(amount * pct / 100)
        actual_return = amount + change

        if actual_return > 0:
            wallet = await add_coins(session, user.id, actual_return)
        # If result is zero or negative — lose everything (add nothing)

    if pct > 0:
        direction = f"📈 You won +{pct}%"
        change_text = f"+{fmt_coins(change)} coins"
    elif pct < 0:
        direction = f"📉 You lost {pct}%"
        change_text = f"-{fmt_coins(abs(change))} coins"
    else:
        direction = "😐 No profit, no loss"
        change_text = "0 coins"

    await update.message.reply_text(
        f"📊 <b>Trade Result</b>\n\n"
        f"💵 Amount Traded: <b>{fmt_coins(amount)} coins</b>\n"
        f"{direction}\n"
        f"Change: <b>{change_text}</b>\n"
        f"💰 Balance: <b>{fmt_coins(wallet.coins)} coins</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /top  — Wealth Top + Thief Top
# ---------------------------------------------------------------------------

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        rich_rows = (await session.execute(
            select(EconomyStats.first_name, EconomyStats.username, Wallet.coins)
            .join(Wallet, Wallet.user_id == EconomyStats.user_id)
            .order_by(desc(Wallet.coins))
            .limit(5)
        )).all()

        thief_rows = (await session.execute(
            select(EconomyStats.first_name, EconomyStats.username,
                   EconomyStats.total_stolen)
            .where(EconomyStats.steal_count > 0)
            .order_by(desc(EconomyStats.total_stolen))
            .limit(5)
        )).all()

    medals = ["🥇", "🥈", "🥉", "4.", "5."]

    rich_lines = []
    for i, (fname, uname, coins) in enumerate(rich_rows):
        name = fmt_user(fname, uname)
        rich_lines.append(f"{medals[i]} {name} — <b>{fmt_coins(coins)}</b> 💰")

    thief_lines = []
    for i, (fname, uname, stolen) in enumerate(thief_rows):
        name = fmt_user(fname, uname)
        thief_lines.append(f"{medals[i]} {name} — <b>{fmt_coins(stolen)}</b> 💰")

    rich_section  = "\n".join(rich_lines)  if rich_lines  else "No data yet"
    thief_section = "\n".join(thief_lines) if thief_lines else "No thieves yet"

    await update.message.reply_text(
        f"✯ <b>Wealth Top</b>\n{rich_section}\n\n"
        f"🦹 <b>Thief Top</b>\n{thief_section}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    transfer_conv = ConversationHandler(
        entry_points=[CommandHandler("transfer", cmd_transfer_start)],
        states={
            TRANSFER_WAIT_ACCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_transfer_account),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_transfer_cancel)],
        per_message=False,
    )

    application.add_handler(CommandHandler("richlist",  cmd_richlist))
    application.add_handler(CommandHandler("openbank",  cmd_openbank))
    application.add_handler(CommandHandler("closebank", cmd_closebank))
    application.add_handler(CommandHandler("mybank",    cmd_mybank))
    application.add_handler(transfer_conv)
    application.add_handler(CommandHandler("balance",   cmd_balance))
    application.add_handler(CommandHandler("checkbal",  cmd_checkbal))
    application.add_handler(CommandHandler("salary",    cmd_salary))
    application.add_handler(CommandHandler("bonus",     cmd_bonus))
    application.add_handler(CommandHandler("steal",     cmd_steal))
    application.add_handler(CommandHandler("thieftop",  cmd_thieftop))
    application.add_handler(CommandHandler("invest",    cmd_invest))
    application.add_handler(CommandHandler("luck",      cmd_luck))
    application.add_handler(CommandHandler("trade",     cmd_trade))
    application.add_handler(CommandHandler("top",       cmd_top))

    from economy.loans import register as register_loans
    from economy.heist import register as register_heist
    await register_loans(application)
    await register_heist(application)

    logger.info(
        "economy plugin registered — 24 commands: "
        "richlist, openbank, closebank, mybank, transfer, balance, checkbal, "
        "salary, bonus, steal, thieftop, invest, luck, trade, top, "
        "loan, repay, myloan, debtors, myjail, bail, bailout, rob, joinrob"
    )
