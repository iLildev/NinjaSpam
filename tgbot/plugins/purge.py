"""
plugins/purge.py — Bulk message deletion tools.

Commands:
  /purge              — Delete all messages from the replied-to message up to
                        the current message (inclusive).
  /del                — Delete the single replied-to message.
  /purgefrom <n>      — Delete the last n messages in the chat (max 200).
  /purgeall           — Delete ALL retrievable messages (max 200, admin only).
  /zombies            — Kick all "deleted account" members from the group.

Notes:
  - Telegram only allows deleting messages up to 48 hours old for most bots.
  - /purge counts from the replied message ID to the command message ID and
    issues individual delete calls (bulk delete is not supported in PTB v20
    for groups).
  - All purge commands require admin rights; /zombies also needs ban rights.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from telegram import ChatMember, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import bot_admin, can_delete, user_admin
from core.log_channel import loggable

log = logging.getLogger(__name__)

# Maximum messages scanned per /purgefrom or /purgeall call.
_MAX_PURGE: int = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _delete_range(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    start_id: int,
    end_id: int,
) -> int:
    """
    Attempt to delete every message with ID in [start_id, end_id] inclusive.

    IDs are not guaranteed to be sequential (Telegram skips IDs for service
    messages), so we just attempt each ID and swallow "not found" errors.

    Returns the count of successfully deleted messages.
    """
    deleted: int = 0
    # Delete in small async batches to avoid hitting rate limits.
    ids: List[int] = list(range(start_id, end_id + 1))
    chunk_size: int = 25

    for i in range(0, len(ids), chunk_size):
        chunk = ids[i: i + chunk_size]
        tasks = [
            context.bot.delete_message(chat_id=chat_id, message_id=mid)
            for mid in chunk
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if not isinstance(res, Exception):
                deleted += 1
        # Brief pause between chunks to stay well under Telegram limits.
        await asyncio.sleep(0.3)

    return deleted


# ---------------------------------------------------------------------------
# /purge
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def purge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete all messages from the replied-to message up to this command.

    Must be used as a reply.  Deletes the command message itself as well.

    Usage:
        Reply to the first message to purge, then send /purge.
    """
    message = update.effective_message
    chat = update.effective_chat

    if not message.reply_to_message:
        await message.reply_text("Reply to the first message you want to delete, then use /purge.")
        return

    start_id: int = message.reply_to_message.message_id
    end_id: int = message.message_id

    if end_id - start_id > _MAX_PURGE:
        await message.reply_text(
            f"⚠️ That would purge more than {_MAX_PURGE} messages. "
            f"Use /purgefrom {_MAX_PURGE} to delete the last {_MAX_PURGE} messages instead."
        )
        return

    # Send a temporary notice (will be deleted too).
    notice = await message.reply_text("🗑 Purging messages…")

    deleted = await _delete_range(context, chat.id, start_id, end_id)

    # Try to delete the notice as well.
    try:
        await notice.delete()
    except BadRequest:
        pass

    confirm = await context.bot.send_message(
        chat_id=chat.id,
        text=f"🗑 <b>Purged {deleted} message(s).</b>",
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(3)
    try:
        await confirm.delete()
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# /del
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def del_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete a single message.

    Must be used as a reply.  Both the replied-to message and the /del
    command itself are deleted.

    Usage:
        Reply to the message you want to delete, then send /del.
    """
    message = update.effective_message

    if not message.reply_to_message:
        await message.reply_text("Reply to the message you want to delete.")
        return

    try:
        await message.reply_to_message.delete()
    except BadRequest as exc:
        if exc.message != "Message to delete not found":
            await message.reply_text(f"Couldn't delete: {exc.message}")
        return
    finally:
        try:
            await message.delete()
        except BadRequest:
            pass


# ---------------------------------------------------------------------------
# /purgefrom
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def purgefrom(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Delete the last N messages in the chat.

    Usage:
        /purgefrom 50     — Delete the last 50 messages.
    """
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args or not args[0].isdigit():
        await message.reply_text("Usage: /purgefrom <number>  (e.g. /purgefrom 50)")
        return

    n: int = min(int(args[0]), _MAX_PURGE)
    end_id: int = message.message_id
    start_id: int = max(1, end_id - n)

    notice = await message.reply_text(f"🗑 Deleting last {n} messages…")
    deleted = await _delete_range(context, chat.id, start_id, end_id)

    try:
        await notice.delete()
    except BadRequest:
        pass

    confirm = await context.bot.send_message(
        chat_id=chat.id,
        text=f"🗑 <b>Purged {deleted} message(s).</b>",
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(3)
    try:
        await confirm.delete()
    except BadRequest:
        pass


# ---------------------------------------------------------------------------
# /zombies
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
async def kick_zombies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Scan the group for deleted Telegram accounts ("zombies") and kick them.

    Deleted accounts appear with ``user.is_deleted`` or a first_name of
    "Deleted Account".  Scanning large groups takes time — the bot sends a
    progress notice and reports how many were kicked.

    Usage:
        /zombies
    """
    chat = update.effective_chat
    message = update.effective_message

    # Verify the bot has ban rights.
    if not await can_delete(chat, context.bot.id):
        await message.reply_text("I need 'Delete Messages' permission to do that.")
        return

    notice = await message.reply_text("🔍 Scanning for deleted accounts…")

    kicked: int = 0
    try:
        async for member in context.bot.get_chat_members(chat.id):
            user = member.user
            is_zombie: bool = (
                getattr(user, "is_deleted", False)
                or user.first_name == "Deleted Account"
                or (not user.first_name and not user.username)
            )
            if is_zombie:
                try:
                    await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                    await context.bot.unban_chat_member(
                        chat_id=chat.id, user_id=user.id, only_if_banned=True
                    )
                    kicked += 1
                except (BadRequest, Exception):
                    pass
    except (BadRequest, AttributeError):
        # get_chat_members may not be available in all group types.
        await notice.edit_text(
            "⚠️ Couldn't scan members — this command only works in supergroups."
        )
        return

    try:
        await notice.delete()
    except BadRequest:
        pass

    await message.reply_html(
        f"✅ Done — kicked <b>{kicked}</b> deleted account(s)."
        if kicked
        else "✅ No deleted accounts found."
    )


# ---------------------------------------------------------------------------
# /logdel  (GroupHelp feature — delete + log to the log channel)
# ---------------------------------------------------------------------------

@user_admin
@bot_admin
@loggable
async def logdel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """
    Delete the replied-to message and emit a log entry to the log channel.

    Unlike /del, which silently removes the message, /logdel records the
    sender, text/media type, and message link in the log channel.

    Usage:
        Reply to a message, then: /logdel [optional reason]
    """
    message = update.effective_message
    chat = update.effective_chat
    admin = update.effective_user

    if not message.reply_to_message:
        await message.reply_text("Reply to the message you want to delete and log.")
        return None

    target = message.reply_to_message
    sender = target.from_user
    reason = " ".join(context.args) if context.args else "No reason given"

    # Determine content type for the log entry.
    if target.text:
        content_preview = target.text[:200]
        content_type = "text"
    elif target.caption:
        content_preview = f"[{target.effective_attachment.__class__.__name__ if target.effective_attachment else 'media'}] {target.caption[:150]}"
        content_type = "media+caption"
    elif target.photo:
        content_preview = "[Photo]"
        content_type = "photo"
    elif target.video:
        content_preview = "[Video]"
        content_type = "video"
    elif target.sticker:
        content_preview = f"[Sticker: {target.sticker.emoji or ''}]"
        content_type = "sticker"
    elif target.document:
        content_preview = f"[File: {target.document.file_name or 'document'}]"
        content_type = "document"
    elif target.audio:
        content_preview = "[Audio]"
        content_type = "audio"
    elif target.voice:
        content_preview = "[Voice]"
        content_type = "voice"
    else:
        content_preview = "[Unknown media]"
        content_type = "unknown"

    # Delete the target message and the command.
    try:
        await target.delete()
    except BadRequest as exc:
        if exc.message != "Message to delete not found":
            await message.reply_text(f"Couldn't delete: {exc.message}")
            return None
    finally:
        try:
            await message.delete()
        except BadRequest:
            pass

    sender_name = sender.full_name if sender else "Unknown"
    sender_id = sender.id if sender else 0

    log_msg: str = (
        f"<b>{chat.title}:</b>\n"
        f"#LOGDEL\n"
        f"<b>Admin:</b> {admin.mention_html()}\n"
        f"<b>From:</b> <a href='tg://user?id={sender_id}'>{sender_name}</a> "
        f"(<code>{sender_id}</code>)\n"
        f"<b>Type:</b> {content_type}\n"
        f"<b>Content:</b> <code>{content_preview}</code>\n"
        f"<b>Reason:</b> {reason}"
    )
    return log_msg


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register purge and cleanup command handlers."""
    application.add_handler(
        CommandHandler("purge", purge, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("del", del_message, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("purgefrom", purgefrom, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("zombies", kick_zombies, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("logdel", logdel, filters=filters.ChatType.GROUPS)
    )
    log.info("Plugin loaded: purge")
