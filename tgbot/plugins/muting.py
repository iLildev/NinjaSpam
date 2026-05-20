"""
plugins/muting.py — الكتم المؤقت والدائم وإلغاؤه.

الأوامر:
  /mute  [مستخدم] [سبب]        — كتم دائم.
  /tmute [مستخدم] <مدة> [سبب]  — كتم مؤقت (10m / 2h / 3d).
  /unmute [مستخدم]              — رفع الكتم.

جميع الإجراءات تُسجَّل في قناة السجلات عبر @loggable.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Optional

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import (
    bot_admin,
    can_restrict,
    is_user_ban_protected,
    user_admin,
)
from core.helpers.extraction import extract_user_and_text
from core.helpers.string_handling import extract_time
from core.i18n import t
from core.log_channel import loggable

logger = logging.getLogger(__name__)

_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
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


async def _mention(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        if u.id == user_id:
            return u.mention_html()
    try:
        chat = await context.bot.get_chat(user_id)
        name = html.escape(chat.full_name or chat.title or str(user_id))
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        return f'<a href="tg://user?id={user_id}">{user_id}</a>'


async def _is_muted(chat: Chat, user_id: int) -> bool:
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest:
        return False
    if member.status == ChatMember.RESTRICTED:
        return not getattr(member, "can_send_messages", True)
    return False


async def _is_fully_unmuted(chat: Chat, user_id: int) -> bool:
    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest:
        return True
    if member.status != ChatMember.RESTRICTED:
        return True
    return bool(
        getattr(member, "can_send_messages", True)
        and getattr(member, "can_send_other_messages", True)
    )


@user_admin
@bot_admin
@can_restrict
@loggable
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, reason = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(t("ban_missing_target"))
        return None
    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("mute_admin"))
        return None
    if user_id == context.bot.id:
        await message.reply_text("🙃 لا.")
        return None
    if await _is_muted(chat, user_id):
        await message.reply_text(t("mute_already"))
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id, permissions=_MUTE_PERMISSIONS
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ فشل الكتم: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _mention(user_id, update, context)
    reason_line = f"\n<b>السبب:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"🔇 <b>تم الكتم</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>المستخدم:</b> {mention}\n"
        f"👮 <b>بواسطة:</b> {user.mention_html()}"
        f"{reason_line}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#MUTE\n"
        f"<b>المشرف:</b> {user.mention_html()}\n"
        f"<b>المستخدم:</b> {mention} (<code>{user_id}</code>)"
        f"{reason_line}"
    )


@user_admin
@bot_admin
@can_restrict
@loggable
async def temp_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, args_text = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(
            "⚠️ حدّد المستخدم والمدة:\n<code>/tmute @username 2h [سبب]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None
    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("mute_admin"))
        return None
    if not args_text:
        await message.reply_text(
            "⚠️ أضف المدة بعد اسم المستخدم:\n<code>/tmute @username 2h [سبب]</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    parts = args_text.split(None, 1)
    time_str = parts[0]
    reason = parts[1] if len(parts) > 1 else ""
    until: Optional[datetime] = extract_time(time_str)

    if until is None:
        await message.reply_text(
            f"⚠️ مدة غير صحيحة <code>{html.escape(time_str)}</code>. "
            f"استخدم: <code>10m</code>، <code>2h</code>، أو <code>3d</code>.",
            parse_mode=ParseMode.HTML,
        )
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id,
            permissions=_MUTE_PERMISSIONS, until_date=until,
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ فشل الكتم المؤقت: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _mention(user_id, update, context)
    reason_line = f"\n<b>السبب:</b> {html.escape(reason)}" if reason else ""

    await message.reply_html(
        f"⏱ <b>كتم مؤقت</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>المستخدم:</b> {mention}\n"
        f"⏳ <b>المدة:</b> <code>{html.escape(time_str)}</code>\n"
        f"👮 <b>بواسطة:</b> {user.mention_html()}"
        f"{reason_line}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#TEMP_MUTE\n"
        f"<b>المشرف:</b> {user.mention_html()}\n"
        f"<b>المستخدم:</b> {mention} (<code>{user_id}</code>)\n"
        f"<b>المدة:</b> {html.escape(time_str)}"
        f"{reason_line}"
    )


@user_admin
@bot_admin
@can_restrict
@loggable
async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    user_id, _ = await extract_user_and_text(update, context)

    if not user_id:
        await message.reply_text(t("ban_missing_target"))
        return None

    try:
        member: ChatMember = await chat.get_member(user_id)
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ لم أجد المستخدم: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    if member.status in (ChatMember.LEFT, ChatMember.BANNED):
        await message.reply_text("ℹ️ المستخدم ليس في المجموعة.")
        return None
    if await _is_fully_unmuted(chat, user_id):
        await message.reply_text(t("unmute_already"))
        return None

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id, permissions=_FULL_PERMISSIONS
        )
    except BadRequest as exc:
        await message.reply_text(
            f"⚠️ فشل رفع الكتم: <code>{html.escape(exc.message)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    mention = await _mention(user_id, update, context)
    await message.reply_html(
        f"🔊 <b>رُفع الكتم</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>المستخدم:</b> {mention}\n"
        f"👮 <b>بواسطة:</b> {user.mention_html()}"
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#UNMUTE\n"
        f"<b>المشرف:</b> {user.mention_html()}\n"
        f"<b>المستخدم:</b> {mention} (<code>{user_id}</code>)"
    )


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("mute", mute, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(["tmute", "tempmute"], temp_mute, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("unmute", unmute, filters=filters.ChatType.GROUPS))
    logger.info("Plugin loaded: muting")
