"""
plugins/strict_mode.py — Shieldy-style Strict Mode for new members.

When enabled, every new member who joins is temporarily restricted from
sending media (photos, videos, stickers, GIFs, audio, documents, polls)
for a configurable number of hours.  Plain text messages are still allowed.
After the duration expires, a scheduled job automatically restores full
permissions.

Commands (admins only):
  /strict <hours>   — Enable strict mode with a duration (1–168 h).
  /strict off       — Disable strict mode (does NOT lift existing restrictions).
  /strict status    — Show current config and how many members are restricted.

On restart, pending restrictions are re-scheduled from the DB so no member
is accidentally left restricted forever.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import bot_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import StrictMember, StrictModeSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission sets
# ---------------------------------------------------------------------------

_STRICT_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,   # stickers, GIFs, games
    can_add_web_page_previews=False,
)

_FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_settings(session, chat_id: int) -> StrictModeSettings:
    row = await session.get(StrictModeSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = StrictModeSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# Scheduled job — lift restriction when duration expires
# ---------------------------------------------------------------------------

async def _lift_strict_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by job_queue when a member's strict period expires."""
    data: dict = context.job.data  # type: ignore[union-attr]
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=_FULL_PERMISSIONS,
        )
        logger.info("strict_mode: lifted restriction for user %d in chat %d", user_id, chat_id)
    except (BadRequest, TelegramError) as exc:
        logger.warning("strict_mode: could not lift restriction for user %d: %s", user_id, exc)

    async with get_session() as session:
        row = await session.get(StrictMember, (chat_id, user_id))
        if row:
            await session.delete(row)
            await session.commit()


def _schedule_lift(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    when: datetime,
) -> None:
    """Schedule (or reschedule) the lift job for a member."""
    job_name = f"strict_lift_{chat_id}_{user_id}"
    for job in context.job_queue.get_jobs_by_name(job_name):  # type: ignore[union-attr]
        job.schedule_removal()

    delay = max(0.0, (when - datetime.now(tz=timezone.utc)).total_seconds())
    context.job_queue.run_once(  # type: ignore[union-attr]
        _lift_strict_job,
        when=delay,
        data={"chat_id": chat_id, "user_id": user_id},
        name=job_name,
        chat_id=chat_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# New-member handler
# ---------------------------------------------------------------------------

async def _handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restrict new members when strict mode is active."""
    message = update.message
    if not message or not message.new_chat_members:
        return

    chat = update.effective_chat

    async with get_session() as session:
        cfg = await session.get(StrictModeSettings, chat.id)
        if not cfg or not cfg.enabled:
            return

        hours = cfg.duration_hours
        now = datetime.now(tz=timezone.utc)
        until = now + timedelta(hours=hours)

        for user in message.new_chat_members:
            if user.is_bot:
                continue

            mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=_STRICT_PERMISSIONS,
                )
            except (BadRequest, TelegramError) as exc:
                logger.warning(
                    "strict_mode: could not restrict user %d in chat %d: %s",
                    user.id, chat.id, exc,
                )
                continue

            row = await session.get(StrictMember, (chat.id, user.id))
            if row:
                row.restrict_until = until
                row.joined_at = now
            else:
                session.add(StrictMember(
                    chat_id=chat.id,
                    user_id=user.id,
                    restrict_until=until,
                    joined_at=now,
                ))

            _schedule_lift(context, chat.id, user.id, until)

            dur_text = f"{hours}h" if hours != 1 else "1h"
            await message.reply_text(
                f"🔒 <b>Strict Mode</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👤 {mention} مرحباً بك!\n\n"
                f"⏳ يمكنك <b>الكتابة فقط</b> لمدة <b>{dur_text}</b>\n"
                f"🚫 الصور، الفيديوهات، الملصقات والوسائط محظورة مؤقتاً\n"
                f"✅ بعد انتهاء المدة ترتفع القيود تلقائياً",
                parse_mode=ParseMode.HTML,
            )
            logger.info(
                "strict_mode: restricted user %d in chat %d for %dh",
                user.id, chat.id, hours,
            )

        await session.commit()


# ---------------------------------------------------------------------------
# /strict command
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def cmd_strict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_settings(session, chat.id)

        # /strict off
        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(
                "🔓 <b>Strict Mode</b> — <b>مُعطَّل</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "الأعضاء الجدد لن يُقيَّدوا بعد الآن.\n"
                "⚠️ القيود الحالية لن تُرفع تلقائياً.",
                parse_mode=ParseMode.HTML,
            )
            return

        # /strict status
        if not args or args[0].lower() == "status":
            count_result = await session.execute(
                select(func.count()).select_from(StrictMember).where(
                    StrictMember.chat_id == chat.id
                )
            )
            count: int = count_result.scalar_one()

            state = "✅ <b>مُفعَّل</b>" if cfg.enabled else "❌ <b>مُعطَّل</b>"
            await update.message.reply_text(
                f"🔒 <b>Strict Mode — الحالة</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"الحالة: {state}\n"
                f"⏳ المدة: <b>{cfg.duration_hours} ساعة</b>\n"
                f"👥 الأعضاء المُقيَّدون حالياً: <b>{count}</b>\n\n"
                f"<i>لتفعيل: /strict &lt;ساعات&gt; | لإيقاف: /strict off</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        # /strict <hours>
        arg = args[0]
        if not arg.isdigit():
            await update.message.reply_text(
                "⚠️ الاستخدام:\n"
                "<code>/strict &lt;ساعات&gt;</code> — تفعيل (1–168)\n"
                "<code>/strict off</code> — تعطيل\n"
                "<code>/strict status</code> — الحالة",
                parse_mode=ParseMode.HTML,
            )
            return

        hours = int(arg)
        if not (1 <= hours <= 168):
            await update.message.reply_text(
                "❌ المدة يجب أن تكون بين <b>1</b> و<b>168</b> ساعة (7 أيام).",
                parse_mode=ParseMode.HTML,
            )
            return

        cfg.enabled = True
        cfg.duration_hours = hours
        await session.commit()

    await update.message.reply_text(
        f"🔒 <b>Strict Mode</b> — <b>مُفعَّل</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏳ كل عضو جديد سيُقيَّد من الوسائط لمدة <b>{hours} ساعة</b>\n"
        f"✍️ الكتابة مسموحة، الصور/الفيديو/الملصقات محظورة\n"
        f"✅ القيود ترتفع تلقائياً بعد انتهاء المدة",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Startup: reschedule pending restrictions from DB
# ---------------------------------------------------------------------------

async def _reschedule_pending(application: Application) -> None:
    """Re-queue lift jobs for members still under restriction after restart."""
    now = datetime.now(tz=timezone.utc)
    rescheduled = 0

    async with get_session() as session:
        result = await session.execute(select(StrictMember))
        members: list[StrictMember] = result.scalars().all()

        for m in members:
            until = m.restrict_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)

            if until <= now:
                # Already expired — lift immediately via a near-zero delay
                _schedule_lift(application, m.chat_id, m.user_id, now + timedelta(seconds=1))
            else:
                _schedule_lift(application, m.chat_id, m.user_id, until)
            rescheduled += 1

    if rescheduled:
        logger.info("strict_mode: rescheduled %d pending restrictions on startup", rescheduled)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("strict", cmd_strict, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            _handle_new_member,
            block=False,
        )
    )

    await _reschedule_pending(application)
    logger.info("Plugin loaded: strict_mode")
