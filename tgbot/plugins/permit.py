"""
plugins/permit.py — Temporary filter-bypass permission (the-guard-bot inspired).

Grants a specific user temporary permission to bypass automated content filters
(antilinks, blacklist, strict_mode) for either N messages or X minutes —
whichever expires first. Different from /approve (permanent whitelist).

Commands (admins only):
  /permit @user 10         — Allow next 10 messages.
  /permit @user 30m        — Allow for 30 minutes.
  /permit @user 10 30m     — Allow 10 messages OR 30 minutes (first to expire).
  /unpermit @user          — Revoke immediately.
  /permits                 — List currently permitted users in this chat.

Other plugins can call `is_permitted(chat_id, user_id)` to check and consume
one message credit (if applicable). The function returns True while permit is
active and automatically cleans up expired permits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.helpers.extraction import extract_user_and_text
from core.helpers.string_handling import extract_time
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import PermittedUser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API — used by antilinks, blacklist, strict_mode
# ---------------------------------------------------------------------------

async def is_permitted(chat_id: int, user_id: int, consume: bool = True) -> bool:
    """
    Return True if the user has an active temporary permit in this chat.

    If consume=True and a message-count limit is active, decrement it and
    delete the permit record when it reaches zero.
    """
    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        row = await session.get(PermittedUser, (chat_id, user_id))
        if not row:
            return False

        # Check time expiry
        if row.expires_at is not None:
            exp = row.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now >= exp:
                await session.delete(row)
                await session.commit()
                return False

        # Check message-count expiry
        if row.messages_remaining is not None:
            if row.messages_remaining <= 0:
                await session.delete(row)
                await session.commit()
                return False
            if consume:
                row.messages_remaining -= 1
                if row.messages_remaining <= 0:
                    await session.delete(row)
                else:
                    pass  # updated automatically on commit
                await session.commit()

        return True


# ---------------------------------------------------------------------------
# /permit
# ---------------------------------------------------------------------------

@user_admin
async def cmd_permit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    admin = update.effective_user

    user_id, args_text = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "⚠️ الاستخدام:\n"
            "<code>/permit @user 10</code> — 10 رسائل\n"
            "<code>/permit @user 30m</code> — 30 دقيقة\n"
            "<code>/permit @user 10 30m</code> — 10 رسائل أو 30 دقيقة",
            parse_mode=ParseMode.HTML,
        )
        return

    args = (args_text or "").split()
    msg_limit: Optional[int] = None
    time_limit: Optional[datetime] = None

    for a in args:
        if a.isdigit():
            msg_limit = int(a)
        else:
            t = extract_time(a)
            if t:
                time_limit = t

    if not msg_limit and not time_limit:
        await message.reply_text(
            "⚠️ حدد عدد رسائل أو مدة زمنية:\n"
            "<code>/permit @user 10</code>  أو  <code>/permit @user 1h</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    now = datetime.now(tz=timezone.utc)

    async with get_session() as session:
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()

        row = await session.get(PermittedUser, (chat.id, user_id))
        if row:
            row.expires_at = time_limit
            row.messages_remaining = msg_limit
            row.granted_by = admin.id
            row.granted_at = now
        else:
            row = PermittedUser(
                chat_id=chat.id,
                user_id=user_id,
                expires_at=time_limit,
                messages_remaining=msg_limit,
                granted_by=admin.id,
                granted_at=now,
            )
            session.add(row)
        await session.commit()

    try:
        target = await context.bot.get_chat(user_id)
        mention = f'<a href="tg://user?id={user_id}">{target.full_name}</a>'
    except Exception:
        mention = f'<a href="tg://user?id={user_id}">{user_id}</a>'

    parts = []
    if msg_limit:
        parts.append(f"🔢 <b>{msg_limit}</b> رسالة")
    if time_limit:
        delta = time_limit - now
        mins = int(delta.total_seconds() / 60)
        parts.append(f"⏳ <b>{mins}</b> دقيقة")

    await message.reply_text(
        f"✅ <b>Permit ممنوح</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 المستخدم: {mention}\n"
        f"📋 الصلاحية: {' أو '.join(parts)}\n"
        f"<i>سيتجاوز الفلاتر التلقائية (روابط، blacklist، strict mode)</i>",
        parse_mode=ParseMode.HTML,
    )
    logger.info("permit: granted to user %d in chat %d (msgs=%s, time=%s)", user_id, chat.id, msg_limit, time_limit)


# ---------------------------------------------------------------------------
# /unpermit
# ---------------------------------------------------------------------------

@user_admin
async def cmd_unpermit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await update.message.reply_text("⚠️ حدد المستخدم: <code>/unpermit @user</code>", parse_mode=ParseMode.HTML)
        return

    async with get_session() as session:
        row = await session.get(PermittedUser, (chat.id, user_id))
        if not row:
            await update.message.reply_text("ℹ️ هذا المستخدم ليس لديه permit نشط.")
            return
        await session.delete(row)
        await session.commit()

    try:
        target = await context.bot.get_chat(user_id)
        mention = f'<a href="tg://user?id={user_id}">{target.full_name}</a>'
    except Exception:
        mention = f'<a href="tg://user?id={user_id}">{user_id}</a>'

    await update.message.reply_text(
        f"🚫 <b>Permit مُلغى</b>: {mention}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /permits
# ---------------------------------------------------------------------------

@user_admin
async def cmd_permits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    now = datetime.now(tz=timezone.utc)

    async with get_session() as session:
        result = await session.execute(
            select(PermittedUser).where(PermittedUser.chat_id == chat.id)
        )
        rows = result.scalars().all()

    # Filter expired
    active = []
    for r in rows:
        if r.expires_at is not None:
            exp = r.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now >= exp:
                continue
        if r.messages_remaining is not None and r.messages_remaining <= 0:
            continue
        active.append(r)

    if not active:
        await update.message.reply_text("📋 لا يوجد مستخدمون لديهم permit نشط.")
        return

    lines = []
    for r in active:
        parts = []
        if r.messages_remaining is not None:
            parts.append(f"{r.messages_remaining} رسالة")
        if r.expires_at is not None:
            exp = r.expires_at.replace(tzinfo=timezone.utc) if r.expires_at.tzinfo is None else r.expires_at
            delta = exp - now
            mins = max(0, int(delta.total_seconds() / 60))
            parts.append(f"{mins}د متبقية")
        detail = " | ".join(parts) if parts else "دون حد"
        lines.append(f"• <a href='tg://user?id={r.user_id}'>{r.user_id}</a> — {detail}")

    await update.message.reply_text(
        f"📋 <b>Permits النشطة ({len(active)})</b>\n"
        f"━━━━━━━━━━━━━━━\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("permit", cmd_permit, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("unpermit", cmd_unpermit, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("permits", cmd_permits, filters=filters.ChatType.GROUPS)
    )
    logger.info("Plugin loaded: permit")
