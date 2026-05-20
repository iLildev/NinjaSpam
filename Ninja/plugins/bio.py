"""
plugins/bio.py — User bio and self-info system.

Commands:
  /setme <text>          — Set your own info / about-me blurb.
  /me [@user|reply]      — Show a user's self-set info.
  /setbio <text> (reply) — Admin sets another user's bio (cannot self-set).
  /bio [@user|reply]     — Show a user's admin-set bio.
  /clearme               — Clear your own info.
  /clearbio (reply)      — Admin clears another user's bio.

Personal data note:
  /gdpr (from misc.py) calls _gdpr_delete(user_id) to erase this data.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode

MAX_MESSAGE_LENGTH: int = 4096
from telegram.ext import Application, CommandHandler, ContextTypes

from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models_extra import UserBioData, UserInfoData

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GDPR hook — called from misc.py /gdpr command
# ---------------------------------------------------------------------------

async def _gdpr_delete(user_id: int) -> None:
    """Delete all bio/info data for *user_id* (GDPR erasure)."""
    async with get_session() as session:
        info_row = await session.get(UserInfoData, user_id)
        if info_row:
            await session.delete(info_row)
        bio_row = await session.get(UserBioData, user_id)
        if bio_row:
            await session.delete(bio_row)


# ---------------------------------------------------------------------------
# /setme
# ---------------------------------------------------------------------------

async def set_about_me(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set the sender's own info blurb."""
    msg = update.effective_message
    user = update.effective_user
    if not user:
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await msg.reply_text("Usage: /setme <your info text>")
        return

    max_len = MAX_MESSAGE_LENGTH // 4
    if len(text) > max_len:
        await msg.reply_text(
            f"Info is too long! Keep it under {max_len} characters (yours: {len(text)})."
        )
        return

    async with get_session() as session:
        row = await session.get(UserInfoData, user.id)
        if row:
            row.info_text = text
        else:
            session.add(UserInfoData(user_id=user.id, info_text=text))

    await msg.reply_text("✅ Your info has been updated!")


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

async def about_me(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show a user's self-set info."""
    msg = update.effective_message

    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None:
        user_id = update.effective_user.id if update.effective_user else None

    if user_id is None:
        await msg.reply_text("Couldn't determine which user you mean.")
        return

    try:
        target = await update.effective_chat.get_member(user_id)
        name = target.user.full_name
    except Exception:
        name = str(user_id)

    async with get_session() as session:
        row = await session.get(UserInfoData, user_id)

    if row and row.info_text:
        await msg.reply_html(
            f"<b>{name}</b>:\n{row.info_text}"
        )
    else:
        if user_id == update.effective_user.id:
            await msg.reply_text(
                "You haven't set an info message about yourself yet! Use /setme <text>."
            )
        else:
            await msg.reply_text(f"{name} hasn't set an info message yet.")


# ---------------------------------------------------------------------------
# /clearme
# ---------------------------------------------------------------------------

async def clear_about_me(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Clear the sender's own info."""
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        row = await session.get(UserInfoData, user.id)
        if row:
            await session.delete(row)
            await update.effective_message.reply_text("✅ Your info has been cleared.")
        else:
            await update.effective_message.reply_text("You don't have any info set.")


# ---------------------------------------------------------------------------
# /setbio (admin → sets another user's bio)
# ---------------------------------------------------------------------------

async def set_about_bio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set another user's bio (reply to their message)."""
    msg = update.effective_message
    sender = update.effective_user
    if not sender:
        return

    if not msg.reply_to_message:
        await msg.reply_text("Reply to someone's message to set their bio!")
        return

    target_user = msg.reply_to_message.from_user
    if target_user is None:
        await msg.reply_text("Couldn't identify that user.")
        return

    if target_user.id == sender.id:
        await msg.reply_text("Ha, you can't set your own bio! You're at the mercy of others here…")
        return

    if target_user.id == context.bot.id:
        await msg.reply_text("Only owners can set my bio!")
        return

    bio_text = " ".join(context.args) if context.args else msg.text.split(None, 1)[1] if " " in msg.text else ""
    if not bio_text:
        await msg.reply_text("Usage: /setbio <bio text> (as a reply)")
        return

    max_len = MAX_MESSAGE_LENGTH // 4
    if len(bio_text) > max_len:
        await msg.reply_text(
            f"Bio is too long! Keep it under {max_len} characters (yours: {len(bio_text)})."
        )
        return

    async with get_session() as session:
        row = await session.get(UserBioData, target_user.id)
        if row:
            row.bio_text = bio_text
        else:
            session.add(UserBioData(user_id=target_user.id, bio_text=bio_text))

    await msg.reply_text(f"✅ Updated {target_user.first_name}'s bio!")


# ---------------------------------------------------------------------------
# /bio
# ---------------------------------------------------------------------------

async def about_bio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show a user's admin-set bio."""
    msg = update.effective_message

    user_id, _ = await extract_user_and_text(update, context)
    if user_id is None:
        user_id = update.effective_user.id if update.effective_user else None

    if user_id is None:
        await msg.reply_text("Couldn't determine which user you mean.")
        return

    try:
        target = await update.effective_chat.get_member(user_id)
        name = target.user.full_name
    except Exception:
        name = str(user_id)

    async with get_session() as session:
        row = await session.get(UserBioData, user_id)

    if row and row.bio_text:
        await msg.reply_html(
            f"<b>{name}</b>:\n{row.bio_text}"
        )
    else:
        if user_id == update.effective_user.id:
            await msg.reply_text("No one has set a bio for you yet.")
        else:
            await msg.reply_text(f"No bio has been set for {name} yet.")


# ---------------------------------------------------------------------------
# /clearbio
# ---------------------------------------------------------------------------

async def clear_bio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Admin clears another user's bio (reply to their message)."""
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to the user whose bio you want to clear.")
        return

    target_user = msg.reply_to_message.from_user
    if not target_user:
        return

    async with get_session() as session:
        row = await session.get(UserBioData, target_user.id)
        if row:
            await session.delete(row)
            await msg.reply_text(f"✅ Cleared {target_user.first_name}'s bio.")
        else:
            await msg.reply_text("That user has no bio set.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register bio/info commands."""
    application.add_handler(CommandHandler("setme", set_about_me))
    application.add_handler(CommandHandler("me", about_me))
    application.add_handler(CommandHandler("clearme", clear_about_me))
    application.add_handler(CommandHandler("setbio", set_about_bio))
    application.add_handler(CommandHandler("bio", about_bio))
    application.add_handler(CommandHandler("clearbio", clear_bio))
    log.info("Plugin loaded: bio")
