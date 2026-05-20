"""
plugins/bayes_filter.py — Bayesian AI spam filter (Task 6).

Phase 1 of the three-phase middleware pipeline.  Runs at handler group 0,
before CAPTCHA (Phase 2) and all permission handlers (Phase 3).

Coexistence principle
---------------------
This filter operates independently of the traditional regex/word-list filter
(plugins/filters.py).  Both can be active simultaneously.  Neither replaces
the other.  The Bayesian filter runs first (group 0); the regex filter runs
later (group 10).

Classification flow (per message)
----------------------------------
1. Look up ChatFeatureSettings for the group.
2. If bayes_filter_enabled is False → pass through.
3. Skip messages from admins, the bot owner, sudo users, and bots.
4. Load per-chat corpus totals (bayes_ham_count, bayes_spam_count).
5. If corpus is below BAYES_MIN_CORPUS_SIZE → abstain (pass through).
6. Call core.spam_bayes.classify() to get P(spam).
7. If P(spam) >= threshold → apply bayes_spam_action.

Spam actions (SpamAction enum)
-------------------------------
DELETE          Delete the message; notify the chat briefly.
DELETE_WARN     Delete + store a WarnEntry + notify.
DELETE_MUTE     Delete + restrict the user (no messages for 24 h) + notify.
DELETE_BAN      Delete + ban the user permanently + notify.

Training commands
-----------------
/train spam     (reply to a message) — label as spam.
/train ham      (reply to a message) — label as ham (not spam).

Management commands
-------------------
/bayes on|off   — Toggle Bayesian filter for this group.
/bayesstat      — Show corpus size and current settings.
/bayesaction <delete|delete_warn|delete_mute|delete_ban>
                — Set the action taken on spam detection.
/bayesthreshold <0.50–0.99>
                — Override the global spam probability threshold per-chat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import ChatPermissions, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from config import settings as cfg
from core import spam_bayes
from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import (
    Chat,
    ChatFeatureSettings,
    SpamAction,
    User,
    WarnEntry,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_feat(
    chat_id: int, session: "AsyncSession"
) -> Optional[ChatFeatureSettings]:
    result = await session.execute(
        select(ChatFeatureSettings).where(
            ChatFeatureSettings.chat_id == chat_id
        )
    )
    return result.scalar_one_or_none()


async def _upsert_chat(
    tg_chat: "telegram.Chat",
    session: "AsyncSession",
) -> Chat:
    result = await session.execute(
        select(Chat).where(Chat.id == tg_chat.id)
    )
    chat: Optional[Chat] = result.scalar_one_or_none()
    if chat is None:
        chat = Chat(
            id=tg_chat.id,
            title=tg_chat.title,
            username=tg_chat.username,
        )
        session.add(chat)
    return chat


def _effective_threshold(feat: ChatFeatureSettings) -> float:
    """Return the spam probability threshold active for this chat."""
    if feat.bayes_spam_threshold_override is not None:
        return feat.bayes_spam_threshold_override
    return config.BAYES_SPAM_THRESHOLD


def _is_privileged(user_id: int) -> bool:
    """Return True if the user should be exempt from spam filtering."""
    return (
        user_id == cfg.OWNER_ID
        or user_id in cfg.SUDO_USERS
        or user_id in cfg.SUPPORT_USERS
    )


async def _apply_spam_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: SpamAction,
    chat_id: int,
    user_id: int,
    p_spam: float,
    session: "AsyncSession",
) -> None:
    """
    Execute the configured spam action for a detected spam message.

    The message triggering the action is deleted first in all cases.
    Subsequent steps depend on *action*.
    """
    msg = update.effective_message
    user = update.effective_user
    mention = user.mention_html() if user else f"User {user_id}"

    # Always delete the spam message
    try:
        await msg.delete()
    except Exception as exc:
        log.warning(
            "Bayes: could not delete spam msg in chat=%d: %s", chat_id, exc
        )

    if action == SpamAction.DELETE:
        try:
            notice = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🤖 Deleted a likely spam message from {mention}.\n"
                    f"<i>Confidence: {p_spam:.0%}</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == SpamAction.DELETE_WARN:
        # Record a warn entry and notify
        warn = WarnEntry(
            chat_id=chat_id,
            user_id=user_id,
            reason="Bayesian spam filter",
            triggered_by="bayes",
        )
        session.add(warn)
        await session.commit()

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ {mention} received a warning for suspected spam.\n"
                    f"<i>Confidence: {p_spam:.0%}</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == SpamAction.DELETE_MUTE:
        mute_until = datetime.now(tz=timezone.utc) + timedelta(hours=24)
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
                ),
                until_date=mute_until,
            )
        except Exception as exc:
            log.warning(
                "Bayes: could not mute user %d in chat %d: %s",
                user_id, chat_id, exc,
            )

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔇 {mention} has been muted for 24 h due to suspected spam.\n"
                    f"<i>Confidence: {p_spam:.0%}</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == SpamAction.DELETE_BAN:
        try:
            await context.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
            )
        except Exception as exc:
            log.warning(
                "Bayes: could not ban user %d in chat %d: %s",
                user_id, chat_id, exc,
            )

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🚫 {mention} has been banned due to suspected spam.\n"
                    f"<i>Confidence: {p_spam:.0%}</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    log.info(
        "Bayes action=%s chat=%d user=%d p_spam=%.4f",
        action.value, chat_id, user_id, p_spam,
    )


# ---------------------------------------------------------------------------
# Phase 1 handler — classifies every group message
# ---------------------------------------------------------------------------

async def bayes_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Evaluate incoming group messages against the per-chat Bayesian classifier.

    Registered at group=0 so it runs before all other content handlers.
    Passes through silently when the filter is disabled, the corpus is too
    small, or the user is privileged.
    """
    if (
        update.effective_message is None
        or update.effective_user is None
        or update.effective_chat is None
    ):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Exempt bots and privileged users
    if update.effective_user.is_bot:
        return
    if _is_privileged(user_id):
        return

    text = update.effective_message.text or update.effective_message.caption
    if not text:
        return

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None or not feat.bayes_filter_enabled:
            return

        threshold = _effective_threshold(feat)
        p_spam = await spam_bayes.classify(
            chat_id=chat_id,
            text=text,
            total_ham=feat.bayes_ham_count,
            total_spam=feat.bayes_spam_count,
            threshold=threshold,
            min_corpus_size=config.BAYES_MIN_CORPUS_SIZE,
            session=session,
        )

        if p_spam is None or p_spam < threshold:
            return

        # Spam detected — apply configured action
        await _apply_spam_action(
            update=update,
            context=context,
            action=feat.bayes_spam_action,
            chat_id=chat_id,
            user_id=user_id,
            p_spam=p_spam,
            session=session,
        )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

