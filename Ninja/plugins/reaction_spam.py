"""
plugins/reaction_spam.py — Reaction spam detection (TG-spam inspired).

Some spam bots never post messages but mass-react to posts to draw attention
to their profile/bio which contains the actual spam. This plugin tracks emoji
reactions per user in a sliding time window and bans them if they exceed the
configured threshold.

Commands (admins only):
  /reactionspam <N> [window_min]  — Enable; ban after N reactions in window.
  /reactionspam off               — Disable.
  /reactionspam status            — Show current config.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

from core.helpers.chat_status import is_user_admin, user_admin
from database.engine import get_session
from database.models import Chat as ChatModel
from database.models_extra import ReactionSpamSettings

logger = logging.getLogger(__name__)

# In-memory: (chat_id, user_id) -> deque of timestamps
_reaction_log: Dict[Tuple[int, int], Deque[float]] = defaultdict(
    lambda: deque(maxlen=200)
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_cfg(session, chat_id: int) -> ReactionSpamSettings:
    row = await session.get(ReactionSpamSettings, chat_id)
    if row is None:
        if not await session.get(ChatModel, chat_id):
            session.add(ChatModel(id=chat_id, title=""))
            await session.flush()
        row = ReactionSpamSettings(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


# ---------------------------------------------------------------------------
# /reactionspam command
# ---------------------------------------------------------------------------

@user_admin
async def cmd_reactionspam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    args = context.args or []

    async with get_session() as session:
        cfg = await _get_cfg(session, chat.id)

        if args and args[0].lower() == "off":
            cfg.enabled = False
            await session.commit()
            await update.message.reply_text(
                "😀 <b>Reaction Spam</b> — <b>مُعطَّل</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args or args[0].lower() == "status":
            state = "✅ مُفعَّل" if cfg.enabled else "❌ مُعطَّل"
            await update.message.reply_text(
                f"😀 <b>Reaction Spam — الحالة</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"الحالة: {state}\n"
                f"🔢 الحد: <b>{cfg.max_reactions}</b> تفاعل\n"
                f"⏳ النافذة: <b>{cfg.window_minutes}</b> دقيقة\n\n"
                f"<i>/reactionspam &lt;N&gt; [دقائق] — تفعيل</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not args[0].isdigit():
            await update.message.reply_text(
                "⚠️ <code>/reactionspam &lt;N&gt; [window_min]</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        max_r = int(args[0])
        if max_r < 3:
            await update.message.reply_text("❌ الحد يجب أن يكون ≥ 3.")
            return

        window = int(args[1]) if len(args) > 1 and args[1].isdigit() else cfg.window_minutes
        cfg.enabled = True
        cfg.max_reactions = max_r
        cfg.window_minutes = max(1, window)
        await session.commit()

    await update.message.reply_text(
        f"😀 <b>Reaction Spam</b> — <b>مُفعَّل</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔢 الحد: <b>{max_r}</b> تفاعل في <b>{window}</b> دقيقة → حظر",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Reaction handler
# ---------------------------------------------------------------------------

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction = update.message_reaction
    if not reaction:
        return

    chat = reaction.chat
    user = reaction.user
    if not user or not chat:
        return

    # Skip admins
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        return

    async with get_session() as session:
        cfg = await session.get(ReactionSpamSettings, chat.id)
        if not cfg or not cfg.enabled:
            return
        max_r = cfg.max_reactions
        window_sec = cfg.window_minutes * 60

    now = time.time()
    key = (chat.id, user.id)
    dq = _reaction_log[key]

    # Expire old entries
    while dq and now - dq[0] > window_sec:
        dq.popleft()

    # Count new reactions added (each reaction object may have multiple)
    new_count = len(reaction.new_reaction) if reaction.new_reaction else 0
    for _ in range(max(1, new_count)):
        dq.append(now)

    if len(dq) >= max_r:
        mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
        try:
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat.id,
                f"😀🚫 <b>Reaction Spam!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{mention} محظور — أرسل <b>{len(dq)}</b> تفاعل في وقت قصير.\n"
                f"<i>هذا سلوك بوتات السبام التي تضغط ردود فعل لجذب الانتباه لـ bio.</i>",
                parse_mode=ParseMode.HTML,
            )
            _reaction_log[key].clear()
            logger.info("reaction_spam: banned user %d in chat %d (%d reactions)", user.id, chat.id, len(dq))
        except (BadRequest, TelegramError) as e:
            logger.warning("reaction_spam: ban failed for %d: %s", user.id, e)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        CommandHandler("reactionspam", cmd_reactionspam, filters=filters.ChatType.GROUPS)
    )
    # PTB v20.7 doesn't have MessageReactionHandler — use TypeHandler to catch
    # Update objects that carry a message_reaction field.
    application.add_handler(
        TypeHandler(type=Update, callback=handle_reaction, block=False)
    )
    logger.info("Plugin loaded: reaction_spam")
