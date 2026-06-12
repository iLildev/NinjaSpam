"""
plugins/fun.py — Fun commands: slap, pat, runs, roll, toss, shrug,
                 decide, 8ball, table, rlg, shout, react, bluetext.

Ported from FallenRobot/modules/fun.py + reactions.py.
"""

from __future__ import annotations

import html
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from core.helpers.extraction import extract_user
from core import fun_data as fun_strings


# ---------------------------------------------------------------------------
# /runs
# ---------------------------------------------------------------------------
async def runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(random.choice(fun_strings.RUN_STRINGS))


# ---------------------------------------------------------------------------
# /slap
# ---------------------------------------------------------------------------
async def slap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot
    message = update.effective_message
    args = context.args

    curr_user = html.escape(message.from_user.first_name)
    user_id = await extract_user(message, args)

    if user_id and user_id == bot.id:
        temp = random.choice(fun_strings.SLAP_FALLEN_TEMPLATES)
        await message.reply_text(temp if isinstance(temp, str) else temp[0])
        return

    if user_id:
        try:
            slapped_user = await bot.get_chat(user_id)
            user1 = curr_user
            user2 = html.escape(slapped_user.first_name or str(user_id))
        except (BadRequest, Exception):
            user1 = bot.first_name
            user2 = curr_user
    else:
        user1 = bot.first_name
        user2 = curr_user

    temp = random.choice(fun_strings.SLAP_TEMPLATES)
    item = random.choice(fun_strings.ITEMS)
    hit = random.choice(fun_strings.HIT)
    throw = random.choice(fun_strings.THROW)
    reply = temp.format(user1=user1, user2=user2, item=item, hits=hit, throws=throw)

    if message.reply_to_message:
        await message.reply_to_message.reply_text(reply, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(reply, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /pat
# ---------------------------------------------------------------------------
async def pat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot
    message = update.effective_message
    args = context.args

    curr_user = html.escape(message.from_user.first_name)
    user_id = await extract_user(message, args)

    if user_id:
        try:
            patted_user = await bot.get_chat(user_id)
            user1 = curr_user
            user2 = html.escape(patted_user.first_name or str(user_id))
        except Exception:
            user1 = bot.first_name
            user2 = curr_user
    else:
        user1 = bot.first_name
        user2 = curr_user

    reply_to = message.reply_to_message if message.reply_to_message else message
    pat_type = random.choice(("Text", "Gif", "Sticker"))

    if pat_type == "Gif":
        try:
            await reply_to.reply_animation(random.choice(fun_strings.PAT_GIFS))
            return
        except BadRequest:
            pat_type = "Text"

    if pat_type == "Sticker":
        try:
            await reply_to.reply_sticker(random.choice(fun_strings.PAT_STICKERS))
            return
        except BadRequest:
            pat_type = "Text"

    if pat_type == "Text":
        temp = random.choice(fun_strings.PAT_TEMPLATES)
        reply = temp.format(user1=user1, user2=user2)
        await reply_to.reply_text(reply, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /roll  /toss  /shrug  /bluetext  /rlg  /decide  /8ball  /table
# ---------------------------------------------------------------------------
async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(str(random.randint(1, 6)))


async def toss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(random.choice(fun_strings.TOSS))


async def shrug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message if msg.reply_to_message else msg
    await target.reply_text(r"¯\_(ツ)_/¯")


async def bluetext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message if msg.reply_to_message else msg
    await target.reply_text(
        "/BLUE /TEXT\n/MUST /CLICK\n/I /AM /A /STUPID /ANIMAL /THAT /IS /ATTRACTED /TO /COLORS"
    )


async def rlg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    eyes = random.choice(fun_strings.EYES)
    mouth = random.choice(fun_strings.MOUTHS)
    ears = random.choice(fun_strings.EARS)
    if len(eyes) == 2:
        repl = ears[0] + eyes[0] + mouth[0] + eyes[1] + ears[1]
    else:
        repl = ears[0] + eyes[0] + mouth[0] + eyes[0] + ears[1]
    await update.effective_message.reply_text(repl)


async def decide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message if msg.reply_to_message else msg
    await target.reply_text(random.choice(fun_strings.DECIDE))


async def eightball(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message if msg.reply_to_message else msg
    await target.reply_text(random.choice(fun_strings.EIGHTBALL))


async def table(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message if msg.reply_to_message else msg
    await target.reply_text(random.choice(fun_strings.TABLE))


# ---------------------------------------------------------------------------
# /shout
# ---------------------------------------------------------------------------
async def shout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.effective_message.reply_text("Give me some text to shout!")
        return
    text = " ".join(args)
    result = [" ".join(list(text))]
    for pos, symbol in enumerate(text[1:]):
        result.append(symbol + " " + "  " * pos + symbol)
    result = list("\n".join(result))
    result[0] = text[0]
    msg = "```\n" + "".join(result) + "```"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)


# ---------------------------------------------------------------------------
# /react  (from reactions.py)
# ---------------------------------------------------------------------------
async def react(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    reaction = random.choice(fun_strings.REACTIONS)
    if message.reply_to_message:
        await message.reply_to_message.reply_text(reaction)
    else:
        await message.reply_text(reaction)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
async def register(application: Application) -> None:
    application.add_handler(CommandHandler("runs", runs))
    application.add_handler(CommandHandler("slap", slap))
    application.add_handler(CommandHandler("pat", pat))
    application.add_handler(CommandHandler("roll", roll))
    application.add_handler(CommandHandler("toss", toss))
    application.add_handler(CommandHandler("shrug", shrug))
    application.add_handler(CommandHandler("bluetext", bluetext))
    application.add_handler(CommandHandler("rlg", rlg))
    application.add_handler(CommandHandler("decide", decide))
    application.add_handler(CommandHandler("8ball", eightball))
    application.add_handler(CommandHandler("table", table))
    application.add_handler(CommandHandler("shout", shout))
    application.add_handler(CommandHandler("react", react))