@user_admin
async def cmd_bayes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /bayes on|off — Toggle the Bayesian spam filter for this group.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /bayes <on|off>")
        return

    enabled = args[0].lower() == "on"
    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.bayes_filter_enabled = enabled
        await session.commit()

    state = "enabled ✅" if enabled else "disabled ✗"
    await update.effective_message.reply_html(
        f"Bayesian spam filter is now <b>{state}</b>."
    )


@user_admin
async def cmd_train(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /train <spam|ham> — Train the Bayesian classifier using a replied-to message.

    Must be used as a reply.  The replied message's text is extracted and
    added to the corpus with the given label.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    if not args or args[0].lower() not in ("spam", "ham"):
        await update.effective_message.reply_text(
            "Usage: Reply to a message and use /train <spam|ham>"
        )
        return

    is_spam = args[0].lower() == "spam"
    reply = update.effective_message.reply_to_message

    if reply is None:
        await update.effective_message.reply_text(
            "Please reply to the message you want to train on."
        )
        return

    text = reply.text or reply.caption
    if not text:
        await update.effective_message.reply_text(
            "The replied-to message has no text or caption to train on."
        )
        return

    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)
            await session.flush()

        token_count = await spam_bayes.train(
            chat_id=chat_id,
            text=text,
            is_spam=is_spam,
            session=session,
        )

    label = "spam 🔴" if is_spam else "ham 🟢"
    await update.effective_message.reply_html(
        f"✅ Trained as <b>{label}</b> — {token_count} unique tokens processed."
    )


