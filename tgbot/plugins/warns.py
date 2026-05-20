"""
plugins/warns.py — Warning system with configurable threshold actions.

Commands:
  /warn   [user] [reason]       — Issue a warning; action fires at limit.
  /warns  [user]                — Show warning count and reasons.
  /resetwarn / /resetwarns [u]  — Clear all warnings for a user.
  /warnlimit <n>                — Set the warning threshold (minimum 3).
  /strongwarn <on|off>          — Toggle between ban (on) and kick (off) at limit.
  /addwarn <keyword> <reply>    — Register a keyword that auto-warns on match.
  /nowarn / /stopwarn <keyword> — Remove a warn filter.
  /warnlist / /warnfilters      — List all warn filters for this chat.

Enforcement:
  A MessageHandler (group=9) checks every message for WarnFilter keywords
  using word-boundary regex matching and calls the warn logic automatically.

The in-memory WarnFilter cache (WARN_FILTERS dict) is loaded from the DB on
plugin registration and kept in sync on every add/remove operation.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, select
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
from core.log_channel import loggable
from database.engine import get_session
from database.models import (
    Chat as ChatModel,
    ChatFeatureSettings,
    ChatMember,
    User,
    WarnAction,
    WarnEntry,
)
from database.models_extra import WarnFilter, WarnReason
from core.i18n import get_chat_lang, t

logger = logging.getLogger(__name__)

WARN_GROUP: int = 9

# ---------------------------------------------------------------------------
# In-memory WarnFilter cache: {chat_id: sorted list of keywords}
# Sorted longest-first so more specific keywords match before subsets.
# ---------------------------------------------------------------------------
WARN_FILTERS: Dict[int, List[str]] = {}


def _keyword_regex(keyword: str) -> re.Pattern[str]:
    """Compile a word-boundary regex for a single keyword."""
    return re.compile(
        r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_or_create_member(session, chat_id: int, user_id: int) -> ChatMember:
    result = await session.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        member = ChatMember(chat_id=chat_id, user_id=user_id)
        session.add(member)
        await session.flush()
    return member


async def _ensure_user_and_chat(session, chat_id: int, user_id: int) -> None:
    """Insert User and Chat stubs if they don't already exist."""
    if not await session.get(User, user_id):
        session.add(User(id=user_id, first_name=""))
        await session.flush()
    if not await session.get(ChatModel, chat_id):
        session.add(ChatModel(id=chat_id, title=""))
        await session.flush()


async def _get_or_create_settings(session, chat_id: int) -> ChatFeatureSettings:
    settings = await session.get(ChatFeatureSettings, chat_id)
    if settings is None:
        settings = ChatFeatureSettings(chat_id=chat_id)
        session.add(settings)
        await session.flush()
    return settings


# ---------------------------------------------------------------------------
# Core warn logic
# ---------------------------------------------------------------------------

