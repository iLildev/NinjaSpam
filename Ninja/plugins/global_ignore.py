"""
plugins/global_ignore.py — Owner-level global user ignore/blacklist.

These are users the bot will silently ignore across all chats
(not to be confused with per-chat blacklists).

Commands (OWNER only):
  /ignore   <user>  [reason]  — Add user to global ignore list.
  /notice   <user>            — Remove user from global ignore list.
  /ignoredlist                — List globally ignored users.
"""

from __future__ import annotations

import html
import logging

from sqlalchemy import BigInteger, String, select, delete
from sqlalchemy.orm import Mapped, mapped_column

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import OWNER_IDS
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.engine import Base

logger = logging.getLogger(__name__)


class GlobalIgnoredUser(Base):
    __tablename__ = "global_ignored_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(String(256), default="")


async def _is_ignored(user_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(GlobalIgnoredUser).where(GlobalIgnoredUser.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None


async def ignore_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id, reason = await extract_user_and_text(message, context.args)
    if not user_id:
        await message.reply_text("Provide a valid user.")
        return
    if user_id in OWNER_IDS:
        await message.reply_text("Cannot ignore an owner.")
        return
    async with get_session() as session:
        existing = await session.execute(
            select(GlobalIgnoredUser).where(GlobalIgnoredUser.user_id == user_id)
        )
        if existing.scalar_one_or_none():
            await message.reply_text("User is already globally ignored.")
            return
        session.add(GlobalIgnoredUser(user_id=user_id, reason=reason or ""))
        await session.commit()
    try:
        u = await context.bot.get_chat(user_id)
        name = html.escape(u.first_name or str(user_id))
    except Exception:
        name = str(user_id)
    await message.reply_text(
        f"🚫 <b>{name}</b> is now globally ignored.",
        parse_mode=ParseMode.HTML,
    )


async def notice_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = await extract_user_and_text(message, context.args)
    if isinstance(user_id, tuple):
        user_id = user_id[0]
    if not user_id:
        await message.reply_text("Provide a valid user.")
        return
    async with get_session() as session:
        result = await session.execute(
            select(GlobalIgnoredUser).where(GlobalIgnoredUser.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            await message.reply_text("User is not globally ignored.")
            return
        await session.delete(row)
        await session.commit()
    await message.reply_text("✅ User is no longer globally ignored.")


async def ignored_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with get_session() as session:
        result = await session.execute(select(GlobalIgnoredUser))
        users = result.scalars().all()
    if not users:
        await message.reply_text("No globally ignored users.")
        return
    lines = ["<b>Globally Ignored Users:</b>"]
    for u in users:
        try:
            chat = await context.bot.get_chat(u.user_id)
            entry = chat.mention_html()
        except Exception:
            entry = f"<code>{u.user_id}</code>"
        if u.reason:
            entry += f" — {html.escape(u.reason)}"
        lines.append(f"• {entry}")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler("ignore", ignore_user, filters=owner_filter))
    application.add_handler(CommandHandler("notice", notice_user, filters=owner_filter))
    application.add_handler(CommandHandler("ignoredlist", ignored_list, filters=owner_filter))
