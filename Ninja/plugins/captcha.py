"""
plugins/captcha.py — Shieldy-inspired CAPTCHA verification middleware (Task 5).

Phase 2 of the three-phase middleware pipeline.  Runs after the Bayesian spam
filter (Phase 1) and before permission checks (Phase 3).

Supported CAPTCHA types
-----------------------
BUTTON  — An inline keyboard button the user must press to confirm they are
           human.  The callback data encodes the expected (chat_id, user_id)
           pair so the handler can validate it without a DB lookup.
MATH    — A simple arithmetic challenge (e.g. "What is 4 + 7?").  The
           expected answer is stored in CaptchaPending.expected_answer.
TEXT    — The user must type a randomly chosen English word exactly.  The
           expected word is stored in CaptchaPending.expected_answer.

Enrollment flow
---------------
1. StatusUpdate.NEW_CHAT_MEMBERS fires when a user joins.
2. For each new member (bots excluded):
   a. Look up ChatFeatureSettings.captcha_enabled for the chat.
   b. If disabled → do nothing.
   c. Optionally mute the user (captcha_mute_until_verified).
   d. Generate the appropriate challenge and send it to the chat.
   e. Persist a CaptchaPending row and schedule a timeout job.
3. On success (button press / correct answer):
   a. Delete the challenge message.
   b. Lift any mute applied at join.
   c. Delete the CaptchaPending row.
   d. Send a brief confirmation.
4. On timeout:
   a. If captcha_kick_on_timeout → kick the user.
   b. Delete the challenge message.
   c. Delete the CaptchaPending row.

Commands
--------
/captcha on|off          — Toggle CAPTCHA for the group.
/setcaptcha button|math|text — Change the CAPTCHA type.
/captchatime <seconds>   — Set per-chat timeout (overrides global default).
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings as cfg
from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import (
    CaptchaPending,
    CaptchaType,
    Chat,
    ChatFeatureSettings,
    User,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERIFY_PREFIX = "captcha_verify"
_TEXT_WORDS: tuple[str, ...] = (
    "WELCOME", "ACCEPT", "CONFIRM", "VERIFIED", "HUMAN", "MEMBER",
    "PROCEED", "APPROVE", "GRANTED", "JOINED",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _timeout_for(feat: ChatFeatureSettings) -> int:
    """Return the effective CAPTCHA timeout in seconds for this chat."""
    if feat.captcha_timeout_override is not None:
        return feat.captcha_timeout_override
    return cfg.CAPTCHA_TIMEOUT_SECONDS


def _make_math_challenge() -> tuple[str, str]:
    """
    Generate a simple arithmetic challenge.

    Returns:
        (question_text, expected_answer) both as strings.
    """
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    op = random.choice(["+", "-", "*"])
    if op == "+":
        answer = a + b
    elif op == "-":
        answer = a - b
    else:
        answer = a * b
    question = f"What is {a} {op} {b}?"
    return question, str(answer)


def _make_text_challenge() -> tuple[str, str]:
    """
    Choose a random verification word.

    Returns:
        (question_text, expected_answer) both as strings.
    """
    word = random.choice(_TEXT_WORDS)
    question = f'Type the word <b>{word}</b> exactly to verify:'
    return question, word


def _build_button_markup(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a single verification button."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="✅ I am not a robot",
            callback_data=f"{_VERIFY_PREFIX}:{chat_id}:{user_id}",
        )
    ]])


async def _get_or_create_user(
    tg_user: "telegram.User",
    session: "AsyncSession",
) -> User:
    """Upsert a User row and return it."""
    from sqlalchemy import select

    existing = await session.execute(
        select(User).where(User.id == tg_user.id)
    )
    user: Optional[User] = existing.scalar_one_or_none()
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name,
            is_bot=tg_user.is_bot,
        )
        session.add(user)
    return user


async def _upsert_chat(
    tg_chat: "telegram.Chat",
    session: "AsyncSession",
) -> Chat:
    """Upsert a Chat row and return it."""
    existing = await session.execute(
        select(Chat).where(Chat.id == tg_chat.id)
    )
    chat: Optional[Chat] = existing.scalar_one_or_none()
    if chat is None:
        chat = Chat(id=tg_chat.id, title=tg_chat.title, username=tg_chat.username)
        session.add(chat)
    return chat


async def _get_feat(
    chat_id: int, session: "AsyncSession"
) -> Optional[ChatFeatureSettings]:
    result = await session.execute(
        select(ChatFeatureSettings).where(ChatFeatureSettings.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


async def _remove_pending(
    chat_id: int, user_id: int, session: "AsyncSession"
) -> Optional[int]:
    """
    Delete the CaptchaPending row for (chat_id, user_id).

    Returns:
        The challenge_message_id stored in the row, or None if no row existed.
    """
    result = await session.execute(
        select(CaptchaPending).where(
            CaptchaPending.chat_id == chat_id,
            CaptchaPending.user_id == user_id,
        )
    )
    row: Optional[CaptchaPending] = result.scalar_one_or_none()
    if row is None:
        return None
    msg_id = row.challenge_message_id
    await session.delete(row)
    return msg_id


async def _cancel_timeout_job(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> None:
    """Cancel a previously scheduled captcha timeout job if it still exists."""
    job_name = f"captcha_expire_{chat_id}_{user_id}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()


# ---------------------------------------------------------------------------
# Timeout job (fired by PTB JobQueue)
# ---------------------------------------------------------------------------

async def _captcha_expire_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    PTB job callback — fires when a new member's CAPTCHA window expires.

    If the user has not yet verified, they are kicked (if configured) and
    the challenge message is deleted.
    """
    data: dict = context.job.data  # type: ignore[union-attr]
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]
    kick: bool = data["kick"]
    mention: str = data.get("mention", "User")

    async with get_session() as session:
        msg_id = await _remove_pending(chat_id, user_id, session)

    if msg_id is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

        if kick:
            try:
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(
                    chat_id=chat_id, user_id=user_id, only_if_banned=True
                )
            except Exception as exc:
                log.warning(
                    "Captcha: failed to kick user %d from chat %d: %s",
                    user_id, chat_id, exc,
                )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{mention} failed the verification and has been "
                    f"{'kicked' if kick else 'left unverified'}."
                ),
            )
        except Exception:
            pass

        log.info(
            "Captcha expired: chat=%d user=%d kick=%s", chat_id, user_id, kick
        )


