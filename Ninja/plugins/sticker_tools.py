"""
plugins/sticker_tools.py — Sticker utility commands.

Commands:
  /getsticker          — Reply to a sticker to get its file_id (for use in
                         /filter or /note commands) and pack info.
  /stickerpack         — Reply to a sticker to get a deep-link to its pack.
  /kang [@emoji] (reply to sticker/image)
                       — Add a sticker to the bot's personal sticker pack for the
                         user (creates the pack on first use).  Emoji defaults to 🤔.
  /sticker2img         — Reply to a sticker to receive it as a PNG image.

Design:
  • /getsticker and /stickerpack are read-only — no permissions needed.
  • /kang creates/manages a per-user sticker pack named
    '<first_name>_by_<botusername>'.  Requires the bot to be able to
    send stickers to the user in PM.
  • /sticker2img downloads the sticker's PNG and forwards it as a document.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode, StickerType
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

logger = logging.getLogger(__name__)

_KANG_EMOJI_DEFAULT = "🤔"


# ---------------------------------------------------------------------------
# /getsticker
# ---------------------------------------------------------------------------

async def getsticker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Return the file_id and pack info of a replied-to sticker."""
    msg = update.effective_message
    if not msg:
        return

    sticker = (msg.reply_to_message or msg).sticker if msg.reply_to_message else None
    if msg.sticker:
        sticker = msg.sticker
    if not sticker:
        await msg.reply_text("Reply to a sticker to get its info.")
        return

    pack_line = ""
    if sticker.set_name:
        pack_line = f"\n🎴 <b>Pack:</b> <a href='https://t.me/addstickers/{sticker.set_name}'>{sticker.set_name}</a>"

    emoji_line = f"\n😀 <b>Emoji:</b> {sticker.emoji}" if sticker.emoji else ""
    animated = " (animated)" if sticker.is_animated else (" (video)" if sticker.is_video else "")

    await msg.reply_text(
        f"🆔 <b>File ID{animated}:</b>\n<code>{sticker.file_id}</code>"
        f"{pack_line}{emoji_line}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# /stickerpack
# ---------------------------------------------------------------------------

async def stickerpack(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send a deep-link to the sticker pack of the replied-to sticker."""
    msg = update.effective_message
    if not msg:
        return

    sticker = msg.reply_to_message.sticker if msg.reply_to_message else None
    if msg.sticker:
        sticker = msg.sticker
    if not sticker:
        await msg.reply_text("Reply to a sticker to get its pack link.")
        return

    if not sticker.set_name:
        await msg.reply_text("That sticker does not belong to a pack.")
        return

    pack_url = f"https://t.me/addstickers/{sticker.set_name}"
    await msg.reply_text(
        f"🎴 <b>Sticker Pack:</b> <a href='{pack_url}'>{sticker.set_name}</a>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False,
    )


# ---------------------------------------------------------------------------
# /sticker2img
# ---------------------------------------------------------------------------

async def sticker2img(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Convert a replied-to sticker to a PNG and send as a document."""
    msg = update.effective_message
    if not msg:
        return

    source = msg.reply_to_message
    sticker = source.sticker if source else None
    if msg.sticker:
        sticker = msg.sticker
        source = msg
    if not sticker:
        await msg.reply_text("Reply to a sticker to convert it to an image.")
        return

    if sticker.is_animated:
        await msg.reply_text("❌ Animated stickers cannot be converted to a static image.")
        return

    if sticker.is_video:
        await msg.reply_text("❌ Video stickers cannot be converted to a static PNG.")
        return

    try:
        sticker_file = await context.bot.get_file(sticker.file_id)
        data = await sticker_file.download_as_bytearray()
    except (BadRequest, TelegramError) as exc:
        await msg.reply_text(f"⚠️ Could not download sticker: {exc.message}")
        return

    from io import BytesIO
    bio = BytesIO(bytes(data))
    bio.name = "sticker.png"
    bio.seek(0)

    await msg.reply_document(
        document=bio,
        filename="sticker.png",
        caption="🖼 Here is your sticker as a PNG.",
    )


# ---------------------------------------------------------------------------
# /kang
# ---------------------------------------------------------------------------

async def kang(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Add a sticker/image to the user's personal sticker pack."""
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    # Only works in PM or when user has started the bot
    emoji = (context.args[0] if context.args else _KANG_EMOJI_DEFAULT)[:2]

    source = msg.reply_to_message or msg
    sticker = source.sticker
    photo = source.photo[-1] if source.photo else None

    if not sticker and not photo:
        await msg.reply_text("Reply to a sticker or photo to kang it.")
        return

    pack_name = f"kang_{user.id}_by_{context.bot.username}"
    pack_title = f"{user.first_name}'s Kang Pack"

    # Download the file
    try:
        if sticker:
            if sticker.is_animated or sticker.is_video:
                await msg.reply_text("❌ Only static stickers can be kanged.")
                return
            file = await context.bot.get_file(sticker.file_id)
            data = await file.download_as_bytearray()
        else:
            file = await context.bot.get_file(photo.file_id)  # type: ignore[union-attr]
            data = await file.download_as_bytearray()
    except (BadRequest, TelegramError) as exc:
        await msg.reply_text(f"⚠️ Download failed: {exc.message}")
        return

    from io import BytesIO
    import struct

    bio = BytesIO(bytes(data))
    bio.name = "sticker.png"

    # Convert to 512x512 PNG using basic resize if needed
    try:
        from PIL import Image  # type: ignore[import]
        img = Image.open(bio).convert("RGBA")
        img.thumbnail((512, 512), Image.LANCZOS)
        out = BytesIO()
        out.name = "sticker.png"
        img.save(out, "PNG")
        out.seek(0)
        bio = out
    except ImportError:
        bio.seek(0)

    # Try to add to existing pack first
    try:
        await context.bot.add_sticker_to_set(
            user_id=user.id,
            name=pack_name,
            sticker={"sticker": bio, "emoji_list": [emoji]},
        )
        pack_url = f"https://t.me/addstickers/{pack_name}"
        await msg.reply_text(
            f"✅ Sticker added to your pack!\n🎴 <a href='{pack_url}'>{pack_title}</a>",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as exc:
        if "STICKERSET_INVALID" in exc.message or "sticker set name is invalid" in exc.message.lower():
            # Pack doesn't exist yet — create it
            bio.seek(0)
            try:
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=pack_name,
                    title=pack_title,
                    stickers=[{"sticker": bio, "emoji_list": [emoji]}],
                    sticker_format="static",
                )
                pack_url = f"https://t.me/addstickers/{pack_name}"
                await msg.reply_text(
                    f"🎉 Created your new sticker pack!\n🎴 <a href='{pack_url}'>{pack_title}</a>",
                    parse_mode=ParseMode.HTML,
                )
            except (BadRequest, TelegramError) as create_exc:
                await msg.reply_text(f"⚠️ Could not create pack: {create_exc.message}")
        else:
            await msg.reply_text(f"⚠️ Could not kang: {exc.message}")
    except TelegramError as exc:
        await msg.reply_text(f"⚠️ Error: {exc.message}")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    application.add_handler(CommandHandler("getsticker",  getsticker))
    application.add_handler(CommandHandler("stickerpack", stickerpack))
    application.add_handler(CommandHandler("sticker2img", sticker2img))
    application.add_handler(CommandHandler("kang",        kang))
    logger.info("sticker_tools plugin registered.")