async def _do_warn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    warner_name: str,
    reason: str,
) -> Optional[str]:
    """
    Issue one warning to user_id in chat_id.

    Returns an HTML log string on success (for @loggable), or None on guard
    failure.  This function is called by both /warn and the auto-warn handler.
    """
    chat = await context.bot.get_chat(chat_id)
    message = update.effective_message

    if await is_user_ban_protected(chat, user_id):
        await message.reply_text("Admins cannot be warned.")
        return None

    if user_id == context.bot.id:
        await message.reply_text("I won't warn myself.")
        return None

    async with get_session() as session:
        await _ensure_user_and_chat(session, chat_id, user_id)
        settings = await _get_or_create_settings(session, chat_id)
        warn_limit: int = settings.warn_limit
        warn_action: WarnAction = settings.warn_action

        # Compute expiry timestamp if the per-chat expiry is configured.
        expires_at = None
        if settings.warn_expiry_days and settings.warn_expiry_days > 0:
            from datetime import timedelta, timezone
            expires_at = __import__("datetime").datetime.now(timezone.utc) + timedelta(
                days=settings.warn_expiry_days
            )

        # Insert the warn entry.
        entry = WarnEntry(
            chat_id=chat_id,
            user_id=user_id,
            reason=reason or None,
            issued_by_id=update.effective_user.id if update.effective_user else None,
            expires_at=expires_at,
        )
        session.add(entry)
        await session.flush()

        # Count only non-expired warns for the threshold check.
        from datetime import timezone as _tz
        from sqlalchemy import or_
        now_utc = __import__("datetime").datetime.now(_tz.utc)
        active_warns_result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat_id,
                WarnEntry.user_id == user_id,
                or_(
                    WarnEntry.expires_at.is_(None),
                    WarnEntry.expires_at > now_utc,
                ),
            )
        )
        active_warns = active_warns_result.scalars().all()
        warn_count: int = len(active_warns)

        # Keep the denormalized counter in sync.
        member = await _get_or_create_member(session, chat_id, user_id)
        member.warn_count = warn_count

    reason_line: str = f"\n<b>Reason:</b> {reason}" if reason else ""

    if warn_count >= warn_limit:
        # Threshold reached — execute the configured action.
        action_text: str = "warned"
        try:
            if warn_action == WarnAction.BAN:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "banned"
            elif warn_action == WarnAction.KICK:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                action_text = "kicked"
            elif warn_action == WarnAction.MUTE:
                from telegram import ChatPermissions
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                action_text = "muted"
        except BadRequest as exc:
            logger.warning(
                "Warn action %s failed for user %s in chat %s: %s",
                warn_action,
                user_id,
                chat_id,
                exc.message,
            )

        # Fetch all reasons for the log.
        async with get_session() as session:
            all_entries_result = await session.execute(
                select(WarnEntry).where(
                    WarnEntry.chat_id == chat_id,
                    WarnEntry.user_id == user_id,
                ).order_by(WarnEntry.created_at)
            )
            all_reasons: List[str] = [
                e.reason for e in all_entries_result.scalars().all() if e.reason
            ]

        reasons_block: str = (
            "\n<b>Reasons:</b>\n" + "\n".join(f"  • {r}" for r in all_reasons)
            if all_reasons
            else ""
        )
        await message.reply_text(
            f"<a href='tg://user?id={user_id}'>{user_id}</a> has been <b>{action_text}</b> "
            f"after reaching {warn_limit} warnings.{reasons_block}",
            parse_mode=ParseMode.HTML,
        )
        log_msg: str = (
            f"<b>{chat.title}:</b>\n"
            f"#WARN_ACTION_{action_text.upper()}\n"
            f"<b>Warned by:</b> {warner_name}\n"
            f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>\n"
            f"<b>Count:</b> {warn_count}/{warn_limit}"
            f"{reason_line}"
        )
        return log_msg

    # Under threshold — show count with inline "Remove warn" button.
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            f"Remove warn [{user_id}]",
            callback_data=f"rmwarn_{chat_id}_{user_id}",
        )]]
    )
    await message.reply_text(
        f"<a href='tg://user?id={user_id}'>{user_id}</a> has "
        f"<b>{warn_count}/{warn_limit}</b> warnings.{reason_line}",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    log_msg = (
        f"<b>{chat.title}:</b>\n"
        f"#WARN\n"
        f"<b>Warned by:</b> {warner_name}\n"
        f"<b>User:</b> <a href='tg://user?id={user_id}'>{user_id}</a>\n"
        f"<b>Count:</b> {warn_count}/{warn_limit}"
        f"{reason_line}"
    )
    return log_msg


# ---------------------------------------------------------------------------
# /warn
# ---------------------------------------------------------------------------

@user_admin
@loggable
async def warn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[str]:
    """Issue a single warning to a group member."""
    user_id, reason = await extract_user_and_text(update, context)
    if not user_id:
        await update.effective_message.reply_text(
            "Reply to the user's message or pass @username / user_id."
        )
        return None

    admin = update.effective_user
    chat_id = update.effective_chat.id
    warner_name: str = admin.mention_html() if admin else "Auto"

    # If no reason provided and chat has predefined reasons — show selector
    if not reason:
        async with get_session() as session:
            res = await session.execute(
                select(WarnReason).where(
                    WarnReason.chat_id == chat_id
                ).order_by(WarnReason.id).limit(10)
            )
            presets = res.scalars().all()

        if presets:
            lang = await get_chat_lang(chat_id)
            buttons = [
                [InlineKeyboardButton(
                    r.reason,
                    callback_data=f"warnreason_{chat_id}_{user_id}_{r.id}"
                )]
                for r in presets
            ]
            buttons.append([InlineKeyboardButton(
                t("warn_custom_reason", lang),
                callback_data=f"warnreason_{chat_id}_{user_id}_custom"
            )])
            keyboard = InlineKeyboardMarkup(buttons)
            # Store pending warn in context
            context.bot_data[f"pwarn_{chat_id}_{user_id}"] = {
                "warner": warner_name,
            }
            await update.effective_message.reply_html(
                t("warn_select_reason", lang),
                reply_markup=keyboard,
            )
            return None  # Will be completed in callback

    return await _do_warn(
        update, context, chat_id, user_id, warner_name, reason
    )


async def warn_reason_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle warn reason selection from inline buttons."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    data = query.data or ""
    # format: warnreason_{chat_id}_{user_id}_{reason_id|custom}
    parts = data.split("_", 3)
    if len(parts) < 4:
        return

    _, chat_id_str, user_id_str, reason_ref = parts
    chat_id = int(chat_id_str)
    user_id = int(user_id_str)

    pending = context.bot_data.pop(f"pwarn_{chat_id}_{user_id}", None)
    warner_name = pending["warner"] if pending else update.effective_user.mention_html()

    if reason_ref == "custom":
        await query.edit_message_text(
            "Send the warn reason as a reply to this message."
        )
        return

    # Fetch reason text from DB
    reason_text = ""
    try:
        reason_id = int(reason_ref)
        async with get_session() as session:
            res = await session.execute(
                select(WarnReason).where(WarnReason.id == reason_id)
            )
            row = res.scalar_one_or_none()
            if row:
                reason_text = row.reason
    except (ValueError, Exception):
        pass

    await query.delete_message()
    await _do_warn(update, context, chat_id, user_id, warner_name, reason_text)


# ---------------------------------------------------------------------------
# /warns
# ---------------------------------------------------------------------------

async def warns(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show warning count and all stored reasons for a user."""
    message = update.effective_message
    chat = update.effective_chat

    user_id: Optional[int]
    user_id, _ = await extract_user_and_text(update, context)
    if not user_id and update.effective_user:
        user_id = update.effective_user.id  # default to self

    async with get_session() as session:
        result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat.id,
                WarnEntry.user_id == user_id,
            ).order_by(WarnEntry.created_at)
        )
        entries = result.scalars().all()
        settings = await session.get(ChatFeatureSettings, chat.id)

    warn_limit: int = settings.warn_limit if settings else 3
    count: int = len(entries)
    reasons: List[str] = [e.reason for e in entries if e.reason]

    if count == 0:
        await message.reply_text(
            f"<a href='tg://user?id={user_id}'>{user_id}</a> has no warnings.",
            parse_mode=ParseMode.HTML,
        )
        return

    reasons_block: str = (
        "\n<b>Reasons:</b>\n" + "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(reasons))
        if reasons
        else ""
    )
    await message.reply_text(
        f"<a href='tg://user?id={user_id}'>{user_id}</a> has "
        f"<b>{count}/{warn_limit}</b> warnings.{reasons_block}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /resetwarn / /resetwarns
# ---------------------------------------------------------------------------

@user_admin
async def reset_warns(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Clear all warnings and reset the counter for a user in this chat."""
    message = update.effective_message
    chat = update.effective_chat

    user_id, _ = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text("Specify a user.")
        return

    async with get_session() as session:
        await session.execute(
            delete(WarnEntry).where(
                WarnEntry.chat_id == chat.id,
                WarnEntry.user_id == user_id,
            )
        )
        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat.id,
                ChatMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member:
            member.warn_count = 0

    await message.reply_text(
        f"Warnings for <a href='tg://user?id={user_id}'>{user_id}</a> have been reset.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /warnlimit
# ---------------------------------------------------------------------------

@user_admin
async def warn_limit_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Set (or show) the warning threshold for this group."""
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        async with get_session() as session:
            settings = await session.get(ChatFeatureSettings, chat.id)
        limit: int = settings.warn_limit if settings else 3
        await message.reply_text(
            f"Current warn limit: <b>{limit}</b>", parse_mode=ParseMode.HTML
        )
        return

    if not context.args[0].isdigit():
        await message.reply_text("Provide a number.")
        return

    new_limit: int = int(context.args[0])
    if new_limit < 3:
        await message.reply_text("Warn limit must be at least 3.")
        return

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id)
        settings.warn_limit = new_limit

    await message.reply_text(
        f"Warn limit set to <b>{new_limit}</b>.", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /strongwarn
# ---------------------------------------------------------------------------

@user_admin
async def strong_warn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Toggle the action taken at the warn threshold.

    /strongwarn on  → BAN at limit.
    /strongwarn off → KICK at limit (can rejoin).
    """
    chat = update.effective_chat
    message = update.effective_message

    if not context.args:
        await message.reply_text("Use /strongwarn on or /strongwarn off.")
        return

    choice: str = context.args[0].lower()
    if choice == "on":
        action = WarnAction.BAN
        label = "ban"
    elif choice == "off":
        action = WarnAction.KICK
        label = "kick"
    else:
        await message.reply_text("Use /strongwarn on or /strongwarn off.")
        return

    async with get_session() as session:
        settings = await _get_or_create_settings(session, chat.id)
        settings.warn_action = action

    await message.reply_text(
        f"Users who hit the warn limit will now be <b>{label}ed</b>.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /addwarn <keyword> <reply>
# ---------------------------------------------------------------------------

@user_admin
async def add_warn_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Register a keyword that automatically issues a warning when detected."""
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw or len(raw.split(None, 1)) < 2:
        await message.reply_text(
            "Usage: /addwarn <keyword> <reply text>"
        )
        return

    parts = raw.split(None, 1)
    keyword: str = parts[0].lower()
    reply_text: str = parts[1]

    async with get_session() as session:
        # Ensure the Chat row exists.
        if not await session.get(ChatModel, chat.id):
            session.add(ChatModel(id=chat.id, title=chat.title or ""))
            await session.flush()

        existing = await session.execute(
            select(WarnFilter).where(
                WarnFilter.chat_id == chat.id,
                WarnFilter.keyword == keyword,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.reply_text = reply_text
        else:
            session.add(WarnFilter(
                chat_id=chat.id,
                keyword=keyword,
                reply_text=reply_text,
            ))

    # Update in-memory cache.
    triggers = WARN_FILTERS.setdefault(chat.id, [])
    if keyword not in triggers:
        triggers.append(keyword)
        WARN_FILTERS[chat.id] = sorted(triggers, key=lambda k: (-len(k), k))

    await message.reply_text(
        f"Warn filter added: <code>{keyword}</code>", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /nowarn / /stopwarn <keyword>
# ---------------------------------------------------------------------------

@user_admin
async def remove_warn_filter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove a warn filter keyword."""
    chat = update.effective_chat
    message = update.effective_message

    raw: str = (message.text or "").split(None, 1)[-1].strip()
    if not raw:
        await message.reply_text("Specify the keyword to remove.")
        return

    keyword: str = raw.lower()

    async with get_session() as session:
        result = await session.execute(
            select(WarnFilter).where(
                WarnFilter.chat_id == chat.id,
                WarnFilter.keyword == keyword,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            await session.delete(row)
        else:
            await message.reply_text(
                f"No warn filter for <code>{keyword}</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

    # Update in-memory cache.
    if chat.id in WARN_FILTERS and keyword in WARN_FILTERS[chat.id]:
        WARN_FILTERS[chat.id].remove(keyword)

    await message.reply_text(
        f"Removed warn filter: <code>{keyword}</code>", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /warnlist / /warnfilters
# ---------------------------------------------------------------------------

async def warn_filters_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all configured warn filters for this chat."""
    chat = update.effective_chat
    message = update.effective_message

    async with get_session() as session:
        result = await session.execute(
            select(WarnFilter).where(WarnFilter.chat_id == chat.id).order_by(WarnFilter.keyword)
        )
        rows = result.scalars().all()

    if not rows:
        await message.reply_text("No warn filters are configured for this group.")
        return

    lines: List[str] = ["<b>Warn Filters:</b>"]
    for row in rows:
        reply_preview = (row.reply_text[:40] + "…") if len(row.reply_text) > 40 else row.reply_text
        lines.append(f"• <code>{row.keyword}</code> → {reply_preview}")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Callback: inline "Remove warn" button
# ---------------------------------------------------------------------------

@user_admin_no_reply
async def remove_warn_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle the inline 'Remove warn' button pressed by an admin."""
    query = update.callback_query
    await query.answer()

    data: str = query.data or ""
    # data format: "rmwarn_{chat_id}_{user_id}"
    try:
        _, chat_id_str, user_id_str = data.split("_", 2)
        chat_id: int = int(chat_id_str)
        user_id: int = int(user_id_str)
    except (ValueError, AttributeError):
        await query.edit_message_text("Invalid callback data.")
        return

    async with get_session() as session:
        # Remove the most recent warn entry for this user.
        result = await session.execute(
            select(WarnEntry).where(
                WarnEntry.chat_id == chat_id,
                WarnEntry.user_id == user_id,
            ).order_by(WarnEntry.created_at.desc()).limit(1)
        )
        entry = result.scalar_one_or_none()
        if entry:
            await session.delete(entry)
            # Decrement counter.
            member_result = await session.execute(
                select(ChatMember).where(
                    ChatMember.chat_id == chat_id,
                    ChatMember.user_id == user_id,
                )
            )
            member = member_result.scalar_one_or_none()
            if member and member.warn_count > 0:
                member.warn_count -= 1
            new_count: int = member.warn_count if member else 0
        else:
            await query.edit_message_text("No warnings to remove.")
            return

    admin = update.effective_user
    await query.edit_message_text(
        f"Warning removed by {admin.mention_html() if admin else 'Admin'}. "
        f"<a href='tg://user?id={user_id}'>{user_id}</a> now has "
        f"<b>{new_count}</b> warning(s).",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Auto-warn enforcement MessageHandler
# ---------------------------------------------------------------------------

async def auto_warn_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Check every group message against the WarnFilter keyword list.
    On a match, issue a warning automatically via _do_warn().
    """
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not user or not chat:
        return

    # Fast path: no filters configured for this chat.
    triggers = WARN_FILTERS.get(chat.id)
    if not triggers:
        return

    text: str = message.text or message.caption or ""
    if not text:
        return

    for keyword in triggers:
        pattern = _keyword_regex(keyword)
        if pattern.search(text):
            # Fetch reply text from DB.
            async with get_session() as session:
                result = await session.execute(
                    select(WarnFilter).where(
                        WarnFilter.chat_id == chat.id,
                        WarnFilter.keyword == keyword,
                    )
                )
                row = result.scalar_one_or_none()
            reply_text: str = row.reply_text if row else ""
            await _do_warn(
                update, context, chat.id, user.id, "Auto", reply_text
            )
            break  # One warn per message.


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Load WarnFilter cache from DB and register all warn handlers."""
    # Populate in-memory cache on startup.
    async with get_session() as session:
        result = await session.execute(select(WarnFilter))
        for row in result.scalars().all():
            if row.chat_id not in WARN_FILTERS:
                WARN_FILTERS[row.chat_id] = []
            if row.keyword not in WARN_FILTERS[row.chat_id]:
                WARN_FILTERS[row.chat_id].append(row.keyword)
    # Sort each chat's keywords longest-first.
    for cid in WARN_FILTERS:
        WARN_FILTERS[cid] = sorted(WARN_FILTERS[cid], key=lambda k: (-len(k), k))

    application.add_handler(
        CommandHandler("warn", warn, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("warns", warns, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(
            ["resetwarn", "resetwarns"], reset_warns, filters=filters.ChatType.GROUPS
        )
    )
    application.add_handler(
        CommandHandler("warnlimit", warn_limit_cmd, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("strongwarn", strong_warn, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("addwarn", add_warn_filter, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(
            ["nowarn", "stopwarn"], remove_warn_filter, filters=filters.ChatType.GROUPS
        )
    )
    application.add_handler(
        CommandHandler(
            ["warnlist", "warnfilters"], warn_filters_list, filters=filters.ChatType.GROUPS
        )
    )
    application.add_handler(
        CallbackQueryHandler(remove_warn_callback, pattern=r"^rmwarn_-?\d+_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(warn_reason_callback, pattern=r"^warnreason_")
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
            auto_warn_check,
        ),
        group=WARN_GROUP,
    )
    logger.info("Plugin loaded: warns (cache: %d chats)", len(WARN_FILTERS))