# ---------------------------------------------------------------------------
# New member handler — issues the challenge
# ---------------------------------------------------------------------------

async def new_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle StatusUpdate.NEW_CHAT_MEMBERS — issue CAPTCHA challenges.

    Skips:
    - Chats with captcha_enabled=False.
    - Bot accounts joining the group.
    - The bot itself joining the group.
    """
    if update.message is None or update.effective_chat is None:
        return

    chat = update.effective_chat
    bot_id: int = context.bot.id

    async with get_session() as session:
        feat = await _get_feat(chat.id, session)

    if feat is None or not feat.captcha_enabled:
        return

    for tg_user in update.message.new_chat_members:
        if tg_user.is_bot:
            continue
        if tg_user.id == bot_id:
            continue

        await _issue_challenge(
            update=update,
            context=context,
            tg_user=tg_user,
            feat=feat,
            chat=chat,
        )


async def _issue_challenge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tg_user: "telegram.User",
    feat: ChatFeatureSettings,
    chat: "telegram.Chat",
) -> None:
    """Issue a CAPTCHA challenge to *tg_user* in *chat*."""
    chat_id = chat.id
    user_id = tg_user.id
    mention = tg_user.mention_html()
    timeout = _timeout_for(feat)
    captcha_type: CaptchaType = feat.captcha_type
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=timeout)

    # Optionally mute until verified
    if feat.captcha_mute_until_verified:
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_audios=False,
                    can_send_documents=False,
                    can_send_photos=False,
                    can_send_videos=False,
                    can_send_video_notes=False,
                    can_send_voice_notes=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_invite_users=False,
                ),
            )
        except Exception as exc:
            log.warning(
                "Captcha: could not mute user %d in chat %d: %s",
                user_id, chat_id, exc,
            )

    # Build challenge
    expected_answer: Optional[str] = None
    if captcha_type == CaptchaType.BUTTON:
        text = (
            f"👋 Welcome, {mention}!\n\n"
            f"Please press the button below within {timeout} seconds to verify "
            f"that you are human.\n\n"
            f"Failure to verify will result in "
            f"{'removal' if feat.captcha_kick_on_timeout else 'mute'}."
        )
        reply_markup = _build_button_markup(chat_id, user_id)
        msg = await update.message.reply_html(text, reply_markup=reply_markup)
    elif captcha_type == CaptchaType.MATH:
        question, expected_answer = _make_math_challenge()
        text = (
            f"👋 Welcome, {mention}!\n\n"
            f"To verify you are human, reply with the answer to this question "
            f"within {timeout} seconds:\n\n"
            f"<b>{question}</b>"
        )
        msg = await update.message.reply_html(text)
    else:  # CaptchaType.TEXT
        question, expected_answer = _make_text_challenge()
        text = (
            f"👋 Welcome, {mention}!\n\n"
            f"To verify you are human, reply to this message within "
            f"{timeout} seconds.\n\n{question}"
        )
        msg = await update.message.reply_html(text)

    challenge_msg_id: Optional[int] = msg.message_id if msg else None

    # Persist CaptchaPending row (upsert to handle re-joins)
    async with get_session() as session:
        await _get_or_create_user(tg_user, session)
        await _upsert_chat(chat, session)

        result = await session.execute(
            select(CaptchaPending).where(
                CaptchaPending.chat_id == chat_id,
                CaptchaPending.user_id == user_id,
            )
        )
        existing: Optional[CaptchaPending] = result.scalar_one_or_none()
        if existing is not None:
            await session.delete(existing)
            await session.flush()

        pending = CaptchaPending(
            chat_id=chat_id,
            user_id=user_id,
            challenge_message_id=challenge_msg_id,
            captcha_type=captcha_type,
            expected_answer=expected_answer,
            expires_at=expires_at,
        )
        session.add(pending)
        await session.commit()

    # Cancel any prior job for this user and schedule a new one
    await _cancel_timeout_job(context, chat_id, user_id)
    job_name = f"captcha_expire_{chat_id}_{user_id}"
    context.job_queue.run_once(
        _captcha_expire_job,
        when=timeout,
        data={
            "chat_id": chat_id,
            "user_id": user_id,
            "kick": feat.captcha_kick_on_timeout,
            "mention": mention,
        },
        name=job_name,
        chat_id=chat_id,
        user_id=user_id,
    )

    log.info(
        "Captcha issued: type=%s chat=%d user=%d timeout=%ds",
        captcha_type.value, chat_id, user_id, timeout,
    )


# ---------------------------------------------------------------------------
# BUTTON verification: callback query handler
# ---------------------------------------------------------------------------

async def button_verify_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle the inline button press for BUTTON-type CAPTCHAs.

    Callback data format: ``captcha_verify:<chat_id>:<user_id>``

    The handler validates that:
    - The pressing user's ID matches the expected user_id in the callback data.
    - There is still an open CaptchaPending row for the pair.
    """
    query = update.callback_query
    if query is None or query.from_user is None or query.message is None:
        return

    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return

    try:
        expected_chat_id = int(parts[1])
        expected_user_id = int(parts[2])
    except ValueError:
        return

    pressing_user_id = query.from_user.id

    # Only allow the intended user to press the button
    if pressing_user_id != expected_user_id:
        await query.answer("This button is not for you.", show_alert=True)
        return

    chat_id = query.message.chat.id
    if chat_id != expected_chat_id:
        return

    await _complete_verification(
        bot=context.bot,
        context=context,
        chat_id=chat_id,
        user_id=expected_user_id,
        tg_user=query.from_user,
        challenge_message_id=query.message.message_id,
        via_callback=True,
    )


