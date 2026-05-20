"""
plugins/vote_mute.py — Community Vote-to-Mute system.

Any group member can initiate a vote to mute another member.  An inline
keyboard panel is posted; members tap to vote Yes or No.  When the Yes
votes reach the configured threshold the target is muted for the
configured duration.  If No votes reach the threshold the vote is cancelled.

Commands:
  /votemute @user [duration]    — Start a mute vote (e.g. /votemute @user 1h).
  /vmconfig threshold <n>       — Set votes needed to mute (admin, default 5).
  /vmconfig duration <time>     — Set default mute duration (admin, e.g. 1h, 30m).
  /vmconfig on|off              — Enable / disable the feature (admin).
  /vmconfig status              — Show current settings (admin).
  /cancelvote                   — Cancel the active vote in this chat (admin).

Duration format: <number><unit>
  s = seconds, m = minutes, h = hours, d = days (max 7d).

Notes:
  - Only one active vote per chat at a time.
  - Admins cannot be vote-muted.
  - The voter who initiates a vote automatically casts a Yes vote.
  - The vote panel expires after 5 minutes if the threshold isn't reached.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete as sa_delete, select
from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from core.helpers.extraction import extract_user_and_text
from database.engine import get_session
from database.models_extra import VoteMuteSettings, VoteMuteSession

log = logging.getLogger(__name__)

_CB = "vmute"
_PANEL_TTL_SECONDS = 300   # 5 minutes
_MAX_DURATION_SECONDS = 7 * 24 * 3600

_DURATION_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_UNIT_MAP = {"s": 1, "m": 60, "h": 3600, "d": 86400}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_duration(text: str) -> Optional[int]:
    """Parse e.g. '30m', '2h', '1d' → seconds.  Returns None on error."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    seconds = value * _UNIT_MAP[unit]
    return min(seconds, _MAX_DURATION_SECONDS)


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


async def _get_settings(chat_id: int) -> Optional[VoteMuteSettings]:
    async with get_session() as session:
        return await session.get(VoteMuteSettings, chat_id)


async def _get_active_session(chat_id: int) -> Optional[VoteMuteSession]:
    async with get_session() as session:
        result = await session.execute(
            select(VoteMuteSession).where(
                VoteMuteSession.chat_id == chat_id,
                VoteMuteSession.active == True,
            )
        )
        return result.scalar_one_or_none()


def _make_keyboard(session_id: int, yes: int, no: int, threshold: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🔇 Mute  {yes}/{threshold}",
            callback_data=f"{_CB}:yes:{session_id}",
        ),
        InlineKeyboardButton(
            f"❌ No  {no}/{threshold}",
            callback_data=f"{_CB}:no:{session_id}",
        ),
    ]])


async def _end_session(session_id: int) -> None:
    async with get_session() as session:
        row = await session.get(VoteMuteSession, session_id)
        if row:
            row.active = False
            await session.commit()


# ---------------------------------------------------------------------------
# /votemute
# ---------------------------------------------------------------------------

