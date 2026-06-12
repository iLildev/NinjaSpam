"""
plugins/account.py — Fake Accounts System 🎮 (For fun only — no real money involved).

Players create fake identities within the game for fictional wallets:
  Fake Al-Kuraimi 💳 | Fake Al-Rajhi 🏦 | Fake PayPal 🌐

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commands:
  /register       — Start registration (in private chat)
  /my_account     — Display your fake in-game identity
  /add_payment    — Add/Update fake wallet
  /remove_payment — Delete fake wallet
  /set_primary    — Set default fake wallet
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
# Conversation States
# ---------------------------------------------------------------------------
CHOOSE_METHOD   = 0   # User chooses payment method
ENTER_ACCOUNT   = 1   # User enters account number
CONFIRM_REMOVE  = 2   # User confirms deletion

# Temporary key in user_data to store the chosen method
_KEY_METHOD = "_acct_method"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Al-Kuraimi",  callback_data="pm_alkarimi")],
        [InlineKeyboardButton("🏦 Al-Rajhi",  callback_data="pm_alrajhi")],
        [InlineKeyboardButton("🌐 PayPal",   callback_data="pm_paypal")],
        [InlineKeyboardButton("❌ Cancel",     callback_data="pm_cancel")],
    ])


def _remove_keyboard(accounts: list[PaymentAccount]) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        label = f"🗑 {acc.method.english_name} — {acc.account_identifier}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"rm_{acc.method.value}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="pm_cancel")])
    return InlineKeyboardMarkup(buttons)


def _primary_keyboard(accounts: list[PaymentAccount]) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        primary_mark = " ✅" if acc.is_primary else ""
        label = f"{acc.method.english_name}{primary_mark}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"primary_{acc.method.value}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="pm_cancel")])
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
    Simple validation — these are fake IDs for fun only.
    Condition: Between 2 and 100 characters.
    """
    identifier = identifier.strip()
    if not identifier or len(identifier) < 2:
        return False, "❌ Fake name is too short — enter at least 2 characters."
    if len(identifier) > 100:
        return False, "❌ Fake name is too long — maximum 100 characters."
    return True, identifier