# ---------------------------------------------------------------------------
# MATH / TEXT verification: message handler
# ---------------------------------------------------------------------------

async def answer_check_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle text messages from pending CAPTCHA users.

    Runs at handler group 4 (Phase 2).  Only matches group text messages from
    users who have an open CaptchaPending row with a non-null expected_answer.
    Other messages are ignored and pass through to later handlers.
    """
    if (
        update.effective_message is None
        or update.effective_user is None
        or update.effective_chat is None
    ):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = (update.effective_message.text or "").strip()

    async with get_session() as session:
        result = await session.execute(
            select(CaptchaPending).where(
                CaptchaPending.chat_id == chat_id,
                CaptchaPending.user_id == user_id,
                CaptchaPending.expected_answer.isnot(None),
            )
        )
        pending: Optional[CaptchaPending] = result.scalar_one_or_none()

    if pending is None:
        return  # User is not awaiting a MATH/TEXT captcha — pass through

    # Check if the answer is correct
    if text == pending.expected_answer:
        await _complete_verification(
            bot=context.bot,
            context=context,
            chat_id=chat_id,
            user_id=user_id,
            tg_user=update.effective_user,
            challenge_message_id=pending.challenge_message_id,
            via_callback=False,
        )
        # Delete the user's answer message to keep the chat clean
        try:
            await update.effective_message.delete()
        except Exception:
            pass
    else:
        try:
            await update.effective_message.reply_text(
                "❌ Incorrect answer. Please try again."
            )
        except Exception:
            pass


async def _complete_verification(
    bot: "telegram.Bot",
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    tg_user: "telegram.User",
    challenge_message_id: Optional[int],
    via_callback: bool,
) -> None:
    """
    Finalise a successful CAPTCHA verification.

    Steps:
    1. Cancel the timeout job.
    2. Delete the CaptchaPending row.
    3. Lift any mute applied at join time.
    4. Delete the challenge message.
    5. Send a brief success notification.
    """
    await _cancel_timeout_job(context, chat_id, user_id)

    async with get_session() as session:
        msg_id = await _remove_pending(chat_id, user_id, session)

    effective_msg_id = challenge_message_id or msg_id

    # Restore permissions
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
            ),
        )
    except Exception as exc:
        log.warning(
            "Captcha: could not lift mute for user %d in chat %d: %s",
            user_id, chat_id, exc,
        )

    # Delete challenge message
    if effective_msg_id is not None:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=effective_msg_id)
        except Exception:
            pass

    # Send brief confirmation
    mention = tg_user.mention_html()
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ {mention} has been verified.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    log.info(
        "Captcha verified: chat=%d user=%d via_callback=%s",
        chat_id, user_id, via_callback,
    )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /captcha on|off — Toggle CAPTCHA for this group.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text(
            "Usage: /captcha <on|off>"
        )
        return

    enabled = args[0].lower() == "on"
    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.captcha_enabled = enabled
        await session.commit()

    state = "enabled ✅" if enabled else "disabled ✗"
    await update.effective_message.reply_text(
        f"CAPTCHA verification is now <b>{state}</b>.", parse_mode="HTML"
    )


@user_admin
async def cmd_setcaptcha(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /setcaptcha button|math|text — Set the CAPTCHA type for this group.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    valid = {t.value for t in CaptchaType}
    if not args or args[0].lower() not in valid:
        await update.effective_message.reply_text(
            f"Usage: /setcaptcha <{'|'.join(sorted(valid))}>"
        )
        return

    captcha_type = CaptchaType(args[0].lower())
    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.captcha_type = captcha_type
        await session.commit()

    await update.effective_message.reply_text(
        f"CAPTCHA type set to <b>{captcha_type.value}</b>.", parse_mode="HTML"
    )


@user_admin
async def cmd_captchatime(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /captchatime <seconds> — Set the per-chat CAPTCHA timeout.

    Accepts values from 30 to 3600 seconds.  Pass "default" to revert to the
    global configuration value.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    chat_id = update.effective_chat.id

    if args and args[0].lower() == "default":
        timeout: Optional[int] = None
        label = f"reset to global default ({cfg.CAPTCHA_TIMEOUT_SECONDS}s)"
    elif args and args[0].isdigit():
        value = int(args[0])
        if not 30 <= value <= 3600:
            await update.effective_message.reply_text(
                "Timeout must be between 30 and 3600 seconds."
            )
            return
        timeout = value
        label = f"set to <b>{timeout}s</b>"
    else:
        await update.effective_message.reply_text(
            "Usage: /captchatime <seconds|default>"
        )
        return

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.captcha_timeout_override = timeout
        await session.commit()

    await update.effective_message.reply_html(f"CAPTCHA timeout {label}.")


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all CAPTCHA handlers and commands with the PTB Application."""

    # Phase 2 — new member challenge (group 5, after Bayes Phase 1 at group 0)
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.ChatType.GROUPS,
            new_member_handler,
        ),
        group=5,
    )

    # Phase 2 — MATH/TEXT answer checking (group 4, before content enforcement)
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
            answer_check_handler,
        ),
        group=4,
    )

    # BUTTON verification callback
    application.add_handler(
        CallbackQueryHandler(
            button_verify_handler,
            pattern=rf"^{_VERIFY_PREFIX}:",
        )
    )

    # Admin commands
    application.add_handler(CommandHandler("captcha", cmd_captcha))
    application.add_handler(CommandHandler("setcaptcha", cmd_setcaptcha))
    application.add_handler(CommandHandler("captchatime", cmd_captchatime))

    log.info("Captcha plugin registered.")
