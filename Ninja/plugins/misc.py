"""
plugins/misc.py — Miscellaneous fun and utility commands.

Commands:
  /runs                   — Reply with a random "running away" string.
  /slap [@user|reply]     — Slap someone with a random item.
  /echo <text>            — Bot echoes text (owner only).
  /markdownhelp           — Markdown/button syntax cheat-sheet (PM only).
  /gdpr                   — Delete all your personal data (PM only).

Ported from PaulSonOfLars/tgbot misc.py to python-telegram-bot v20 async.
"""

from __future__ import annotations

import logging
import random
from typing import List

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import settings as cfg
from core.helpers.extraction import extract_user_and_text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# /runs strings
# ---------------------------------------------------------------------------

_RUN_STRINGS: List[str] = [
    "Where do you think you're going?",
    "Huh? what? did they get away?",
    "ZZzzZZzz... Huh? what? oh, just them again, nevermind.",
    "Get back here!",
    "Not so fast...",
    "Look out for the wall!",
    "Don't leave me alone with them!!",
    "You run, you die.",
    "Jokes on you, I'm everywhere.",
    "You're gonna regret that...",
    "You could also try /kickme, I hear that's fun.",
    "Go bother someone else, no-one here cares.",
    "You can run, but you can't hide.",
    "Is that all you've got?",
    "I'm behind you...",
    "You've got company!",
    "We can do this the easy way, or the hard way.",
    "You just don't get it, do you?",
    "Yeah, you better run!",
    "Please, remind me how much I care?",
    "I'd run faster if I were you.",
    "Famous last words.",
    "And they disappeared forever, never to be seen again.",
    "Legend has it, they're still running...",
    "Hasta la vista, baby.",
    "Who let the dogs out?",
    "It's funny, because no one cares.",
    "Frankly, my dear, I don't give a damn.",
    "Han shot first. So will I.",
    "As The Doctor would say… RUN!",
]

# ---------------------------------------------------------------------------
# /slap templates & items
# ---------------------------------------------------------------------------

_SLAP_TEMPLATES: List[str] = [
    "{user1} {hits} {user2} with a {item}.",
    "{user1} {hits} {user2} in the face with a {item}.",
    "{user1} {hits} {user2} around a bit with a {item}.",
    "{user1} {throws} a {item} at {user2}.",
    "{user1} grabs a {item} and {throws} it at {user2}'s face.",
    "{user1} launches a {item} in {user2}'s general direction.",
    "{user1} starts slapping {user2} silly with a {item}.",
    "{user1} pins {user2} down and repeatedly {hits} them with a {item}.",
    "{user1} grabs up a {item} and {hits} {user2} with it.",
    "{user1} ties {user2} to a chair and {throws} a {item} at them.",
    "{user1} gave a friendly push to help {user2} learn to swim in lava.",
]

_ITEMS: List[str] = [
    "cast iron skillet", "large trout", "baseball bat", "cricket bat",
    "wooden cane", "nail", "printer", "shovel", "CRT monitor",
    "physics textbook", "toaster", "television", "five ton truck",
    "roll of duct tape", "book", "laptop", "old television",
    "sack of rocks", "rainbow trout", "rubber chicken", "spiked bat",
    "fire extinguisher", "heavy rock", "chunk of dirt", "beehive",
    "piece of rotten meat", "bear", "ton of bricks",
]

_THROW: List[str] = ["throws", "flings", "chucks", "hurls"]
_HIT: List[str] = ["hits", "whacks", "slaps", "smacks", "bashes"]


# ---------------------------------------------------------------------------
# Markdown help text
# ---------------------------------------------------------------------------

_MARKDOWN_HELP = """<b>Markdown & Button Syntax</b>

<b>Formatting:</b>
  <code>_italic_</code>   → <i>italic</i>
  <code>*bold*</code>     → <b>bold</b>
  <code>`code`</code>     → monospace
  <code>[text](url)</code> → hyperlink

<b>Inline URL Buttons (notes/filters/welcome):</b>
  <code>[Label](buttonurl:https://example.com)</code>
  Same row: <code>[B2](buttonurl:https://example.com:same)</code>

<b>Welcome template variables:</b>
  <code>{first}</code>   <code>{last}</code>   <code>{fullname}</code>
  <code>{username}</code>  <code>{mention}</code>  <code>{id}</code>
  <code>{count}</code>   <code>{chatname}</code>

<b>Example:</b>
<code>/setwelcome Hello {mention}! Welcome to {chatname} 🎉
[Rules](buttonurl:https://example.com)</code>
"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def runs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reply with a random 'running away' string."""
    await update.effective_message.reply_text(random.choice(_RUN_STRINGS))