def _render_accounts(accounts: list[PaymentAccount]) -> str:
    if not accounts:
        return "  No registered payment methods."
    lines = []
    for acc in accounts:
        primary = " ⭐" if acc.is_primary else ""
        lines.append(f"  • {acc.method.english_name}{primary}\n    ↳ <code>{acc.account_identifier}</code>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /start — Entry point (Private Only)
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Works in private chat:
    — New user: Starts registration flow.
    — Registered user: Shows summary and ends conversation.
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    if update.effective_chat.type != "private":
        return ConversationHandler.END

    async with get_session() as session:
        profile  = await _get_profile(session, user.id)
        accounts = await _get_accounts(session, user.id)

        # Create user profile if it doesn't exist
        if not profile:
            profile = UserProfile(
                user_id    = user.id,
                first_name = user.first_name or "",
                username   = user.username,
            )
            session.add(profile)

    if profile and profile.is_registered and accounts:
        # Registered user — show summary
        await update.message.reply_text(
            f"👋 Hello {user.first_name}!\n\n"
            f"✅ Your fake identity is registered.\n\n"
            f"<b>Your fake wallets 🎮:</b>\n"
            f"{_render_accounts(accounts)}\n\n"
            f"Commands:\n"
            f"  /my_account     — Identity details\n"
            f"  /add_payment    — Add/Update fake wallet\n"
            f"  /remove_payment — Delete fake wallet\n"
            f"  /set_primary    — Change default wallet"
        )
        return ConversationHandler.END

    # New user — start registration
    await update.message.reply_text(
        f"🌟 <b>Welcome {user.first_name}!</b>\n\n"
        f"🎮 This system is <b>entirely fake</b> — for fun and roleplay within the group only.\n"
        f"It does not connect to any real bank account.\n\n"
        f"Choose your <b>fake wallet</b> to register your identity:",
        reply_markup=_method_keyboard(),
    )
    return CHOOSE_METHOD


# ---------------------------------------------------------------------------
# /add_payment — Add/Update payment method
# ---------------------------------------------------------------------------

async def cmd_add_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command only works in private chat.")
        return ConversationHandler.END

    await update.message.reply_text(
        "💳 <b>Add Fake Wallet 🎮</b>\n\n"
        "Choose the fake wallet you want to add your identity to:",
        reply_markup=_method_keyboard(),
    )
    return CHOOSE_METHOD


# ---------------------------------------------------------------------------
# Phase 1 — Choose Method (callback)
# ---------------------------------------------------------------------------

async def cb_choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ Operation cancelled.")
        return ConversationHandler.END

    method_key = data.removeprefix("pm_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ Invalid option.")
        return ConversationHandler.END

    context.user_data[_KEY_METHOD] = method.value
    await query.edit_message_text(
        f"<b>{method.english_name}</b> — Fake 🎮\n\n"
        f"{method.input_hint}\n\n"
        f"💡 Any name or text suits — this is for fun only!\n"
        f"Or send /cancel to abort."
    )
    return ENTER_ACCOUNT


# ---------------------------------------------------------------------------
# Phase 2 — Enter Account Number (text message)
# ---------------------------------------------------------------------------

async def msg_enter_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    raw  = (update.message.text or "").strip()

    method_val = context.user_data.get(_KEY_METHOD)
    if not method_val:
        await update.message.reply_text("⚠️ Session expired, start again with /add_payment")
        return ConversationHandler.END

    method = PaymentMethod(method_val)
    valid, result = _validate_identifier(method, raw)
    if not valid:
        await update.message.reply_text(
            f"{result}\n\nRe-enter the data or send /cancel to abort."
        )
        return ENTER_ACCOUNT

    identifier = result  # Cleaned value

    async with get_session() as session:
        # Check if previous record exists for same method → Update
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
                f"🔄 Your fake identity in <b>{method.english_name}</b> has been updated.\n\n"
                f"Old: <code>{old_id}</code>\n"
                f"New: <code>{identifier}</code>"
            )
        else:
            # Check if this is the first account — becomes primary automatically
            all_accts = await _get_accounts(session, user.id)
            is_primary = len(all_accts) == 0

            session.add(PaymentAccount(
                user_id            = user.id,
                method             = method,
                account_identifier = identifier,
                is_primary         = is_primary,
            ))
            action = (
                f"✅ Your fake identity in <b>{method.english_name}</b> has been registered!\n"
                f"Fake ID: <code>{identifier}</code>\n\n"
                f"🎮 This is for fun only — no real money involved."
            )

        # Update user profile and set as registered
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
        f"Use /my_account to view all your payment methods."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /cancel — Inside conversation
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_KEY_METHOD, None)
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /my_account — View account
# ---------------------------------------------------------------------------

async def cmd_my_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command only works in private chat.")
        return

    async with get_session() as session:
        profile  = await _get_profile(session, user.id)
        accounts = await _get_accounts(session, user.id)

    if not profile or not profile.is_registered:
        await update.message.reply_text(
            "📋 Your fake identity is not registered yet.\n"
            "Start with /start to create your in-game character."
        )
        return

    reg_date = ""
    if profile.registered_at:
        reg_date = profile.registered_at.strftime("%Y-%m-%d")

    await update.message.reply_text(
        f"🎮 <b>Your Fake Identity</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Name: <b>{profile.first_name}</b>\n"
        f"ID: {'@' + profile.username if profile.username else '—'}\n"
        f"Registration Date: {reg_date}\n\n"
        f"<b>Your fake wallets ({len(accounts)}) 🎮:</b>\n"
        f"{_render_accounts(accounts)}\n\n"
        f"⚠️ This system is fake for fun only — it does not connect to any real financial entity.\n\n"
        f"Management:\n"
        f"  /add_payment    — Add/Update wallet\n"
        f"  /remove_payment — Delete wallet\n"
        f"  /set_primary    — Change default"
    )


