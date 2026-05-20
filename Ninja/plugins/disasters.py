"""
plugins/disasters.py — Elevated-user hierarchy (sudo/support/tiger/wolf).

Adds a multi-tier privilege system on top of OWNER_IDS:
  • Dragon  (sudo)   — Can do almost everything an owner can.
  • Demon   (support)— Support-level helpers.
  • Tiger            — Trusted user, gban-immune.
  • Wolf   (whitelist)— Whitelist user.

Commands (owner+ only):
  /addsudo   /removesudo
  /addsupport /removesupport
  /addtiger  /removetiger
  /addwolf   /removewolf
  /dragons  /demons  /tigers  /wolves — List elevated users.
  /disasters — Show all elevated users.

Elevated users are stored in tgbot/elevated_users.json.
"""

from __future__ import annotations

import html
import json
import logging
import os
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import OWNER_IDS
from core.helpers.extraction import extract_user

logger = logging.getLogger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "elevated_users.json")
_FILE = os.path.normpath(_FILE)

DRAGONS: list[int] = []
DEMONS: list[int] = []
TIGERS: list[int] = []
WOLVES: list[int] = []


def _load() -> None:
    global DRAGONS, DEMONS, TIGERS, WOLVES
    if not os.path.exists(_FILE):
        _save()
        return
    try:
        with open(_FILE) as f:
            data = json.load(f)
        DRAGONS = data.get("sudos", [])
        DEMONS = data.get("supports", [])
        TIGERS = data.get("tigers", [])
        WOLVES = data.get("whitelists", [])
    except Exception as e:
        logger.warning("Could not load elevated_users.json: %s", e)


def _save() -> None:
    try:
        with open(_FILE, "w") as f:
            json.dump(
                {"sudos": DRAGONS, "supports": DEMONS, "tigers": TIGERS, "whitelists": WOLVES},
                f,
                indent=4,
            )
    except Exception as e:
        logger.warning("Could not save elevated_users.json: %s", e)


_load()


def is_dragon(user_id: int) -> bool:
    return user_id in DRAGONS or user_id in OWNER_IDS


def is_demon(user_id: int) -> bool:
    return user_id in DEMONS or is_dragon(user_id)


def is_tiger(user_id: int) -> bool:
    return user_id in TIGERS or is_dragon(user_id)


def is_wolf(user_id: int) -> bool:
    return user_id in WOLVES or is_demon(user_id)


# ---------------------------------------------------------------------------
# Generic add/remove helper
# ---------------------------------------------------------------------------
async def _add_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_list: list[int],
    tier_name: str,
) -> None:
    message = update.effective_message
    caller = update.effective_user
    if caller.id not in OWNER_IDS:
        await message.reply_text("Owner only.")
        return
    user_id = await extract_user(message, context.args)
    if not user_id:
        await message.reply_text("Provide a valid user.")
        return
    if user_id in target_list:
        await message.reply_text(f"User is already a {tier_name}.")
        return
    target_list.append(user_id)
    _save()
    try:
        u = await context.bot.get_chat(user_id)
        name = html.escape(u.first_name)
    except Exception:
        name = str(user_id)
    await message.reply_text(f"Added {name} as <b>{tier_name}</b>.", parse_mode=ParseMode.HTML)


async def _remove_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_list: list[int],
    tier_name: str,
) -> None:
    message = update.effective_message
    caller = update.effective_user
    if caller.id not in OWNER_IDS:
        await message.reply_text("Owner only.")
        return
    user_id = await extract_user(message, context.args)
    if not user_id or user_id not in target_list:
        await message.reply_text(f"User is not a {tier_name}.")
        return
    target_list.remove(user_id)
    _save()
    await message.reply_text(f"Removed from <b>{tier_name}</b>.", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def addsudo(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_user(u, c, DRAGONS, "Dragon (sudo)")


async def removesudo(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_user(u, c, DRAGONS, "Dragon (sudo)")


async def addsupport(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_user(u, c, DEMONS, "Demon (support)")


async def removesupport(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_user(u, c, DEMONS, "Demon (support)")


async def addtiger(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_user(u, c, TIGERS, "Tiger")


async def removetiger(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_user(u, c, TIGERS, "Tiger")


async def addwolf(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_user(u, c, WOLVES, "Wolf")


async def removewolf(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _remove_user(u, c, WOLVES, "Wolf")


async def _list_tier(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_list: list[int], tier_name: str
) -> None:
    message = update.effective_message
    if not user_list:
        await message.reply_text(f"No {tier_name}s currently.")
        return
    lines = [f"<b>{tier_name}s:</b>"]
    for uid in user_list:
        try:
            u = await context.bot.get_chat(uid)
            lines.append(f"• {u.mention_html()} (<code>{uid}</code>)")
        except Exception:
            lines.append(f"• <code>{uid}</code>")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def list_dragons(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_tier(u, c, DRAGONS, "Dragon")


async def list_demons(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_tier(u, c, DEMONS, "Demon")


async def list_tigers(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_tier(u, c, TIGERS, "Tiger")


async def list_wolves(u: Update, c: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_tier(u, c, WOLVES, "Wolf")


async def disasters_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = "<b>Elevated Users (Disasters):</b>\n\n"
    for tier, lst in [("🐉 Dragons (sudo)", DRAGONS), ("👹 Demons (support)", DEMONS),
                      ("🐯 Tigers", TIGERS), ("🐺 Wolves", WOLVES)]:
        if lst:
            text += f"<b>{tier}:</b>\n"
            for uid in lst:
                try:
                    u = await context.bot.get_chat(uid)
                    text += f"  • {u.mention_html()}\n"
                except Exception:
                    text += f"  • <code>{uid}</code>\n"
            text += "\n"
    if text == "<b>Elevated Users (Disasters):</b>\n\n":
        text += "No elevated users set."
    await message.reply_text(text, parse_mode=ParseMode.HTML)


async def register(application: Application) -> None:
    owner_filter = filters.User(user_id=list(OWNER_IDS))
    application.add_handler(CommandHandler("addsudo", addsudo, filters=owner_filter))
    application.add_handler(CommandHandler("removesudo", removesudo, filters=owner_filter))
    application.add_handler(CommandHandler("addsupport", addsupport, filters=owner_filter))
    application.add_handler(CommandHandler("removesupport", removesupport, filters=owner_filter))
    application.add_handler(CommandHandler("addtiger", addtiger, filters=owner_filter))
    application.add_handler(CommandHandler("removetiger", removetiger, filters=owner_filter))
    application.add_handler(CommandHandler("addwolf", addwolf, filters=owner_filter))
    application.add_handler(CommandHandler("removewolf", removewolf, filters=owner_filter))
    application.add_handler(CommandHandler("dragons", list_dragons))
    application.add_handler(CommandHandler("demons", list_demons))
    application.add_handler(CommandHandler("tigers", list_tigers))
    application.add_handler(CommandHandler("wolves", list_wolves))
    application.add_handler(CommandHandler("disasters", disasters_list))