@user_admin
async def cmd_bayesstat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /bayesstat — Display the current Bayesian filter status for this group.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)

    if feat is None:
        await update.effective_message.reply_text(
            "No feature settings found for this group. "
            "Send a message or run /bayes on to initialise."
        )
        return

    threshold = _effective_threshold(feat)
    corpus = feat.bayes_ham_count + feat.bayes_spam_count
    min_corpus = config.BAYES_MIN_CORPUS_SIZE

    enabled_label = "✅ Enabled" if feat.bayes_filter_enabled else "✗ Disabled"
    ready_label = (
        "✅ Active"
        if corpus >= min_corpus
        else f"⏳ Needs {min_corpus - corpus} more samples"
    )

    text = (
        f"<b>Bayesian Spam Filter — {update.effective_chat.title}</b>\n\n"
        f"Status: {enabled_label}\n"
        f"Classifier: {ready_label}\n"
        f"Ham samples: {feat.bayes_ham_count:,}\n"
        f"Spam samples: {feat.bayes_spam_count:,}\n"
        f"Threshold: {threshold:.0%}"
        f"{'  <i>(per-chat override)</i>' if feat.bayes_spam_threshold_override is not None else ''}\n"
        f"Action: <b>{feat.bayes_spam_action.value}</b>\n\n"
        f"<i>Use /train spam or /train ham (as a reply) to add training data.</i>"
    )
    await update.effective_message.reply_html(text)


@user_admin
async def cmd_bayesaction(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /bayesaction <delete|delete_warn|delete_mute|delete_ban>
    Set the action taken when a message is classified as spam.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    valid = {a.value for a in SpamAction}
    args = context.args or []
    if not args or args[0].lower() not in valid:
        await update.effective_message.reply_text(
            f"Usage: /bayesaction <{'|'.join(sorted(valid))}>"
        )
        return

    action = SpamAction(args[0].lower())
    chat_id = update.effective_chat.id

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.bayes_spam_action = action
        await session.commit()

    await update.effective_message.reply_html(
        f"Bayesian spam action set to <b>{action.value}</b>."
    )


@user_admin
async def cmd_bayesthreshold(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /bayesthreshold <0.50–0.99|default>
    Override the global spam probability threshold for this chat.
    """
    if update.effective_message is None or update.effective_chat is None:
        return

    args = context.args or []
    chat_id = update.effective_chat.id

    if args and args[0].lower() == "default":
        override: Optional[float] = None
        label = f"reset to global default ({config.BAYES_SPAM_THRESHOLD:.0%})"
    elif args:
        try:
            value = float(args[0])
        except ValueError:
            await update.effective_message.reply_text(
                "Usage: /bayesthreshold <0.50–0.99|default>"
            )
            return
        if not 0.50 <= value <= 0.99:
            await update.effective_message.reply_text(
                "Threshold must be between 0.50 and 0.99."
            )
            return
        override = value
        label = f"set to <b>{value:.0%}</b>"
    else:
        await update.effective_message.reply_text(
            "Usage: /bayesthreshold <0.50–0.99|default>"
        )
        return

    async with get_session() as session:
        feat = await _get_feat(chat_id, session)
        if feat is None:
            await _upsert_chat(update.effective_chat, session)
            feat = ChatFeatureSettings(chat_id=chat_id)
            session.add(feat)

        feat.bayes_spam_threshold_override = override
        await session.commit()

    await update.effective_message.reply_html(
        f"Bayesian spam threshold {label}."
    )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register all Bayesian filter handlers with the PTB Application."""

    # Phase 1 — message classifier (group 0, runs before all other handlers)
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION)
            & filters.ChatType.GROUPS
            & ~filters.COMMAND,
            bayes_message_handler,
        ),
        group=0,
    )

    # Admin commands
    application.add_handler(CommandHandler("bayes", cmd_bayes))
    application.add_handler(CommandHandler("train", cmd_train))
    application.add_handler(CommandHandler("bayesstat", cmd_bayesstat))
    application.add_handler(CommandHandler("bayesaction", cmd_bayesaction))
    application.add_handler(CommandHandler("bayesthreshold", cmd_bayesthreshold))

    log.info("Bayes filter plugin registered.")
