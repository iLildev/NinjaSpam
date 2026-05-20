"""
core/helpers/chat_status.py — Async permission helpers and handler decorators.

Provides:
- Pure async helpers: is_user_admin, is_bot_admin, is_user_ban_protected,
  is_user_in_chat, can_delete
- Async handler decorators: user_admin, user_admin_no_reply, bot_admin,
  can_restrict, can_promote, can_pin, user_not_admin

All decorators follow the PTB v20 signature:
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE)

They are designed to be stacked — the outermost (topmost in source) runs first.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional

from cachetools import TTLCache
from telegram import Chat, ChatMember, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Admin status TTL cache — avoids repeated get_chat_member() API calls.
# Key: (chat_id, user_id) → bool (is admin)
# TTL: 5 minutes.  Invalidated on /promote and /demote via invalidate_admin_cache().
# Max 2048 entries — covers ~100 groups × ~20 admins with room to spare.
# ---------------------------------------------------------------------------
_admin_cache: TTLCache = TTLCache(maxsize=2048, ttl=300)


def invalidate_admin_cache(chat_id: int, user_id: int) -> None:
    """
    Remove the cached admin status for a specific (chat, user) pair.

    Must be called after /promote and /demote so that subsequent checks
    reflect the new status without waiting for the TTL to expire.
    """
    _admin_cache.pop((chat_id, user_id), None)


# ---------------------------------------------------------------------------
# Pure async helpers
# ---------------------------------------------------------------------------

async def is_user_admin(chat: Chat, user_id: int) -> bool:
    """
    Return True if ``user_id`` is an administrator or creator in ``chat``.

    Results are cached for 5 minutes per (chat_id, user_id) pair to avoid
    hammering the Telegram API on every message in high-traffic groups.
    The cache is invalidated automatically on /promote and /demote.
    """
    if chat.type == Chat.PRIVATE:
        return True  # All users are their own admin in private chats.

    cache_key = (chat.id, user_id)
    cached = _admin_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        member: ChatMember = await chat.get_member(user_id)
        result = member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
        _admin_cache[cache_key] = result
        return result
    except BadRequest:
        return False


async def is_bot_admin(chat: Chat, bot_id: int) -> bool:
    """Return True if the bot itself is an administrator or creator in ``chat``."""
    try:
        member: ChatMember = await chat.get_member(bot_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except BadRequest:
        return False


async def is_user_ban_protected(chat: Chat, user_id: int) -> bool:
    """
    Return True when ``user_id`` should be immune to bans and kicks.

    A user is protected if they are an administrator, the group creator, or
    the bot itself.  This prevents moderators from accidentally banning other
    admins through the bot.
    """
    try:
        member: ChatMember = await chat.get_member(user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except BadRequest:
        return False


async def is_user_in_chat(chat: Chat, user_id: int) -> bool:
    """
    Return True if ``user_id`` is currently a member of ``chat``
    (i.e. status is not 'left' or 'kicked').
    """
    try:
        member: ChatMember = await chat.get_member(user_id)
        return member.status not in (ChatMember.LEFT, ChatMember.BANNED)
    except BadRequest:
        return False


async def can_delete(chat: Chat, bot_id: int) -> bool:
    """Return True if the bot has ``can_delete_messages`` permission in ``chat``."""
    try:
        member: ChatMember = await chat.get_member(bot_id)
        if member.status == ChatMember.OWNER:
            return True
        return bool(getattr(member, "can_delete_messages", False))
    except BadRequest:
        return False


async def _get_bot_member(chat: Chat, bot_id: int) -> Optional[ChatMember]:
    """Return the bot's ChatMember object, or None on failure."""
    try:
        return await chat.get_member(bot_id)
    except BadRequest:
        return None


# ---------------------------------------------------------------------------
# Handler decorators
# ---------------------------------------------------------------------------

def user_admin(func: Callable) -> Callable:
    """
    Decorator: ensures the calling user is a group admin before running the
    handler.  Sends an error reply and returns if the check fails.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if not await is_user_admin(chat, user.id):
            await update.effective_message.reply_text(
                "This command is restricted to group administrators."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def user_admin_no_reply(func: Callable) -> Callable:
    """
    Like ``user_admin`` but silently returns (no message) when the caller is
    not an admin.  Used for callback query handlers where a reply is unwanted.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if not await is_user_admin(chat, user.id):
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def user_not_admin(func: Callable) -> Callable:
    """
    Decorator: runs the handler only when the calling user is NOT an admin.
    Used for enforcement handlers (blacklist, lock deletion, flood check)
    that should skip admins entirely.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return
        if await is_user_admin(chat, user.id):
            return  # Silently skip for admins.
        return await func(update, context, *args, **kwargs)

    return wrapper


def bot_admin(func: Callable) -> Callable:
    """
    Decorator: ensures the bot is an administrator in the group before running
    the handler.  Without admin status many moderation actions are impossible.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        chat = update.effective_chat
        if not chat:
            return
        if not await is_bot_admin(chat, context.bot.id):
            await update.effective_message.reply_text(
                "I need to be an administrator to use this command."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def can_restrict(func: Callable) -> Callable:
    """
    Decorator: verifies the bot has ``can_restrict_members`` permission before
    executing the handler.  Required for ban, kick, mute operations.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        chat = update.effective_chat
        if not chat:
            return
        bot_member = await _get_bot_member(chat, context.bot.id)
        if not bot_member:
            return
        if bot_member.status == ChatMember.OWNER:
            return await func(update, context, *args, **kwargs)
        if not getattr(bot_member, "can_restrict_members", False):
            await update.effective_message.reply_text(
                "I need the 'Restrict Members' permission to do that."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def can_promote(func: Callable) -> Callable:
    """
    Decorator: verifies the bot has ``can_promote_members`` permission.
    Required for /promote and /demote.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        chat = update.effective_chat
        if not chat:
            return
        bot_member = await _get_bot_member(chat, context.bot.id)
        if not bot_member:
            return
        if bot_member.status == ChatMember.OWNER:
            return await func(update, context, *args, **kwargs)
        if not getattr(bot_member, "can_promote_members", False):
            await update.effective_message.reply_text(
                "I need the 'Add New Admins' permission to do that."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def can_pin(func: Callable) -> Callable:
    """
    Decorator: verifies the bot has ``can_pin_messages`` permission.
    Required for /pin and /unpin.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        chat = update.effective_chat
        if not chat:
            return
        bot_member = await _get_bot_member(chat, context.bot.id)
        if not bot_member:
            return
        if bot_member.status == ChatMember.OWNER:
            return await func(update, context, *args, **kwargs)
        if not getattr(bot_member, "can_pin_messages", False):
            await update.effective_message.reply_text(
                "I need the 'Pin Messages' permission to do that."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper
