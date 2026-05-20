"""
plugins/appeals.py — Ban appeal system.

Banned users can send /appeal <reason> in a private chat with the bot.
The appeal is forwarded to the admins of every group where the user is
banned, with Approve / Reject buttons.

If approved, the bot removes the ban and notifies the user.
If rejected, the user is notified without explanation.

Database: BanAppeal model tracks pending appeals to prevent duplicate
submissions and stores outcomes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.i18n import get_chat_lang, t
from database.engine import get_session
from database.models import Chat
from database.models_extra import BanAppeal, BanRecord

log = logging.getLogger(__name__)

_APPROVE = "appeal_approve"
_REJECT = "appeal_reject"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{name}</a>'


async def _find_user_bans(user_id: int) -> list[BanRecord]:
    """Return all active BanRecord rows for this user."""
    async with get_session() as session:
        result = await session.execute(
            select(BanRecord).where(
                BanRecord.user_id == user_id,
                BanRecord.unbanned == False,  # noqa: E712
            )
        )
        return list(result.scalars().all())


async def _get_chat_admins(bot, chat_id: int) -> list[int]:
    """Return list of admin user_ids for a chat (for notification)."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return [a.user.id for a in admins if not a.user.is_bot]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# /appeal command (private chat only)
# ---------------------------------------------------------------------------

async def cmd_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/appeal <reason> — Submit a ban appeal (private chat only)."""
    if update.effective_user is None or update.effective_message is None:
        return

    # This command should only work in private chats
    if update.effective_chat and update.effective_chat.type != "private":
        return

    user = update.effective_user
    user_id = user.id
    lang = "en"  # Private chat — default to English

    args = context.args or []
    reason = " ".join(args).strip()
    if not reason:
        await update.effective_message.reply_text(t("appeal_usage", lang))
        return

    # Check if user has active bans
    bans = await _find_user_bans(user_id)
    if not bans:
        await update.effective_message.reply_text(t("appeal_no_ban", lang))
        return

    # Check for existing pending appeal
    async with get_session() as session:
        existing = await session.execute(
            select(BanAppeal).where(
                BanAppeal.user_id == user_id,
                BanAppeal.status == "pending",
            )
        )
        if existing.scalar_one_or_none():
            await update.effective_message.reply_text(t("appeal_already_pending", lang))
            return

    # Create appeal records and notify admins for each banned chat
    now = datetime.now(tz=timezone.utc)

    for ban in bans:
        chat_id = ban.chat_id
        chat_lang = await get_chat_lang(chat_id)

        async with get_session() as session:
            appeal = BanAppeal(
                user_id=user_id,
                chat_id=chat_id,
                appeal_text=reason,
                ban_reason=ban.reason or "No reason specified",
                status="pending",
                submitted_at=now,
            )
            session.add(appeal)
            await session.flush()
            appeal_id = appeal.id

        # Get chat title
        try:
            tg_chat = await context.bot.get_chat(chat_id)
            chat_title = tg_chat.title or str(chat_id)
        except Exception:
            chat_title = str(chat_id)

        mention = _mention_html(user_id, user.full_name)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    t("appeal_approve_btn", chat_lang),
                    callback_data=f"{_APPROVE}:{appeal_id}:{user_id}:{chat_id}",
                ),
                InlineKeyboardButton(
                    t("appeal_reject_btn", chat_lang),
                    callback_data=f"{_REJECT}:{appeal_id}:{user_id}:{chat_id}",
                ),
            ]
        ])

        notify_text = t(
            "appeal_notify_admins", chat_lang,
            mention=mention,
            user_id=user_id,
            ban_reason=ban.reason or "No reason specified",
            appeal_text=reason,
            time=now.strftime("%Y-%m-%d %H:%M UTC"),
        )

        # Notify each admin
        admin_ids = await _get_chat_admins(context.bot, chat_id)
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notify_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception as exc:
                log.debug("Could not notify admin %d: %s", admin_id, exc)

        # Confirm to the user
        await update.effective_message.reply_html(
            t("appeal_submitted", lang, chat_title=chat_title, reason=reason)
        )


# ---------------------------------------------------------------------------
# Approve / Reject callbacks
# ---------------------------------------------------------------------------

async def _approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 4:
        return

    _, appeal_id_str, user_id_str, chat_id_str = parts[:4]
    appeal_id = int(appeal_id_str)
    user_id = int(user_id_str)
    chat_id = int(chat_id_str)
    chat_lang = await get_chat_lang(chat_id)

    # Update appeal status
    async with get_session() as session:
        result = await session.execute(
            select(BanAppeal).where(BanAppeal.id == appeal_id)
        )
        appeal = result.scalar_one_or_none()
        if appeal is None or appeal.status != "pending":
            await query.edit_message_text("Appeal already processed.")
            return
        appeal.status = "approved"
        appeal.reviewed_by = update.effective_user.id

    # Unban the user
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        # Also mark BanRecord as unbanned
        async with get_session() as session:
            result = await session.execute(
                select(BanRecord).where(
                    BanRecord.user_id == user_id,
                    BanRecord.chat_id == chat_id,
                    BanRecord.unbanned == False,  # noqa: E712
                )
            )
            ban_record = result.scalar_one_or_none()
            if ban_record:
                ban_record.unbanned = True
    except Exception as exc:
        log.warning("Appeal approve: could not unban user %d from chat %d: %s", user_id, chat_id, exc)

    # Notify the user
    try:
        tg_chat = await context.bot.get_chat(chat_id)
        chat_title = tg_chat.title or str(chat_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=t("appeal_approved", chat_lang, chat_title=chat_title),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await query.edit_message_text(f"✅ Appeal #{appeal_id} approved. User unbanned.")


async def _reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 4:
        return

    _, appeal_id_str, user_id_str, chat_id_str = parts[:4]
    appeal_id = int(appeal_id_str)
    user_id = int(user_id_str)
    chat_id = int(chat_id_str)
    chat_lang = await get_chat_lang(chat_id)

    async with get_session() as session:
        result = await session.execute(
            select(BanAppeal).where(BanAppeal.id == appeal_id)
        )
        appeal = result.scalar_one_or_none()
        if appeal is None or appeal.status != "pending":
            await query.edit_message_text("Appeal already processed.")
            return
        appeal.status = "rejected"
        appeal.reviewed_by = update.effective_user.id

    # Notify the user
    try:
        tg_chat = await context.bot.get_chat(chat_id)
        chat_title = tg_chat.title or str(chat_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=t("appeal_rejected", chat_lang, chat_title=chat_title),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await query.edit_message_text(f"❌ Appeal #{appeal_id} rejected.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("appeal", cmd_appeal, filters=filters.ChatType.PRIVATE),
        group=10,
    )
    application.add_handler(
        CallbackQueryHandler(_approve_callback, pattern=rf"^{_APPROVE}:"),
        group=10,
    )
    application.add_handler(
        CallbackQueryHandler(_reject_callback, pattern=rf"^{_REJECT}:"),
        group=10,
    )
    log.info("Plugin loaded: appeals")
