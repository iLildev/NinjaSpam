"""
economy/plugin.py — نظام الاقتصاد الوهمي الكامل.

الأوامر:
  /richlist    — توب أغنى 20 شخص
  /openbank    — فتح حساب بنكي
  /closebank   — مسح الحساب البنكي
  /transfer    — تحويل (محادثة من خطوتين)
  /mybank      — عرض حسابي البنكي
  /balance     — رصيدي
  /checkbal    — رصيد شخص آخر (رد أو منشن)
  /salary      — راتب (كل 20 دقيقة)
  /bonus       — بخشيش (كل 10 دقائق)
  /steal       — سرقة (كل 10 دقائق، رد على شخص)
  /thieftop    — توب الحراميه
  /invest      — استثمار (ربح 0-9%)
  /luck        — حظ (50% تضاعف / 50% تخسر)
  /trade       — مضاربة (-90% إلى +90%)
  /top         — توب الفلوس + توب الحراميه
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
    """أرجع نصّ الوقت المتبقّي إن كان الـ cooldown لم ينتهِ بعد، وإلا None."""
    if last_at is None:
        return None
    diff = cooldown - (_utcnow() - last_at)
    if diff.total_seconds() <= 0:
        return None
    total = int(diff.total_seconds())
    mins, secs = divmod(total, 60)
    return f"{mins}د {secs}ث"


async def _resolve_target(update: Update) -> Optional[int]:
    """استخرج user_id الهدف من الرد أو المنشن في النص."""
    msg: Message = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user.id
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention" and msg.text:
                # @username mention — لا يمكن تحويله لـ user_id بدون البحث في DB
                return None
            if entity.type == "text_mention" and entity.user:
                return entity.user.id
    return None


# ---------------------------------------------------------------------------
# /richlist  — توب الفلوس
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
        await update.message.reply_text("🏦 لا يوجد أحد في القائمة بعد!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (fname, uname, coins) in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = fmt_user(fname, uname)
        lines.append(f"{medal} {name} — <b>{fmt_coins(coins)}</b> 💰")

    text = "✯ <b>توب الفلوس</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /openbank  — إنشاء حساب بنكي
# ---------------------------------------------------------------------------

async def cmd_openbank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        existing = await get_bank_account_by_user(session, user.id)
        if existing:
            await update.message.reply_text(
                f"🏦 لديك حساب بنكي بالفعل!\n\n"
                f"رقم حسابك: <code>{existing.account_number}</code>",
                parse_mode="HTML",
            )
            return
        account = await create_bank_account(session, user.id, user.first_name, user.username)

    await update.message.reply_text(
        f"✅ <b>تم فتح حسابك البنكي!</b>\n\n"
        f"👤 الاسم: {user.first_name}\n"
        f"🔢 رقم الحساب: <code>{account.account_number}</code>\n\n"
        f"احفظ رقم حسابك وشاركه مع من يريد يحوّل لك فلوس 💳",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /closebank  — مسح الحساب البنكي
# ---------------------------------------------------------------------------

async def cmd_closebank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        account = await get_bank_account_by_user(session, user.id)
        if not account:
            await update.message.reply_text("❌ ليس لديك حساب بنكي لمسحه.")
            return
        await session.delete(account)

    await update.message.reply_text(
        "🗑️ تم مسح حسابك البنكي.\n"
        "يمكنك فتح حساب جديد في أي وقت بـ /openbank"
    )


# ---------------------------------------------------------------------------
# /mybank  — حسابي
# ---------------------------------------------------------------------------

async def cmd_mybank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        account = await get_bank_account_by_user(session, user.id)
        if not account:
            await update.message.reply_text(
                "❌ ليس لديك حساب بنكي.\n"
                "افتح حساباً بـ /openbank"
            )
            return
        wallet = await get_wallet(session, user.id)

    await update.message.reply_text(
        f"🏦 <b>حسابي البنكي</b>\n\n"
        f"👤 الاسم: {account.owner_first_name}\n"
        f"🔢 رقم الحساب: <code>{account.account_number}</code>\n"
        f"💰 الرصيد: <b>{fmt_coins(wallet.coins)} عملة</b>\n"
        f"📅 تاريخ الفتح: {account.created_at.strftime('%Y-%m-%d')}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /transfer  — تحويل (ConversationHandler)
# ---------------------------------------------------------------------------

async def cmd_transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📤 <b>تحويل</b>\n\n"
            "الاستخدام: /transfer <المبلغ>\n"
            "مثال: /transfer 500",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    amount = int(args[0])
    context.user_data["transfer_amount"] = amount
    await update.message.reply_text(
        f"💸 سيتم تحويل <b>{fmt_coins(amount)} عملة</b>\n\n"
        f"أرسل الآن <b>رقم حساب</b> المستلم:",
        parse_mode="HTML",
    )
    return TRANSFER_WAIT_ACCOUNT


async def cmd_transfer_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    account_number = (update.message.text or "").strip()
    amount = context.user_data.get("transfer_amount", 0)

    if not account_number.isdigit() or len(account_number) != 10:
        await update.message.reply_text(
            "❌ رقم الحساب يجب أن يكون 10 أرقام. حاول مرة ثانية أو /cancel للإلغاء."
        )
        return TRANSFER_WAIT_ACCOUNT

    async with get_session() as session:
        sender_account = await get_bank_account_by_user(session, user.id)
        if not sender_account:
            await update.message.reply_text(
                "❌ يجب أن يكون لديك حساب بنكي للتحويل.\n"
                "افتح حساباً بـ /openbank"
            )
            return ConversationHandler.END

        receiver_account = await get_bank_account_by_number(session, account_number)
        if not receiver_account:
            await update.message.reply_text("❌ رقم الحساب غير موجود. تحقق من الرقم وأعد المحاولة.")
            return TRANSFER_WAIT_ACCOUNT

        if receiver_account.user_id == user.id:
            await update.message.reply_text("❌ لا تستطيع التحويل لنفسك!")
            return ConversationHandler.END

        sender_wallet = await deduct_coins(session, user.id, amount)
        if sender_wallet is None:
            sender_wallet = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"رصيدك الحالي: <b>{fmt_coins(sender_wallet.coins)} عملة</b>",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        receiver_wallet = await add_coins(session, receiver_account.user_id, amount)
        receiver_name = fmt_user(receiver_account.owner_first_name, receiver_account.owner_username)

    await update.message.reply_text(
        f"✅ <b>تم التحويل بنجاح!</b>\n\n"
        f"📤 حوّلت: <b>{fmt_coins(amount)} عملة</b>\n"
        f"👤 إلى: {receiver_name}\n"
        f"💳 رقم الحساب: <code>{account_number}</code>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(sender_wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cmd_transfer_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("transfer_amount", None)
    await update.message.reply_text("❌ تم إلغاء التحويل.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /balance  — فلوسي
# ---------------------------------------------------------------------------

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        wallet = await get_wallet(session, user.id)

    await update.message.reply_text(
        f"💰 <b>رصيدك يا {user.first_name}</b>\n\n"
        f"<b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /checkbal  — فلوس (رد أو منشن)
# ---------------------------------------------------------------------------

async def cmd_checkbal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_id = await _resolve_target(update)
    if not target_id:
        await update.message.reply_text(
            "ℹ️ استخدم الأمر بالرد على رسالة شخص لمعرفة رصيده.\n"
            "مثال: ردّ على رسالته واكتب /checkbal"
        )
        return

    target_user = update.effective_message.reply_to_message.from_user
    async with get_session() as session:
        wallet = await get_wallet(session, target_id)

    name = fmt_user(target_user.first_name, target_user.username)
    await update.message.reply_text(
        f"💰 <b>رصيد {name}</b>\n\n"
        f"<b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /salary  — راتب (كل 20 دقيقة)
# ---------------------------------------------------------------------------

async def cmd_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_salary_at, SALARY_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ راتبك القادم بعد <b>{wait}</b>",
                parse_mode="HTML",
            )
            return
        wallet = await add_coins(session, user.id, SALARY_AMOUNT)
        stats.last_salary_at = _utcnow()

    await update.message.reply_text(
        f"💵 <b>استلمت راتبك!</b>\n\n"
        f"<b>+{fmt_coins(SALARY_AMOUNT)} عملة</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /bonus  — بخشيش (كل 10 دقائق)
# ---------------------------------------------------------------------------

async def cmd_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_bonus_at, BONUS_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ البخشيش القادم بعد <b>{wait}</b>",
                parse_mode="HTML",
            )
            return
        wallet = await add_coins(session, user.id, BONUS_AMOUNT)
        stats.last_bonus_at = _utcnow()

    await update.message.reply_text(
        f"🎁 <b>بخشيشك!</b>\n\n"
        f"<b>+{fmt_coins(BONUS_AMOUNT)} عملة</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /steal  — سرقة (كل 10 دقائق، رد على شخص)
# ---------------------------------------------------------------------------

async def cmd_steal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target_id = await _resolve_target(update)

    if not target_id:
        await update.message.reply_text(
            "🦹 ردّ على رسالة شخص واكتب /steal لسرقة فلوسه!"
        )
        return

    if target_id == user.id:
        await update.message.reply_text("😂 ما تقدر تسرق من نفسك!")
        return

    target_user = update.effective_message.reply_to_message.from_user

    async with get_session() as session:
        stats = await get_stats(session, user.id, user.first_name, user.username)
        wait = _remaining(stats.last_steal_at, STEAL_COOLDOWN)
        if wait:
            await update.message.reply_text(
                f"⏳ ما تقدر تسرق الآن، انتظر <b>{wait}</b>",
                parse_mode="HTML",
            )
            return

        victim_wallet = await get_wallet(session, target_id)
        if victim_wallet.coins < 10:
            await update.message.reply_text(
                f"😅 {target_user.first_name} ما عنده شي يستاهل تسرقه!"
            )
            stats.last_steal_at = _utcnow()
            return

        steal_pct = random.randint(STEAL_MIN_PCT, STEAL_MAX_PCT)
        steal_amount = max(1, int(victim_wallet.coins * steal_pct / 100))

        # 70% فرصة نجاح
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
                f"🦹 <b>سرقة ناجحة!</b>\n\n"
                f"سرقت <b>{fmt_coins(steal_amount)} عملة</b> من {victim_name}\n"
                f"💰 رصيدك الآن: <b>{fmt_coins(thief_wallet.coins)} عملة</b>",
                parse_mode="HTML",
            )
        else:
            # فشل — يدفع المسروق منه نسبة كغرامة من السارق
            fine = max(1, steal_amount // 2)
            thief_wallet = await deduct_coins(session, user.id, fine)
            stats.last_steal_at = _utcnow()
            if thief_wallet is None:
                thief_wallet = await get_wallet(session, user.id)
            victim_name = fmt_user(target_user.first_name, target_user.username)
            await update.message.reply_text(
                f"🚔 <b>انكشفت!</b>\n\n"
                f"حاولت تسرق {victim_name} بس ما نجحت!\n"
                f"دفعت غرامة <b>{fmt_coins(fine)} عملة</b>\n"
                f"💰 رصيدك الآن: <b>{fmt_coins(thief_wallet.coins)} عملة</b>",
                parse_mode="HTML",
            )


# ---------------------------------------------------------------------------
# /thieftop  — توب الحراميه
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
        await update.message.reply_text("🦹 لا يوجد لصوص بعد!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (fname, uname, stolen, count) in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = fmt_user(fname, uname)
        lines.append(
            f"{medal} {name}\n"
            f"   سرق: <b>{fmt_coins(stolen)}</b> 💰 | عمليات: {count}"
        )

    text = "🦹 <b>توب الحراميه</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /invest  — استثمار (ربح مضمون 0-9%)
# ---------------------------------------------------------------------------

async def cmd_invest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📈 <b>استثمار</b>\n\n"
            "الاستخدام: /invest <المبلغ>\n"
            "مثال: /invest 1000\n\n"
            "نسبة الربح المضمونة: 0% - 9%",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"رصيدك: <b>{fmt_coins(current.coins)} عملة</b>",
                parse_mode="HTML",
            )
            return
        profit_pct = random.randint(INVEST_MIN_PCT, INVEST_MAX_PCT)
        profit = int(amount * profit_pct / 100)
        total_return = amount + profit
        wallet = await add_coins(session, user.id, total_return)

    await update.message.reply_text(
        f"📈 <b>نتيجة الاستثمار</b>\n\n"
        f"💵 استثمرت: <b>{fmt_coins(amount)} عملة</b>\n"
        f"📊 نسبة الربح: <b>{profit_pct}%</b>\n"
        f"✅ ربحت: <b>{fmt_coins(profit)} عملة</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /luck  — حظ (50% تضاعف / 50% تخسر الكل)
# ---------------------------------------------------------------------------

async def cmd_luck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "🎲 <b>حظ</b>\n\n"
            "الاستخدام: /luck <المبلغ>\n"
            "مثال: /luck 500\n\n"
            "50% تضاعف مبلغك — 50% تخسره",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"رصيدك: <b>{fmt_coins(current.coins)} عملة</b>",
                parse_mode="HTML",
            )
            return

        won = random.random() < 0.5
        if won:
            wallet = await add_coins(session, user.id, amount * 2)
            result_text = (
                f"🎉 <b>فزت!</b>\n\n"
                f"ضاعفت <b>{fmt_coins(amount)} عملة</b>\n"
                f"ربحت: <b>+{fmt_coins(amount)} عملة</b>\n"
                f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>"
            )
        else:
            result_text = (
                f"💔 <b>خسرت!</b>\n\n"
                f"خسرت <b>{fmt_coins(amount)} عملة</b>\n"
                f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>"
            )

    await update.message.reply_text(result_text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /trade  — مضاربة (-90% إلى +90%)
# ---------------------------------------------------------------------------

async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text(
            "📉📈 <b>مضاربة</b>\n\n"
            "الاستخدام: /trade <المبلغ>\n"
            "مثال: /trade 1000\n\n"
            "النتيجة عشوائية من -90% إلى +90%",
            parse_mode="HTML",
        )
        return

    amount = int(args[0])
    async with get_session() as session:
        wallet = await deduct_coins(session, user.id, amount)
        if wallet is None:
            current = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"رصيدك: <b>{fmt_coins(current.coins)} عملة</b>",
                parse_mode="HTML",
            )
            return

        pct = random.randint(-90, 90)
        change = int(amount * pct / 100)
        actual_return = amount + change

        if actual_return > 0:
            wallet = await add_coins(session, user.id, actual_return)
        # إذا كانت النتيجة صفراً أو سالبة — خسر كل شيء (لا نضيف شيئاً)

    if pct > 0:
        direction = f"📈 ربحت +{pct}%"
        change_text = f"+{fmt_coins(change)} عملة"
    elif pct < 0:
        direction = f"📉 خسرت {pct}%"
        change_text = f"-{fmt_coins(abs(change))} عملة"
    else:
        direction = "😐 لا ربح ولا خسارة"
        change_text = "0 عملة"

    await update.message.reply_text(
        f"📊 <b>نتيجة المضاربة</b>\n\n"
        f"💵 ضاربت بـ: <b>{fmt_coins(amount)} عملة</b>\n"
        f"{direction}\n"
        f"التغيير: <b>{change_text}</b>\n"
        f"💰 رصيدك الآن: <b>{fmt_coins(wallet.coins)} عملة</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /top  — توب الفلوس + توب الحراميه
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

    rich_section  = "\n".join(rich_lines)  if rich_lines  else "لا يوجد بيانات بعد"
    thief_section = "\n".join(thief_lines) if thief_lines else "لا يوجد لصوص بعد"

    await update.message.reply_text(
        f"✯ <b>توب الفلوس</b>\n{rich_section}\n\n"
        f"🦹 <b>توب الحراميه</b>\n{thief_section}",
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

    logger.info(
        "economy plugin registered — 15 commands: "
        "richlist, openbank, closebank, mybank, transfer, balance, checkbal, "
        "salary, bonus, steal, thieftop, invest, luck, trade, top"
    )
