"""
plugins/account.py — نظام الحسابات الوهمية 🎮 (للمتعة فقط — لا علاقة له بالمال الحقيقي).

اللاعب ينشئ هوية وهمية داخل اللعبة لمحافظ خيالية:
  الكريمي الوهمي 💳 | الراجحي الوهمي 🏦 | PayPal الوهمي 🌐

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الأوامر:
  /start          — بدء التسجيل (في المحادثة الخاصة)
  /my_account     — عرض هويتك الوهمية في اللعبة
  /add_payment    — إضافة/تحديث محفظة وهمية
  /remove_payment — حذف محفظة وهمية
  /set_primary    — تحديد المحفظة الوهمية الافتراضية
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
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

from database.engine import get_session
from database.payment_models import PaymentAccount, PaymentMethod, UserProfile

logger = logging.getLogger(__name__)

_utcnow = lambda: datetime.now(tz=timezone.utc)

# ---------------------------------------------------------------------------
# حالات المحادثة
# ---------------------------------------------------------------------------
CHOOSE_METHOD   = 0   # المستخدم يختار طريقة الدفع
ENTER_ACCOUNT   = 1   # المستخدم يكتب رقم الحساب
CONFIRM_REMOVE  = 2   # المستخدم يؤكد الحذف

# مفتاح مؤقت في user_data لحفظ الطريقة المختارة
_KEY_METHOD = "_acct_method"


# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def _method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 الكريمي",  callback_data="pm_alkarimi")],
        [InlineKeyboardButton("🏦 الراجحي",  callback_data="pm_alrajhi")],
        [InlineKeyboardButton("🌐 PayPal",   callback_data="pm_paypal")],
        [InlineKeyboardButton("❌ إلغاء",     callback_data="pm_cancel")],
    ])


def _remove_keyboard(accounts: list[PaymentAccount]) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        label = f"🗑 {acc.method.arabic_name} — {acc.account_identifier}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"rm_{acc.method.value}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="pm_cancel")])
    return InlineKeyboardMarkup(buttons)


def _primary_keyboard(accounts: list[PaymentAccount]) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        primary_mark = " ✅" if acc.is_primary else ""
        label = f"{acc.method.arabic_name}{primary_mark}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"primary_{acc.method.value}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="pm_cancel")])
    return InlineKeyboardMarkup(buttons)


async def _get_profile(session, user_id: int) -> Optional[UserProfile]:
    r = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return r.scalar_one_or_none()


async def _get_accounts(session, user_id: int) -> list[PaymentAccount]:
    r = await session.execute(
        select(PaymentAccount).where(PaymentAccount.user_id == user_id)
    )
    return list(r.scalars().all())


def _validate_identifier(method: PaymentMethod, identifier: str) -> tuple[bool, str]:
    """
    تحقق بسيط — هذه هويات وهمية للمتعة فقط.
    الشرط الوحيد: بين 2 و100 حرف.
    """
    identifier = identifier.strip()
    if not identifier or len(identifier) < 2:
        return False, "❌ الاسم الوهمي قصير جداً — أدخل حرفين على الأقل."
    if len(identifier) > 100:
        return False, "❌ الاسم الوهمي طويل جداً — لا يتجاوز 100 حرف."
    return True, identifier


def _render_accounts(accounts: list[PaymentAccount]) -> str:
    if not accounts:
        return "  لا توجد طرق دفع مسجّلة."
    lines = []
    for acc in accounts:
        primary = " ⭐" if acc.is_primary else ""
        lines.append(f"  • {acc.method.arabic_name}{primary}\n    ↳ <code>{acc.account_identifier}</code>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /start — نقطة الدخول (خاص فقط)
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يعمل في المحادثة الخاصة:
    — مستخدم جديد: يبدأ تدفق التسجيل.
    — مستخدم مسجّل: يعرض ملخصاً ويخرج من المحادثة.
    في المجموعات يعرض رسالة ترحيب مختصرة ويوجّه للخاص.
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # في المجموعات — رسالة مختصرة
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            f"👋 مرحباً {user.first_name}!\n"
            f"لإدارة حسابك ابدأ محادثة خاصة معي وأرسل /start"
        )
        return ConversationHandler.END

    async with get_session() as session:
        profile  = await _get_profile(session, user.id)
        accounts = await _get_accounts(session, user.id)

        # إنشاء ملف المستخدم إن لم يكن موجوداً
        if not profile:
            profile = UserProfile(
                user_id    = user.id,
                first_name = user.first_name or "",
                username   = user.username,
            )
            session.add(profile)

    if profile and profile.is_registered and accounts:
        # مستخدم مسجّل — عرض ملخص
        await update.message.reply_text(
            f"👋 أهلاً {user.first_name}!\n\n"
            f"✅ هويتك الوهمية مُسجَّلة.\n\n"
            f"<b>محافظك الوهمية 🎮:</b>\n"
            f"{_render_accounts(accounts)}\n\n"
            f"الأوامر:\n"
            f"  /my_account     — تفاصيل هويتك\n"
            f"  /add_payment    — إضافة/تحديث محفظة وهمية\n"
            f"  /remove_payment — حذف محفظة وهمية\n"
            f"  /set_primary    — تغيير المحفظة الافتراضية"
        )
        return ConversationHandler.END

    # مستخدم جديد — ابدأ التسجيل
    await update.message.reply_text(
        f"🌟 <b>مرحباً {user.first_name}!</b>\n\n"
        f"🎮 هذا النظام <b>وهمي تماماً</b> — للمتعة والتمثيل داخل المجموعة فقط.\n"
        f"لا يتصل بأي حساب مصرفي حقيقي.\n\n"
        f"اختر <b>محفظتك الوهمية</b> لتسجيل هويتك:",
        reply_markup=_method_keyboard(),
    )
    return CHOOSE_METHOD


# ---------------------------------------------------------------------------
# /add_payment — إضافة/تحديث طريقة دفع
# ---------------------------------------------------------------------------

async def cmd_add_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط في المحادثة الخاصة.")
        return ConversationHandler.END

    await update.message.reply_text(
        "💳 <b>إضافة محفظة وهمية 🎮</b>\n\n"
        "اختر المحفظة الوهمية التي تريد إضافة هويتك فيها:",
        reply_markup=_method_keyboard(),
    )
    return CHOOSE_METHOD


# ---------------------------------------------------------------------------
# المرحلة 1 — اختيار طريقة الدفع (callback)
# ---------------------------------------------------------------------------

async def cb_choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ تم إلغاء العملية.")
        return ConversationHandler.END

    method_key = data.removeprefix("pm_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ خيار غير صالح.")
        return ConversationHandler.END

    context.user_data[_KEY_METHOD] = method.value
    await query.edit_message_text(
        f"<b>{method.arabic_name}</b> — وهمية 🎮\n\n"
        f"{method.input_hint}\n\n"
        f"💡 أي اسم أو نص يناسبك — هذا للمتعة فقط!\n"
        f"أو أرسل /cancel للإلغاء."
    )
    return ENTER_ACCOUNT


# ---------------------------------------------------------------------------
# المرحلة 2 — إدخال رقم الحساب (رسالة نصية)
# ---------------------------------------------------------------------------

async def msg_enter_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    raw  = (update.message.text or "").strip()

    method_val = context.user_data.get(_KEY_METHOD)
    if not method_val:
        await update.message.reply_text("⚠️ انتهت الجلسة، ابدأ من جديد بـ /add_payment")
        return ConversationHandler.END

    method = PaymentMethod(method_val)
    valid, result = _validate_identifier(method, raw)
    if not valid:
        await update.message.reply_text(
            f"{result}\n\nأعد إدخال البيانات أو أرسل /cancel للإلغاء."
        )
        return ENTER_ACCOUNT

    identifier = result  # القيمة المُنظَّفة

    async with get_session() as session:
        # تحقق من وجود سجل سابق بنفس الطريقة → تحديث
        r = await session.execute(
            select(PaymentAccount).where(
                PaymentAccount.user_id == user.id,
                PaymentAccount.method  == method,
            )
        )
        acc = r.scalar_one_or_none()

        if acc:
            old_id = acc.account_identifier
            acc.account_identifier = identifier
            action = (
                f"🔄 تم تحديث هويتك الوهمية في <b>{method.arabic_name}</b>.\n\n"
                f"القديم: <code>{old_id}</code>\n"
                f"الجديد: <code>{identifier}</code>"
            )
        else:
            # تحقق إن كان هذا أول حساب — يصبح رئيسياً تلقائياً
            all_accts = await _get_accounts(session, user.id)
            is_primary = len(all_accts) == 0

            session.add(PaymentAccount(
                user_id            = user.id,
                method             = method,
                account_identifier = identifier,
                is_primary         = is_primary,
            ))
            action = (
                f"✅ تم تسجيل هويتك الوهمية في <b>{method.arabic_name}</b>!\n"
                f"المعرّف الوهمي: <code>{identifier}</code>\n\n"
                f"🎮 هذا للمتعة فقط — لا علاقة له بالمال الحقيقي."
            )

        # تحديث ملف المستخدم وتعيينه كمسجّل
        profile = await _get_profile(session, user.id)
        if not profile:
            profile = UserProfile(
                user_id    = user.id,
                first_name = user.first_name or "",
                username   = user.username,
            )
            session.add(profile)
        if not profile.is_registered:
            profile.is_registered = True
            profile.registered_at  = _utcnow()
        profile.first_name = user.first_name or profile.first_name
        profile.username   = user.username

    context.user_data.pop(_KEY_METHOD, None)
    await update.message.reply_text(
        f"{action}\n\n"
        f"استخدم /my_account لعرض جميع طرق دفعك."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /cancel — داخل المحادثة
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_KEY_METHOD, None)
    await update.message.reply_text("❌ تم إلغاء العملية.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /my_account — عرض الحساب
# ---------------------------------------------------------------------------

async def cmd_my_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط في المحادثة الخاصة.")
        return

    async with get_session() as session:
        profile  = await _get_profile(session, user.id)
        accounts = await _get_accounts(session, user.id)

    if not profile or not profile.is_registered:
        await update.message.reply_text(
            "📋 لم تُسجّل هويتك الوهمية بعد.\n"
            "ابدأ بـ /start لإنشاء شخصيتك في اللعبة."
        )
        return

    reg_date = ""
    if profile.registered_at:
        reg_date = profile.registered_at.strftime("%Y-%m-%d")

    await update.message.reply_text(
        f"🎮 <b>هويتك الوهمية</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"الاسم: <b>{profile.first_name}</b>\n"
        f"المعرّف: {'@' + profile.username if profile.username else '—'}\n"
        f"تاريخ التسجيل: {reg_date}\n\n"
        f"<b>محافظك الوهمية ({len(accounts)}) 🎮:</b>\n"
        f"{_render_accounts(accounts)}\n\n"
        f"⚠️ هذا النظام وهمي للمتعة فقط — لا يتصل بأي جهة مالية حقيقية.\n\n"
        f"الإدارة:\n"
        f"  /add_payment    — إضافة/تحديث محفظة\n"
        f"  /remove_payment — حذف محفظة\n"
        f"  /set_primary    — تغيير الافتراضي"
    )


# ---------------------------------------------------------------------------
# /remove_payment — حذف طريقة دفع
# ---------------------------------------------------------------------------

async def cmd_remove_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط في المحادثة الخاصة.")
        return ConversationHandler.END

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)

    if not accounts:
        await update.message.reply_text("ℹ️ لا توجد طرق دفع مسجّلة لحذفها.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🗑 <b>حذف طريقة دفع</b>\n\nاختر الحساب الذي تريد حذفه:",
        reply_markup=_remove_keyboard(accounts),
    )
    return CONFIRM_REMOVE


async def cb_confirm_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ تم إلغاء الحذف.")
        return ConversationHandler.END

    method_key = data.removeprefix("rm_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ خيار غير صالح.")
        return ConversationHandler.END

    async with get_session() as session:
        r = await session.execute(
            select(PaymentAccount).where(
                PaymentAccount.user_id == user.id,
                PaymentAccount.method  == method,
            )
        )
        acc = r.scalar_one_or_none()
        if not acc:
            await query.edit_message_text("⚠️ الحساب غير موجود.")
            return ConversationHandler.END

        was_primary = acc.is_primary
        await session.execute(
            delete(PaymentAccount).where(
                PaymentAccount.user_id == user.id,
                PaymentAccount.method  == method,
            )
        )

        # إذا كان الحساب الرئيسي، عيّن أول حساب متبقٍّ كرئيسي تلقائياً
        # flush لضمان ظهور الحذف في الاستعلامات اللاحقة داخل نفس الـ session
        await session.flush()

        if was_primary:
            remaining = await _get_accounts(session, user.id)
            if remaining:
                remaining[0].is_primary = True

        # إذا لم يعد هناك حسابات → إلغاء التسجيل
        remaining_after = await _get_accounts(session, user.id)
        if not remaining_after:
            profile = await _get_profile(session, user.id)
            if profile:
                profile.is_registered = False
                profile.registered_at  = None

    await query.edit_message_text(
        f"🗑 تم حذف حساب <b>{method.arabic_name}</b>.\n\n"
        f"{'⚠️ لم يعد لديك طرق دفع — استخدم /add_payment لإضافة جديدة.' if not remaining_after else 'استخدم /my_account لعرض الحسابات المتبقية.'}"
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /set_primary — تعيين طريقة الدفع الافتراضية
# ---------------------------------------------------------------------------

async def cmd_set_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط في المحادثة الخاصة.")
        return

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)

    if not accounts:
        await update.message.reply_text("ℹ️ لا توجد طرق دفع مسجّلة.")
        return

    if len(accounts) == 1:
        await update.message.reply_text(
            f"ℹ️ لديك طريقة دفع واحدة فقط وهي الافتراضية تلقائياً:\n"
            f"  {accounts[0].method.arabic_name} — <code>{accounts[0].account_identifier}</code>"
        )
        return

    await update.message.reply_text(
        "⭐ <b>اختر طريقة الدفع الافتراضية:</b>",
        reply_markup=_primary_keyboard(accounts),
    )


async def cb_set_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ تم إلغاء العملية.")
        return

    method_key = data.removeprefix("primary_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ خيار غير صالح.")
        return

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)
        for acc in accounts:
            acc.is_primary = (acc.method == method)

    await query.edit_message_text(
        f"⭐ تم تعيين <b>{method.arabic_name}</b> كطريقة الدفع الافتراضية."
    )


# ---------------------------------------------------------------------------
# تسجيل الإضافة
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    # ConversationHandler للتسجيل وإضافة الحسابات
    reg_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",       cmd_start,       filters=filters.ChatType.PRIVATE),
            CommandHandler("add_payment", cmd_add_payment, filters=filters.ChatType.PRIVATE),
        ],
        states={
            CHOOSE_METHOD: [
                CallbackQueryHandler(cb_choose_method, pattern=r"^pm_"),
            ],
            ENTER_ACCOUNT: [
                CommandHandler("cancel", cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_enter_account),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_chat=True,
        per_user=True,
    )

    # ConversationHandler لحذف الحسابات
    remove_conv = ConversationHandler(
        entry_points=[
            CommandHandler("remove_payment", cmd_remove_payment, filters=filters.ChatType.PRIVATE),
        ],
        states={
            CONFIRM_REMOVE: [
                CallbackQueryHandler(cb_confirm_remove, pattern=r"^rm_"),
                CallbackQueryHandler(cb_confirm_remove, pattern=r"^pm_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_chat=True,
        per_user=True,
    )

    # /start في المجموعات (خارج المحادثة)
    application.add_handler(
        CommandHandler("start", cmd_start, filters=~filters.ChatType.PRIVATE),
        group=1,
    )

    application.add_handler(reg_conv,    group=0)
    application.add_handler(remove_conv, group=0)

    # أوامر مستقلة
    application.add_handler(CommandHandler("my_account",  cmd_my_account),  group=0)
    application.add_handler(CommandHandler("set_primary", cmd_set_primary), group=0)
    application.add_handler(
        CallbackQueryHandler(cb_set_primary, pattern=r"^primary_"),
        group=0,
    )

    logger.info("account plugin registered — registration flow + payment methods.")