# ---------------------------------------------------------------------------
# /remove_payment — Delete payment method
# ---------------------------------------------------------------------------

async def cmd_remove_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command only works in private chat.")
        return ConversationHandler.END

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)

    if not accounts:
        await update.message.reply_text("ℹ️ No registered payment methods to delete.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🗑 <b>Delete Payment Method</b>\n\nChoose the account you want to delete:",
        reply_markup=_remove_keyboard(accounts),
    )
    return CONFIRM_REMOVE


async def cb_confirm_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ Deletion cancelled.")
        return ConversationHandler.END

    method_key = data.removeprefix("rm_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ Invalid option.")
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
            await query.edit_message_text("⚠️ Account not found.")
            return ConversationHandler.END

        was_primary = acc.is_primary
        await session.execute(
            delete(PaymentAccount).where(
                PaymentAccount.user_id == user.id,
                PaymentAccount.method  == method,
            )
        )

        # If it was the primary account, assign the first remaining account as primary automatically
        # flush to ensure deletion appears in subsequent queries within the same session
        await session.flush()

        if was_primary:
            remaining = await _get_accounts(session, user.id)
            if remaining:
                remaining[0].is_primary = True

        # If no more accounts → Cancel registration
        remaining_after = await _get_accounts(session, user.id)
        if not remaining_after:
            profile = await _get_profile(session, user.id)
            if profile:
                profile.is_registered = False
                profile.registered_at  = None

    await query.edit_message_text(
        f"🗑 Account <b>{method.english_name}</b> has been deleted.\n\n"
        f"{'⚠️ You no longer have payment methods — use /add_payment to add a new one.' if not remaining_after else 'Use /my_account to view remaining accounts.'}"
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /set_primary — Set default payment method
# ---------------------------------------------------------------------------

async def cmd_set_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ This command only works in private chat.")
        return

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)

    if not accounts:
        await update.message.reply_text("ℹ️ No registered payment methods.")
        return

    if len(accounts) == 1:
        await update.message.reply_text(
            f"ℹ️ You have only one payment method and it is automatically the default:\n"
            f"  {accounts[0].method.english_name} — <code>{accounts[0].account_identifier}</code>"
        )
        return

    await update.message.reply_text(
        "⭐ <b>Choose default payment method:</b>",
        reply_markup=_primary_keyboard(accounts),
    )


async def cb_set_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    data  = query.data

    if data == "pm_cancel":
        await query.edit_message_text("❌ Operation cancelled.")
        return

    method_key = data.removeprefix("primary_")
    try:
        method = PaymentMethod(method_key)
    except ValueError:
        await query.edit_message_text("⚠️ Invalid option.")
        return

    async with get_session() as session:
        accounts = await _get_accounts(session, user.id)
        for acc in accounts:
            acc.is_primary = (acc.method == method)

    await query.edit_message_text(
        f"⭐ <b>{method.english_name}</b> has been set as the default payment method."
    )


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    # ConversationHandler for registration and adding accounts
    reg_conv = ConversationHandler(
        entry_points=[
            CommandHandler("register",    cmd_start,       filters=filters.ChatType.PRIVATE),
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
        conversation_timeout=300,
    )

    # ConversationHandler for deleting accounts
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
        conversation_timeout=300,
    )

    application.add_handler(reg_conv,    group=0)
    application.add_handler(remove_conv, group=0)

    # Independent commands
    application.add_handler(CommandHandler("my_account",  cmd_my_account),  group=0)
    application.add_handler(CommandHandler("set_primary", cmd_set_primary), group=0)
    application.add_handler(
        CallbackQueryHandler(cb_set_primary, pattern=r"^primary_"),
        group=0,
    )

    logger.info("account plugin registered — registration flow + payment methods.")
