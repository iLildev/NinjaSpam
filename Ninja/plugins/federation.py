"""
plugins/federation.py — Cross-group ban federation system (inspired by Rose Bot).

A federation is a named group of Telegram chats that share a common ban list.
When a federation admin issues /fban, the target user is banned from every
chat that has joined the federation.

Commands (group admins):
  /joinfed <fed_id>         — Join this chat to an existing federation.
  /leavefed                 — Leave the current federation.
  /chatfed                  — Show which federation this chat belongs to.

Commands (federation admins):
  /fban [@user|reply] [reason]  — Ban a user from all federation chats.
  /funban [@user|reply]         — Lift a federation ban.
  /fbanlist                     — Download the federation ban list as a .txt file.

Commands (federation owner):
  /newfed <name>            — Create a new federation (private chat only).
  /delfed                   — Delete a federation you own.
  /fpromote [@user|reply]   — Grant federation admin rights.
  /fdemote [@user|reply]    — Revoke federation admin rights.
  /fedinfo [fed_id]         — Show federation details and member count.
  /myfeds                   — List federations you own.

Notes:
  - Federation IDs are UUID strings printed when the federation is created.
  - One chat can belong to at most one federation at a time.
  - Propagation errors (bot not in chat, not admin, etc.) are silently ignored.
"""

from __future__ import annotations

import html
import io
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core.helpers.chat_status import user_admin
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models import Chat as ChatModel, User
from database.models_extra import ChatFed, FedAdmin, FedBan, Federation

log = logging.getLogger(__name__)

# Telegram errors safe to swallow during federation ban propagation.
_PROP_ERRORS = frozenset([
    "User is an administrator of the chat",
    "Method is available for supergroup and channel chats only",
    "Not enough rights to restrict/unrestrict chat member",
    "Chat not found",
    "Bot is not a member of the group chat",
])


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

