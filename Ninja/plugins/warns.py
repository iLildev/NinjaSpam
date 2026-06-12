"""
plugins/warns.py — Warnings system with automatic actions at limit.

Commands:
  /warn   [user] [reason]    — Issue a warning.
  /warns  [user]          — View warnings.
  /resetwarn [user]       — Clear all warnings.
  /warnlimit <n>            — Set limit (← use /settings).
  /strongwarn on|off        — Toggle action at limit (← use /settings).
  /addwarn <word> <reply>      — Automatic warning filter.
  /nowarn <word>            — Delete warning filter.

Automatic check: MessageHandler checks every message against the filtered words list.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Dict, List, Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import (
    is_user_ban_protected,
    user_admin,
    user_admin_no_reply,
)
from core.helpers.extraction import extract_user_and_text
from core.i18n import t
from core.log_channel import loggable
from database.engine import get_session
from database.models import ChatFeatureSettings, WarnAction, WarnEntry
from database.models_extra import WarnFilter, WarnReason
from db.repositories import warns as warns_repo
from db.repositories import settings as settings_repo

logger = logging.getLogger(__name__)

WARN_GROUP: int = 9
WARN_FILTERS: Dict[int, List[str]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mention_from_id(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    update: "Update | None" = None,
) -> str:
    if update is not None:
        msg = getattr(update, "effective_message", None)
        if msg and getattr(msg, "reply_to_message", None):
            u = msg.reply_to_message.from_user
            if u and u.id == user_id:
                return u.mention_html()
    try:
        chat = await context.bot.get_chat(user_id)
        name = html.escape(chat.full_name or chat.title or str(user_id))
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        return f'<a href="tg://user?id={user_id}">{user_id}</a>'


def _keyword_regex(keyword: str) -> re.Pattern[str]:
    return re.compile(
        r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Core Warn Logic
# ---------------------------------------------------------------------------

async def _do_warn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    warner_name: str,
    reason: str,
) -> Optional[str]:
    chat = await context.bot.get_chat(chat_id)
    message = update.effective_message

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text(t("warn_admin"))
        return None
    if user_id == context.bot.id:
        await message.reply_text(t("warn_self"))
        return None

    warn_count, warn_limit = await warns_repo.add(
        chat_id, user_id,
        issued_by=update.effective_user.id if update.effective_user else 0,
        reason=reason,
    )

    cfg = await settings_repo.get(chat_id)
    warn_action: WarnAction = cfg.warn_action if cfg else WarnAction.BAN

    mention = await _mention_from_id(context, user_id, update)
    reason_line = f"\n<b>Reason:</b> {html.escape(reason)}" if reason else ""

    if warn_count >= warn_limit:
        action_text = "warned"
        try:
            if warn_action == WarnAction.BAN:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "Permanently Banned"
            elif warn_action == WarnAction.KICK:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "Kicked"
            elif warn_action == WarnAction.MUTE:
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id, user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                action_text = "Muted"
        except BadRequest as exc:
            logger.warning("Warn action failed for user %s in %s: %s", user_id, chat_id, exc.message)

        entries = await warns_repo.list_entries(chat_id, user_id)
        reasons = [e.reason for e in entries if e.reason]
        reasons_block = (
            "\n\n<b>📋 Reasons:</b>\n" + "\n".join(f"  {i+1}. {html.escape(r)}" for i, r in enumerate(reasons))
            if reasons else ""
        )
        await message.reply_html(
            f"🚨 <b>Limit reached!</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {mention}\n"
            f"📊 <b>Warnings:</b> [▓▓▓▓▓▓▓▓] {warn_limit}/{warn_limit}\n"
            f"⚡ <b>Action:</b> {action_text}"
            f"{reasons_block}"
        )
        return (
            f"<b>{html.escape(chat.title or '')}:</b>\n"
            f"#WARN_ACTION\n"
            f"<b>By:</b> {warner_name}\n"
            f"<b>User:</b> {mention} (<code>{user_id}</code>)\n"
            f"<b>Count:</b> {warn_count}/{warn_limit}"
            f"{reason_line}"
        )

    filled = round((warn_count / warn_limit) * 8)
    bar = "▓" * filled + "░" * (8 - filled)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Remove Warning", callback_data=f"rmwarn_{chat_id}_{user_id}")
    ]])
    await message.reply_html(
        f"⚠️ <b>Warning</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"📊 <b>Warnings:</b> [{bar}] {warn_count}/{warn_limit}"
        f"{reason_line}",
        reply_markup=keyboard,
    )
    return (
        f"<b>{html.escape(chat.title or '')}:</b>\n"
        f"#WARN\n"
        f"<b>By:</b> {warner_name}\n"
        f"<b>User:</b> {mention} (<code>{user_id}</code>)\n"
        f"<b>Count:</b> {warn_count}/{warn_limit}"
        f"{reason_line}"
    )


# ---------------------------------------------------------------------------
# /warn
# ---------------------------------------------------------------------------

@user_admin
@loggable
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await update.effective_message.reply_text(t("warn_no_target"))
        return None

    admin = update.effective_user
    chat_id = update.effective_chat.id
    warner_name = admin.mention_html() if admin else "Automatic"

    if not reason:
        async with get_session() as session:
            res = await session.execute(
                select(WarnReason).where(WarnReason.chat_id == chat_id).limit(10)
            )
            presets = res.scalars().all()

        if presets:
            buttons = [
                [InlineKeyboardButton(r.reason, callback_data=f"warnreason_{chat_id}_{user_id}_{r.id}")]
                for r in presets
            ]
            buttons.append([InlineKeyboardButton(t("warn_custom_reason"), callback_data=f"warnreason_{chat_id}_{user_id}_custom")])
            context.bot_data[f"pwarn_{chat_id}_{user_id}"] = {"warner": warner_name}
            await update.effective_message.reply_html(
                t("warn_select_reason"),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return None

    return await _do_warn(update, context, chat_id, user_id, warner_name, reason or "")


async def warn_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    parts = (query.data or "").split("_", 3)
    if len(parts) < 4:
        return

    _, chat_id_str, user_id_str, reason_ref = parts
    chat_id, user_id = int(chat_id_str), int(user_id_str)
    pending = context.bot_data.pop(f"pwarn_{chat_id}_{user_id}", None)
    warner_name = pending["warner"] if pending else update.effective_user.mention_html()

    if reason_ref == "custom":
        await query.edit_message_text("Send the warning reason as a reply to this message.")
        return

    reason_text = ""
    try:
        async with get_session() as session:
            res = await session.execute(select(WarnReason).where(WarnReason.id == int(reason_ref)))
            row = res.scalar_one_or_none()
            if row:
                reason_text = row.reason
    except Exception:
        pass

    await query.delete_message()
    await _do_warn(update, context, chat_id, user_id, warner_name, reason_text)


# ---------------------------------------------------------------------------
# /warns
# ---------------------------------------------------------------------------

async def warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user_id, _ = await extract_user_and_text(update, context)
    if not user_id and update.effective_user:
        user_id = update.effective_user.id

    entries = await warns_repo.list_entries(chat.id, user_id)
    count, warn_limit = await warns_repo.count(chat.id, user_id)
    mention = await _mention_from_id(context, user_id)

    if count == 0:
        await message.reply_text(t("warns_none", mention=mention), parse_mode=ParseMode.HTML)
        return

    filled = round((count / warn_limit) * 8)
    bar = "▓" * filled + "░" * (8 - filled)
    reasons = [e.reason for e in entries if e.reason]
    reasons_block = (
        "\n\n<b>📋 Reasons:</b>\n" + "\n".join(f"  {i+1}. {html.escape(r)}" for i, r in enumerate(reasons))
        if reasons else ""
    )
    await message.reply_html(
        f"📊 <b>Warning Record</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"📊 <b>Warnings:</b> [{bar}] {count}/{warn_limit}"
        f"{reasons_block}"
    )


# ---------------------------------------------------------------------------
# /resetwarn
# ---------------------------------------------------------------------------

@user_admin
async def reset_warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(t("warn_need_user"))
        return

    _, warn_limit = await warns_repo.count(chat.id, user_id)
    await warns_repo.clear_all(chat.id, user_id)
    mention = await _mention_from_id(context, user_id, update)
    await message.reply_html(
        f"🗑 <b>Warnings cleared</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {mention}\n"
        f"📊 <b>Warnings:</b> [░░░░░░░░] 0/{warn_limit}"
    )


# ---------------------------------------------------------------------------
# Issue Warning without Update (used by other plugins)
# ---------------------------------------------------------------------------

async def issue_warn(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    reason: str,
    issuer_name: str = "Automatic",
) -> str:
    try:
        member_info = await context.bot.get_chat_member(chat_id, user_id)
        if member_info.status in ("administrator", "creator"):
            return "⛔ Cannot warn admins."
    except Exception:
        pass

    warn_count, warn_limit = await warns_repo.add(chat_id, user_id, issued_by=0, reason=reason)
    cfg = await settings_repo.get(chat_id)
    warn_action = cfg.warn_action if cfg else WarnAction.BAN

    mention = f'<a href="tg://user?id={user_id}">{user_id}</a>'
    reason_text = f" — Reason: {reason}" if reason else ""

    if warn_count >= warn_limit:
        action_text = "warned"
        try:
            if warn_action == WarnAction.BAN:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "Permanently Banned"
            elif warn_action == WarnAction.KICK:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "Kicked"
            elif warn_action == WarnAction.MUTE:
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id, user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                action_text = "Muted"
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ {mention} — {action_text} after reaching the limit ({warn_limit} warnings).{reason_text}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return f"{mention} — {action_text} ({warn_count}/{warn_limit})."

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ {mention} has been warned by {issuer_name}. ({warn_count}/{warn_limit}){reason_text}",
            parse_mode="HTML",
        )
    except Exception:
        pass
    return f"Warning {warn_count}/{warn_limit} issued."


# ---------------------------------------------------------------------------
# /warnlimit — Redirect to /settings
# ---------------------------------------------------------------------------

@user_admin
async def warn_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "⚙️ To set the warning limit, use /settings ← <b>Warnings</b>."
    )


# ---------------------------------------------------------------------------
# /warnaction <ban|kick|mute> — Set per-chat action at warn limit
# ---------------------------------------------------------------------------

_WARN_ACTION_MAP = {
    "ban":  WarnAction.BAN,
    "kick": WarnAction.KICK,
    "mute": WarnAction.MUTE,
}

@user_admin
async def warn_action_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /warnaction <ban|kick|mute>
    Set the action taken when a user reaches their warning limit.
    """
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args or args[0].lower() not in _WARN_ACTION_MAP:
        async with get_session() as session:
            cfg = await session.get(ChatFeatureSettings, chat.id)
        current = cfg.warn_action.value if cfg else WarnAction.BAN.value
        await message.reply_html(
            f"⚠️ Usage: <code>/warnaction &lt;ban|kick|mute&gt;</code>\n"
            f"Current action: <b>{current}</b>"
        )
        return

    action = _WARN_ACTION_MAP[args[0].lower()]

    async with get_session() as session:
        cfg = await session.get(ChatFeatureSettings, chat.id)
        if cfg is None:
            from database.models import Chat as ChatModel
            if not await session.get(ChatModel, chat.id):
                session.add(ChatModel(id=chat.id, title=chat.title or ""))
                await session.flush()
            cfg = ChatFeatureSettings(chat_id=chat.id)
            session.add(cfg)
        cfg.warn_action = action

    icons = {"ban": "🔨", "kick": "👢", "mute": "🔇"}
    icon = icons[args[0].lower()]
    await message.reply_html(
        f"{icon} Warn action set to <b>{action.value}</b>.\n"
        f"Users will be <b>{action.value}ned</b> upon reaching the warn limit."
    )


