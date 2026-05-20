"""
plugins/blacklist_stickers.py — Blacklist specific sticker file_unique_ids per chat.

Admins can ban stickers; any non-admin who sends a banned sticker is warned/deleted.

Commands:
  /blsticker          — List blacklisted stickers.
  /addblsticker       — Reply to a sticker to add it to the blacklist.
  /unblsticker        — Reply to a sticker to remove it from the blacklist.
  /rmblsticker        — Alias for /unblsticker.
"""

from __future__ import annotations

import logging

from sqlalchemy import BigInteger, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import user_admin
from database.engine import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
from database.engine import Base


class BlacklistedSticker(Base):
    __tablename__ = "blacklisted_stickers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    file_unique_id: Mapped[str] = mapped_column(String(128), nullable=False)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
async def _get_blstickers(chat_id: int) -> list[str]:
    async with get_session() as session:
        result = await session.execute(
            select(BlacklistedSticker.file_unique_id).where(
                BlacklistedSticker.chat_id == chat_id
            )
        )
        return [r[0] for r in result.all()]


async def _add_blsticker(chat_id: int, file_unique_id: str) -> bool:
    existing = await _get_blstickers(chat_id)
    if file_unique_id in existing:
        return False
    async with get_session() as session:
        session.add(BlacklistedSticker(chat_id=chat_id, file_unique_id=file_unique_id))
        await session.commit()
    return True


async def _remove_blsticker(chat_id: int, file_unique_id: str) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(BlacklistedSticker).where(
                BlacklistedSticker.chat_id == chat_id,
                BlacklistedSticker.file_unique_id == file_unique_id,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        await session.delete(row)
        await session.commit()
    return True


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
@user_admin
async def list_blstickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    stickers = await _get_blstickers(chat.id)
    if not stickers:
        await update.effective_message.reply_text("No blacklisted stickers in this chat.")
        return
    lines = "\n".join(f"• <code>{s}</code>" for s in stickers)
    await update.effective_message.reply_text(
        f"<b>Blacklisted stickers ({len(stickers)}):</b>\n{lines}",
        parse_mode=ParseMode.HTML,
    )


@user_admin
async def add_blsticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    reply = message.reply_to_message
    if not reply or not reply.sticker:
        await message.reply_text("Reply to a sticker to blacklist it.")
        return
    fuid = reply.sticker.file_unique_id
    added = await _add_blsticker(update.effective_chat.id, fuid)
    if added:
        await message.reply_text(f"✅ Sticker added to blacklist.")
    else:
        await message.reply_text("This sticker is already blacklisted.")


@user_admin
async def remove_blsticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    reply = message.reply_to_message
    if not reply or not reply.sticker:
        await message.reply_text("Reply to a sticker to remove it from the blacklist.")
        return
    fuid = reply.sticker.file_unique_id
    removed = await _remove_blsticker(update.effective_chat.id, fuid)
    if removed:
        await message.reply_text("✅ Sticker removed from blacklist.")
    else:
        await message.reply_text("That sticker is not blacklisted.")


# ---------------------------------------------------------------------------
# Auto-delete handler
# ---------------------------------------------------------------------------
async def check_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message.sticker:
        return
    stickers = await _get_blstickers(chat.id)
    if message.sticker.file_unique_id not in stickers:
        return
    user = message.from_user
    member = await chat.get_member(user.id)
    if member.status in ("creator", "administrator"):
        return
    try:
        await message.delete()
        await context.bot.send_message(
            chat.id,
            f"⚠️ Blacklisted sticker from {user.mention_html()} deleted.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Could not delete blacklisted sticker: %s", e)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("blsticker", list_blstickers))
    application.add_handler(CommandHandler("addblsticker", add_blsticker))
    application.add_handler(CommandHandler(["unblsticker", "rmblsticker"], remove_blsticker))
    application.add_handler(
        MessageHandler(filters.Sticker.ALL, check_sticker),
        group=30,
    )