async def slap(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Slap a user (or yourself if no target given)."""
    msg = update.effective_message
    bot_user = context.bot
    sender = update.effective_user

    # Resolve sender display name.
    sender_name = (
        "@" + sender.username if sender.username else sender.full_name
    )
    user1 = sender_name

    user_id, _ = await extract_user_and_text(update, context)

    if user_id:
        try:
            target = await bot_user.get_chat(user_id)
            user2 = (
                "@" + target.username
                if getattr(target, "username", None)
                else getattr(target, "full_name", None) or str(user_id)
            )
        except Exception:
            user2 = str(user_id)
    else:
        # No target → bot slaps the sender.
        user1 = bot_user.first_name
        user2 = sender_name

    text = random.choice(_SLAP_TEMPLATES).format(
        user1=user1,
        user2=user2,
        item=random.choice(_ITEMS),
        hits=random.choice(_HIT),
        throws=random.choice(_THROW),
    )

    if msg.reply_to_message:
        await msg.reply_to_message.reply_text(text)
    else:
        await msg.reply_text(text)


async def echo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Echo text (owner only — deletes the command message)."""
    msg = update.effective_message
    args = msg.text.split(None, 1)
    if len(args) < 2:
        await msg.reply_text("Usage: /echo <text>")
        return
    text = args[1]
    try:
        await msg.delete()
    except Exception:
        pass
    if msg.reply_to_message:
        await msg.reply_to_message.reply_text(text)
    else:
        await update.effective_chat.send_message(text)


async def markdown_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send the markdown/button syntax guide (PM only)."""
    await update.effective_message.reply_html(_MARKDOWN_HELP)
    await update.effective_message.reply_text(
        "Try forwarding the following message to me and I'll preview the buttons!\n\n"
        "/save test Hello! This is a *bold* test.\n"
        "[Google](buttonurl:https://google.com)\n"
        "[GitHub](buttonurl:https://github.com:same)"
    )


async def gdpr(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Delete all personal data stored by the bot for the requesting user (PM only)."""
    user = update.effective_user
    msg = update.effective_message
    if not user:
        return

    await msg.reply_text("⏳ Deleting your identifiable data…")

    deleted_modules: list[str] = []

    # Bio / setme data
    try:
        from plugins.bio import _gdpr_delete as _bio_gdpr
        await _bio_gdpr(user.id)
        deleted_modules.append("bio & info")
    except Exception:
        pass

    # AFK status
    try:
        from database.engine import get_session
        from database.models_extra import AFKStatus
        async with get_session() as session:
            row = await session.get(AFKStatus, user.id)
            if row:
                await session.delete(row)
        deleted_modules.append("AFK status")
    except Exception:
        pass

    # Risk score
    try:
        from database.engine import get_session
        from database.models_extra import UserRiskScore
        async with get_session() as session:
            row = await session.get(UserRiskScore, user.id)
            if row:
                await session.delete(row)
        deleted_modules.append("risk score")
    except Exception:
        pass

    modules_text = ", ".join(deleted_modules) if deleted_modules else "relevant personal data"
    await msg.reply_text(
        f"✅ Your personal data has been deleted ({modules_text}).\n\n"
        "<i>Note: this does not remove you from ban/warn records, as these are "
        "retained for the protection of group members per legitimate interest "
        "under GDPR Art. 6(1)(f). Active bans and federation bans are also "
        "Telegram-side and cannot be removed by me.</i>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register misc commands."""
    application.add_handler(CommandHandler("runs", runs))
    application.add_handler(CommandHandler("slap", slap))
    application.add_handler(
        CommandHandler("echo", echo, filters=filters.User(cfg.OWNER_IDS))
    )
    application.add_handler(
        CommandHandler("markdownhelp", markdown_help, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("gdpr", gdpr, filters=filters.ChatType.PRIVATE)
    )
    log.info("Plugin loaded: misc")