async def _get_fed_for_chat(session, chat_id: int) -> Optional[ChatFed]:
    result = await session.execute(
        select(ChatFed).where(ChatFed.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


async def _is_fed_admin(session, fed_id: str, user_id: int) -> bool:
    """Return True if user_id is the federation owner or a federation admin."""
    fed = await session.get(Federation, fed_id)
    if fed and fed.owner_id == user_id:
        return True
    result = await session.execute(
        select(FedAdmin).where(
            FedAdmin.fed_id == fed_id,
            FedAdmin.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _ensure_chat(session, chat_id: int, title: str = "") -> None:
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=title))
        await session.flush()


# ---------------------------------------------------------------------------
# /newfed
# ---------------------------------------------------------------------------

async def newfed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Create a new federation.

    Only works in private chat to avoid leaking the federation ID publicly.

    Usage:
        /newfed My Federation Name
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != "private":
        await message.reply_text("Create federations in our private chat for security.")
        return

    if not context.args:
        await message.reply_text("Usage: /newfed <federation name>")
        return

    name = " ".join(context.args).strip()
    if len(name) > 64:
        await message.reply_text("Federation name must be 64 characters or less.")
        return

    fed_id = str(uuid.uuid4())

    async with get_session() as session:
        session.add(Federation(fed_id=fed_id, name=name, owner_id=user.id))

    await message.reply_html(
        f"✅ Federation <b>{name}</b> created!\n\n"
        f"<b>Federation ID:</b> <code>{fed_id}</code>\n\n"
        f"Share this ID with group admins so they can /joinfed."
    )
    log.info("Federation %s created by user %s.", fed_id, user.id)


# ---------------------------------------------------------------------------
# /delfed
# ---------------------------------------------------------------------------

async def delfed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Delete a federation you own and remove all its bans and chat links."""
    message = update.effective_message
    user = update.effective_user

    if not context.args:
        await message.reply_text("Usage: /delfed <fed_id>")
        return

    fed_id = context.args[0].strip()

    async with get_session() as session:
        fed = await session.get(Federation, fed_id)
        if fed is None:
            await message.reply_text("Federation not found.")
            return
        if fed.owner_id != user.id:
            await message.reply_text("Only the federation owner can delete it.")
            return

        await session.delete(fed)

    await message.reply_html(f"✅ Federation <b>{fed_id}</b> deleted.")


# ---------------------------------------------------------------------------
# /joinfed
# ---------------------------------------------------------------------------

@user_admin
async def joinfed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Join the current group to a federation.

    Usage:
        /joinfed <fed_id>
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Usage: /joinfed <fed_id>")
        return

    fed_id = context.args[0].strip()

    async with get_session() as session:
        fed = await session.get(Federation, fed_id)
        if fed is None:
            await message.reply_text("Federation not found. Check the ID and try again.")
            return

        existing = await _get_fed_for_chat(session, chat.id)
        if existing:
            if existing.fed_id == fed_id:
                await message.reply_text("This chat is already in that federation.")
                return
            await message.reply_text(
                "This chat is already in a federation. Use /leavefed first."
            )
            return

        await _ensure_chat(session, chat.id, chat.title or "")
        session.add(ChatFed(chat_id=chat.id, fed_id=fed_id))

    # Apply existing federation bans to the new chat.
    async with get_session() as session:
        result = await session.execute(
            select(FedBan).where(FedBan.fed_id == fed_id)
        )
        bans = result.scalars().all()

    for ban in bans:
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=ban.user_id)
        except (BadRequest, Forbidden):
            pass

    await message.reply_html(
        f"✅ Joined federation <b>{fed.name}</b>.\n"
        f"{len(bans)} existing federation ban(s) applied."
    )


# ---------------------------------------------------------------------------
# /leavefed
# ---------------------------------------------------------------------------

@user_admin
async def leavefed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Leave the federation this chat currently belongs to."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return
        fed = await session.get(Federation, chat_fed.fed_id)
        fed_name = fed.name if fed else chat_fed.fed_id
        await session.delete(chat_fed)

    await message.reply_html(f"✅ Left federation <b>{html.escape(str(fed_name))}</b>.")


# ---------------------------------------------------------------------------
# /chatfed
# ---------------------------------------------------------------------------

async def chatfed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show which federation this chat belongs to."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return
        fed = await session.get(Federation, chat_fed.fed_id)

    if fed is None:
        await message.reply_text("Federation data not found.")
        return

    await message.reply_html(
        f"<b>Federation:</b> {fed.name}\n"
        f"<b>ID:</b> <code>{fed.fed_id}</code>\n"
        f"<b>Owner ID:</b> <code>{fed.owner_id}</code>"
    )


# ---------------------------------------------------------------------------
# /fban
# ---------------------------------------------------------------------------

async def fban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Ban a user from all chats in the federation.

    The caller must be the federation owner or a federation admin.

    Usage:
        /fban @username [reason]
        /fban <reply> [reason]
    """
    chat = update.effective_chat
    message = update.effective_message
    admin = update.effective_user

    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify who to federation-ban.")
        return

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return

        if not await _is_fed_admin(session, chat_fed.fed_id, admin.id):
            await message.reply_text("Only federation admins can use /fban.")
            return

        fed = await session.get(Federation, chat_fed.fed_id)

        # Upsert the FedBan entry.
        result = await session.execute(
            select(FedBan).where(
                FedBan.fed_id == chat_fed.fed_id,
                FedBan.user_id == user_id,
            )
        )
        ban_entry = result.scalar_one_or_none()
        if ban_entry:
            ban_entry.reason = reason or ban_entry.reason
            ban_entry.banned_by = admin.id
        else:
            ban_entry = FedBan(
                fed_id=chat_fed.fed_id,
                user_id=user_id,
                banned_by=admin.id,
                reason=reason or None,
            )
            session.add(ban_entry)

        # Collect all chats in this federation.
        result = await session.execute(
            select(ChatFed).where(ChatFed.fed_id == chat_fed.fed_id)
        )
        fed_chats = result.scalars().all()
        chat_ids = [c.chat_id for c in fed_chats]

    # Propagate the ban to every federation chat.
    banned_count: int = 0
    fed_name = fed.name if fed else "federation"

    for cid in chat_ids:
        try:
            await context.bot.ban_chat_member(chat_id=cid, user_id=user_id)
            banned_count += 1
        except (BadRequest, Forbidden) as exc:
            if exc.message not in _PROP_ERRORS:
                log.debug("FedBan propagation error in chat %s: %s", cid, exc.message)
            continue

        # Send i18n auto-ban notification to each chat's log channel
        try:
            from core.i18n import get_chat_lang, t
            from database.models_extra import LogChannelSettings
            from sqlalchemy import select as _sel
            async with get_session() as _s:
                _lr = await _s.execute(
                    _sel(LogChannelSettings).where(LogChannelSettings.chat_id == cid)
                )
                _log_setting = _lr.scalar_one_or_none()
            if _log_setting:
                _lang = await get_chat_lang(cid)
                _mention = f"<a href='tg://user?id={user_id}'>{user_id}</a>"
                await context.bot.send_message(
                    chat_id=_log_setting.log_channel_id,
                    text=t(
                        "fed_auto_ban", _lang,
                        mention=_mention,
                        source_chat=chat.title or str(chat.id),
                        fed_name=fed_name,
                    ),
                    parse_mode="HTML",
                )
        except Exception as _e:
            log.debug("Fed auto-ban log notification failed for chat %s: %s", cid, _e)

    reason_line = f"\n<b>Reason:</b> {reason}" if reason else ""
    await message.reply_html(
        f"🚫 Federation ban issued for <a href='tg://user?id={user_id}'>{user_id}</a>"
        f" in <b>{fed_name}</b>.\n"
        f"Banned from <b>{banned_count}</b> chat(s)."
        f"{reason_line}"
    )


# ---------------------------------------------------------------------------
# /funban
# ---------------------------------------------------------------------------

async def funban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Lift a federation ban and unban the user in all federation chats."""
    chat = update.effective_chat
    message = update.effective_message
    admin = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify who to federation-unban.")
        return

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return

        if not await _is_fed_admin(session, chat_fed.fed_id, admin.id):
            await message.reply_text("Only federation admins can use /funban.")
            return

        result = await session.execute(
            select(FedBan).where(
                FedBan.fed_id == chat_fed.fed_id,
                FedBan.user_id == user_id,
            )
        )
        ban_entry = result.scalar_one_or_none()
        if ban_entry is None:
            await message.reply_text("That user is not federation-banned.")
            return

        await session.delete(ban_entry)

        result = await session.execute(
            select(ChatFed).where(ChatFed.fed_id == chat_fed.fed_id)
        )
        chat_ids = [c.chat_id for c in result.scalars().all()]

    unbanned: int = 0
    for cid in chat_ids:
        try:
            await context.bot.unban_chat_member(chat_id=cid, user_id=user_id, only_if_banned=True)
            unbanned += 1
        except (BadRequest, Forbidden):
            pass

    await message.reply_html(
        f"✅ Federation unban issued for <a href='tg://user?id={user_id}'>{user_id}</a>. "
        f"Unbanned in <b>{unbanned}</b> chat(s)."
    )


# ---------------------------------------------------------------------------
# /fbanlist
# ---------------------------------------------------------------------------

async def fbanlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Download the federation ban list as a text file."""
    chat = update.effective_chat
    message = update.effective_message
    admin = update.effective_user

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return

        if not await _is_fed_admin(session, chat_fed.fed_id, admin.id):
            await message.reply_text("Only federation admins can view the ban list.")
            return

        fed = await session.get(Federation, chat_fed.fed_id)
        result = await session.execute(
            select(FedBan).where(FedBan.fed_id == chat_fed.fed_id)
        )
        bans = result.scalars().all()

    if not bans:
        await message.reply_text("No federation bans yet.")
        return

    lines = [f"Federation Ban List — {fed.name if fed else chat_fed.fed_id}", ""]
    for ban in bans:
        reason = ban.reason or "No reason"
        lines.append(f"User ID: {ban.user_id} | Reason: {reason}")

    content = "\n".join(lines).encode("utf-8")
    buf = io.BytesIO(content)
    buf.name = "fbanlist.txt"
    await message.reply_document(document=buf, caption=f"Federation ban list ({len(bans)} entries)")


# ---------------------------------------------------------------------------
# /fpromote / /fdemote
# ---------------------------------------------------------------------------

async def fpromote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Grant federation admin rights to a user (owner only)."""
    chat = update.effective_chat
    message = update.effective_message
    caller = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify who to promote.")
        return

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return

        fed = await session.get(Federation, chat_fed.fed_id)
        if fed is None or fed.owner_id != caller.id:
            await message.reply_text("Only the federation owner can promote admins.")
            return

        existing = await session.execute(
            select(FedAdmin).where(
                FedAdmin.fed_id == chat_fed.fed_id,
                FedAdmin.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none():
            await message.reply_text("That user is already a federation admin.")
            return

        session.add(FedAdmin(
            fed_id=chat_fed.fed_id,
            user_id=user_id,
            added_by=caller.id,
        ))

    await message.reply_html(
        f"✅ <a href='tg://user?id={user_id}'>{user_id}</a> is now a federation admin."
    )


async def fdemote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Revoke federation admin rights (owner only)."""
    chat = update.effective_chat
    message = update.effective_message
    caller = update.effective_user

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify who to demote.")
        return

    async with get_session() as session:
        chat_fed = await _get_fed_for_chat(session, chat.id)
        if chat_fed is None:
            await message.reply_text("This chat is not in any federation.")
            return

        fed = await session.get(Federation, chat_fed.fed_id)
        if fed is None or fed.owner_id != caller.id:
            await message.reply_text("Only the federation owner can demote admins.")
            return

        result = await session.execute(
            select(FedAdmin).where(
                FedAdmin.fed_id == chat_fed.fed_id,
                FedAdmin.user_id == user_id,
            )
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            await message.reply_text("That user is not a federation admin.")
            return
        await session.delete(entry)

    await message.reply_html(
        f"✓ <a href='tg://user?id={user_id}'>{user_id}</a> removed from federation admins."
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all federation commands."""
    # Owner commands (work in PM and groups)
    application.add_handler(CommandHandler("newfed", newfed))
    application.add_handler(CommandHandler("delfed", delfed))
    application.add_handler(CommandHandler("myfeds", myfeds))
    application.add_handler(CommandHandler("fedinfo", fedinfo))

    # Group admin commands
    application.add_handler(
        CommandHandler("joinfed", joinfed, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("leavefed", leavefed, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("chatfed", chatfed, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("fpromote", fpromote, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("fdemote", fdemote, filters=filters.ChatType.GROUPS)
    )

    # Fed admin commands (work in any fed chat)
    application.add_handler(CommandHandler("fban", fban))
    application.add_handler(CommandHandler("funban", funban))
    application.add_handler(CommandHandler("fbanlist", fbanlist))

    log.info("Plugin loaded: federation")


# ---------------------------------------------------------------------------
# /fedinfo  /myfeds  (stub implementations)
# ---------------------------------------------------------------------------

async def fedinfo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show federation details."""
    message = update.effective_message
    chat = update.effective_chat

    async with get_session() as session:
        if context.args:
            fed_id = context.args[0].strip()
            fed = await session.get(Federation, fed_id)
        else:
            chat_fed = await _get_fed_for_chat(session, chat.id)
            fed = await session.get(Federation, chat_fed.fed_id) if chat_fed else None

        if fed is None:
            await message.reply_text("Federation not found.")
            return

        result = await session.execute(
            select(ChatFed).where(ChatFed.fed_id == fed.fed_id)
        )
        chat_count = len(result.scalars().all())

        result2 = await session.execute(
            select(FedBan).where(FedBan.fed_id == fed.fed_id)
        )
        ban_count = len(result2.scalars().all())

        result3 = await session.execute(
            select(FedAdmin).where(FedAdmin.fed_id == fed.fed_id)
        )
        admin_count = len(result3.scalars().all())

    await message.reply_html(
        f"<b>Federation Info</b>\n\n"
        f"<b>Name:</b> {fed.name}\n"
        f"<b>ID:</b> <code>{fed.fed_id}</code>\n"
        f"<b>Owner:</b> <a href='tg://user?id={fed.owner_id}'>{fed.owner_id}</a>\n"
        f"<b>Chats:</b> {chat_count}\n"
        f"<b>Admins:</b> {admin_count}\n"
        f"<b>Banned users:</b> {ban_count}"
    )


async def myfeds(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List federations owned by the calling user."""
    message = update.effective_message
    user = update.effective_user

    async with get_session() as session:
        result = await session.execute(
            select(Federation).where(Federation.owner_id == user.id)
        )
        feds = result.scalars().all()

    if not feds:
        await message.reply_text("You don't own any federations. Create one with /newfed.")
        return

    lines = ["<b>Your Federations</b>", ""]
    for fed in feds:
        lines.append(f"• <b>{fed.name}</b> — <code>{fed.fed_id}</code>")

    await message.reply_html("\n".join(lines))