# ---------------------------------------------------------------------------
# /strongwarn — Redirect to /warnaction
# ---------------------------------------------------------------------------

@user_admin
async def strong_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "⚙️ Use <code>/warnaction &lt;ban|kick|mute&gt;</code> to set the action at warn limit."
    )


# ---------------------------------------------------------------------------
# /addwarn
# ---------------------------------------------------------------------------

@user_admin
async def add_warn_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    raw = (message.text or "").split(None, 1)[-1].strip()

    if not raw or len(raw.split(None, 1)) < 2:
        await message.reply_text("Usage: /addwarn <word> <reply text>")
        return

    parts = raw.split(None, 1)
    keyword = parts[0].lower()
    reply_text = parts[1]

    async with get_session() as session:
        from database.models import Chat as ChatModel
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()
        existing = await session.execute(
            select(WarnFilter).where(WarnFilter.chat_id == chat.id, WarnFilter.keyword == keyword)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.reply_text = reply_text
        else:
            session.add(WarnFilter(chat_id=chat.id, keyword=keyword, reply_text=reply_text))

    triggers = WARN_FILTERS.setdefault(chat.id, [])
    if keyword not in triggers:
        triggers.append(keyword)
        WARN_FILTERS[chat.id] = sorted(triggers, key=lambda k: (-len(k), k))

    await message.reply_text(f"✅ Warning filter added: <code>{keyword}</code>", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /nowarn / /stopwarn
# ---------------------------------------------------------------------------

@user_admin
async def remove_warn_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    raw = (message.text or "").split(None, 1)[-1].strip()

    if not raw:
        await message.reply_text("Send the word to be deleted.")
        return

    keyword = raw.lower()
    async with get_session() as session:
        result = await session.execute(
            select(WarnFilter).where(WarnFilter.chat_id == chat.id, WarnFilter.keyword == keyword)
        )
        row = result.scalar_one_or_none()
        if not row:
            await message.reply_text(f"No filter found for <code>{keyword}</code>.", parse_mode=ParseMode.HTML)
            return
        await session.delete(row)

    if chat.id in WARN_FILTERS and keyword in WARN_FILTERS[chat.id]:
        WARN_FILTERS[chat.id].remove(keyword)

    await message.reply_text(f"✅ Warning filter deleted: <code>{keyword}</code>", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /warnlist
# ---------------------------------------------------------------------------

async def warn_filters_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(WarnFilter).where(WarnFilter.chat_id == chat.id).order_by(WarnFilter.keyword)
        )
        rows = result.scalars().all()

    if not rows:
        await message.reply_text("No warning filters set for this group.")
        return

    lines = ["<b>📋 Warning Filters:</b>"]
    for row in rows:
        preview = (row.reply_text[:40] + "…") if len(row.reply_text) > 40 else row.reply_text
        lines.append(f"• <code>{row.keyword}</code> ← {preview}")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Remove Warn Callback
# ---------------------------------------------------------------------------

@user_admin_no_reply
async def remove_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, chat_id_str, user_id_str = (query.data or "").split("_", 2)
        chat_id, user_id = int(chat_id_str), int(user_id_str)
    except (ValueError, AttributeError):
        await query.edit_message_text("Invalid data.")
        return

    removed = await warns_repo.remove_latest(chat_id, user_id)
    if not removed:
        await query.edit_message_text("No warnings to remove.")
        return

    new_count, _ = await warns_repo.count(chat_id, user_id)
    admin = update.effective_user
    await query.edit_message_text(
        f"✅ Warning removed by {admin.mention_html() if admin else 'Admin'}.\n"
        f"<a href='tg://user?id={user_id}'>{user_id}</a> now has <b>{new_count}</b> warnings.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Auto Message Check
# ---------------------------------------------------------------------------

async def auto_warn_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not user or not chat:
        return

    triggers = WARN_FILTERS.get(chat.id)
    if not triggers:
        return

    text = message.text or message.caption or ""
    if not text:
        return

    for keyword in triggers:
        if _keyword_regex(keyword).search(text):
            async with get_session() as session:
                result = await session.execute(
                    select(WarnFilter).where(
                        WarnFilter.chat_id == chat.id, WarnFilter.keyword == keyword
                    )
                )
                row = result.scalar_one_or_none()
            reply_text = row.reply_text if row else ""
            await _do_warn(update, context, chat.id, user.id, "Automatic", reply_text)
            break


# ---------------------------------------------------------------------------
# Register Plugin
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    async with get_session() as session:
        result = await session.execute(select(WarnFilter))
        for row in result.scalars().all():
            WARN_FILTERS.setdefault(row.chat_id, [])
            if row.keyword not in WARN_FILTERS[row.chat_id]:
                WARN_FILTERS[row.chat_id].append(row.keyword)
    for cid in WARN_FILTERS:
        WARN_FILTERS[cid] = sorted(WARN_FILTERS[cid], key=lambda k: (-len(k), k))

    application.add_handler(CommandHandler("warn", warn, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("warns", warns, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(["resetwarn", "resetwarns"], reset_warns, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("warnlimit",  warn_limit_cmd,  filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("warnaction", warn_action_cmd, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("strongwarn", strong_warn,     filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("addwarn", add_warn_filter, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(["nowarn", "stopwarn"], remove_warn_filter, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler(["warnlist", "warnfilters"], warn_filters_list, filters=filters.ChatType.GROUPS))
    application.add_handler(CallbackQueryHandler(remove_warn_callback, pattern=r"^rmwarn_-?\d+_\d+$"))
    application.add_handler(CallbackQueryHandler(warn_reason_callback, pattern=r"^warnreason_"))
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION), auto_warn_check),
        group=WARN_GROUP,
    )
    logger.info("Plugin loaded: warns (cache: %d chats)", len(WARN_FILTERS))