async def votemute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a community vote to mute a member."""
    message = update.effective_message
    chat = update.effective_chat
    initiator = update.effective_user

    settings = await _get_settings(chat.id)
    if not settings or not settings.enabled:
        await message.reply_text(
            "Vote-mute is not enabled here.\n"
            "Admins can enable it with /vmconfig on"
        )
        return

    # Check for existing active session.
    existing = await _get_active_session(chat.id)
    if existing:
        await message.reply_text(
            "There is already an active vote in this chat. "
            "Wait for it to finish or an admin can use /cancelvote."
        )
        return

    # Resolve target.
    user_id, rest = await extract_user_and_text(update, context)
    if not user_id:
        await message.reply_text(
            "Specify who to mute.\n"
            "Usage: /votemute @username [duration]\n"
            "       /votemute (as reply) [duration]"
        )
        return

    if user_id == initiator.id:
        await message.reply_text("You can't vote to mute yourself.")
        return

    if user_id == context.bot.id:
        await message.reply_text("I can't mute myself.")
        return

    if await is_user_admin(chat, user_id):
        await message.reply_text("Admins cannot be vote-muted.")
        return

    # Parse optional duration from args.
    duration_seconds = settings.default_duration
    if rest:
        parsed = _parse_duration(rest.strip().split()[0])
        if parsed:
            duration_seconds = parsed

    # Fetch target user info.
    try:
        target_chat = await context.bot.get_chat(user_id)
        target_name = target_chat.first_name or str(user_id)
    except (BadRequest, Forbidden):
        target_name = str(user_id)

    threshold = settings.threshold
    # Initiator auto-votes Yes.
    yes_voters = [initiator.id]
    no_voters: list[int] = []

    # Create DB session record.
    async with get_session() as session:
        vote_session = VoteMuteSession(
            chat_id=chat.id,
            target_user_id=user_id,
            target_name=target_name,
            initiator_id=initiator.id,
            yes_votes=1,
            no_votes=0,
            yes_voter_ids=str(initiator.id),
            no_voter_ids="",
            duration_seconds=duration_seconds,
            active=True,
        )
        session.add(vote_session)
        await session.flush()
        session_id = vote_session.id
        await session.commit()

    kb = _make_keyboard(session_id, 1, 0, threshold)
    dur_txt = _fmt_duration(duration_seconds)

    panel_text = (
        f"🗳 <b>Vote to mute</b> <a href='tg://user?id={user_id}'>{target_name}</a>\n"
        f"Duration: <b>{dur_txt}</b>\n\n"
        f"🔇 Mute: <b>1/{threshold}</b>   ❌ No: <b>0/{threshold}</b>\n"
        f"<i>Vote expires in {_PANEL_TTL_SECONDS // 60} minutes.</i>"
    )

    try:
        panel = await message.reply_html(panel_text, reply_markup=kb)
    except (BadRequest, Forbidden):
        await _end_session(session_id)
        return

    # Store panel message id for later cleanup.
    async with get_session() as session:
        row = await session.get(VoteMuteSession, session_id)
        if row:
            row.panel_message_id = panel.message_id
            await session.commit()

    # Delete the /votemute command message.
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass

    # Schedule panel expiry.
    async def _expire():
        await asyncio.sleep(_PANEL_TTL_SECONDS)
        active = await _get_active_session(chat.id)
        if active and active.id == session_id:
            await _end_session(session_id)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat.id,
                    message_id=panel.message_id,
                    text=f"⏱ Vote to mute <b>{target_name}</b> expired — not enough votes.",
                    parse_mode=ParseMode.HTML,
                )
            except (BadRequest, Forbidden):
                pass
            await asyncio.sleep(8)
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=panel.message_id)
            except (BadRequest, Forbidden):
                pass

    asyncio.create_task(_expire())


# ---------------------------------------------------------------------------
# Callback — Yes / No buttons
# ---------------------------------------------------------------------------

async def vmute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes/No vote button presses."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    _, vote_type, session_id_str = parts
    session_id = int(session_id_str)
    voter_id = query.from_user.id
    chat = update.effective_chat

    async with get_session() as session:
        row = await session.get(VoteMuteSession, session_id)
        if not row or not row.active:
            await query.answer("This vote has already ended.", show_alert=True)
            return

        yes_ids = set(int(x) for x in row.yes_voter_ids.split(",") if x)
        no_ids = set(int(x) for x in row.no_voter_ids.split(",") if x)

        if voter_id in yes_ids or voter_id in no_ids:
            await query.answer("You have already voted.", show_alert=True)
            return

        if vote_type == "yes":
            yes_ids.add(voter_id)
            row.yes_votes = len(yes_ids)
            row.yes_voter_ids = ",".join(str(i) for i in yes_ids)
        else:
            no_ids.add(voter_id)
            row.no_votes = len(no_ids)
            row.no_voter_ids = ",".join(str(i) for i in no_ids)

        yes_count = row.yes_votes
        no_count = row.no_votes
        threshold = (await session.get(VoteMuteSettings, row.chat_id)).threshold
        target_id = row.target_user_id
        target_name = row.target_name
        duration = row.duration_seconds
        panel_msg_id = row.panel_message_id

        await session.commit()

    settings = await _get_settings(chat.id)
    if not settings:
        return

    dur_txt = _fmt_duration(duration)

    # Mute threshold reached.
    if yes_count >= threshold:
        await _end_session(session_id)
        until_ts = int(
            (datetime.now(timezone.utc) + timedelta(seconds=duration)).timestamp()
        )
        muted = False
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=target_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_ts,
            )
            muted = True
        except (BadRequest, Forbidden):
            pass

        result_text = (
            f"🔇 <a href='tg://user?id={target_id}'>{target_name}</a> "
            f"has been {'muted' if muted else 'restricted'} for <b>{dur_txt}</b> "
            f"by community vote ({yes_count}/{threshold} votes)."
        )
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=panel_msg_id,
                text=result_text,
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, Forbidden):
            await context.bot.send_message(
                chat_id=chat.id, text=result_text, parse_mode=ParseMode.HTML
            )
        await asyncio.sleep(10)
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=panel_msg_id)
        except (BadRequest, Forbidden):
            pass
        return

    # Cancel threshold reached.
    if no_count >= threshold:
        await _end_session(session_id)
        result_text = (
            f"✅ Vote to mute <b>{target_name}</b> was cancelled "
            f"({no_count}/{threshold} No votes)."
        )
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=panel_msg_id,
                text=result_text,
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, Forbidden):
            pass
        await asyncio.sleep(8)
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=panel_msg_id)
        except (BadRequest, Forbidden):
            pass
        return

    # Update panel.
    kb = _make_keyboard(session_id, yes_count, no_count, threshold)
    panel_text = (
        f"🗳 <b>Vote to mute</b> <a href='tg://user?id={target_id}'>{target_name}</a>\n"
        f"Duration: <b>{dur_txt}</b>\n\n"
        f"🔇 Mute: <b>{yes_count}/{threshold}</b>   ❌ No: <b>{no_count}/{threshold}</b>\n"
        f"<i>Vote expires in {_PANEL_TTL_SECONDS // 60} minutes.</i>"
    )
    try:
        await query.edit_message_text(
            text=panel_text, parse_mode=ParseMode.HTML, reply_markup=kb
        )
    except BadRequest:
        pass
    await query.answer(f"Vote recorded! ({yes_count} yes / {no_count} no)")


# ---------------------------------------------------------------------------
# /vmconfig — admin configuration
# ---------------------------------------------------------------------------

@user_admin
async def vmconfig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin configuration for the vote-mute system."""
    message = update.effective_message
    chat = update.effective_chat
    args = context.args or []

    if not args:
        settings = await _get_settings(chat.id)
        if not settings:
            await message.reply_html(
                "<b>Vote-Mute</b>\n\nStatus: ❌ Not configured\n"
                "Use /vmconfig on to enable."
            )
        else:
            state = "✅ Active" if settings.enabled else "❌ Disabled"
            await message.reply_html(
                f"<b>Vote-Mute Configuration</b>\n\n"
                f"Status: {state}\n"
                f"Threshold: <b>{settings.threshold}</b> votes\n"
                f"Default mute duration: <b>{_fmt_duration(settings.default_duration)}</b>"
            )
        return

    sub = args[0].lower()

    if sub in ("on", "off"):
        enabled = sub == "on"
        async with get_session() as session:
            row = await session.get(VoteMuteSettings, chat.id)
            if row is None:
                row = VoteMuteSettings(chat_id=chat.id, enabled=enabled)
                session.add(row)
            else:
                row.enabled = enabled
            await session.commit()
        await message.reply_text(f"Vote-mute is now {'✅ enabled' if enabled else '❌ disabled'}.")

    elif sub == "threshold" and len(args) >= 2:
        try:
            n = int(args[1])
            if n < 2 or n > 50:
                raise ValueError
        except ValueError:
            await message.reply_text("Threshold must be between 2 and 50.")
            return
        async with get_session() as session:
            row = await session.get(VoteMuteSettings, chat.id)
            if row is None:
                row = VoteMuteSettings(chat_id=chat.id, threshold=n)
                session.add(row)
            else:
                row.threshold = n
            await session.commit()
        await message.reply_text(f"✅ Vote-mute threshold set to {n}.")

    elif sub == "duration" and len(args) >= 2:
        secs = _parse_duration(args[1])
        if secs is None:
            await message.reply_text("Invalid duration. Use format like 30m, 2h, 1d.")
            return
        async with get_session() as session:
            row = await session.get(VoteMuteSettings, chat.id)
            if row is None:
                row = VoteMuteSettings(chat_id=chat.id, default_duration=secs)
                session.add(row)
            else:
                row.default_duration = secs
            await session.commit()
        await message.reply_text(f"✅ Default mute duration set to {_fmt_duration(secs)}.")

    elif sub == "status":
        settings = await _get_settings(chat.id)
        if not settings:
            await message.reply_text("Not configured. Use /vmconfig on to enable.")
        else:
            state = "✅ Active" if settings.enabled else "❌ Disabled"
            await message.reply_html(
                f"<b>Vote-Mute Configuration</b>\n\n"
                f"Status: {state}\n"
                f"Threshold: <b>{settings.threshold}</b> votes\n"
                f"Default duration: <b>{_fmt_duration(settings.default_duration)}</b>"
            )
    else:
        await message.reply_text(
            "Usage:\n"
            "/vmconfig on|off\n"
            "/vmconfig threshold <number>\n"
            "/vmconfig duration <time> (e.g. 30m, 2h, 1d)\n"
            "/vmconfig status"
        )


# ---------------------------------------------------------------------------
# /cancelvote — admin only
# ---------------------------------------------------------------------------

@user_admin
async def cancelvote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the active vote-mute session in this chat."""
    message = update.effective_message
    chat = update.effective_chat

    active = await _get_active_session(chat.id)
    if not active:
        await message.reply_text("No active vote-mute in this chat.")
        return

    await _end_session(active.id)

    if active.panel_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=active.panel_message_id,
                text=f"🚫 Vote to mute <b>{active.target_name}</b> was cancelled by an admin.",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, Forbidden):
            pass
        await asyncio.sleep(5)
        try:
            await context.bot.delete_message(
                chat_id=chat.id, message_id=active.panel_message_id
            )
        except (BadRequest, Forbidden):
            pass

    await message.reply_text(f"✅ Vote cancelled.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("votemute", votemute, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("vmconfig", vmconfig, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("cancelvote", cancelvote, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CallbackQueryHandler(vmute_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: vote_mute")
