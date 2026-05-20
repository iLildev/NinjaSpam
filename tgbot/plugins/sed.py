"""
plugins/sed.py — Sed/regex text substitution on replied messages.

Usage (reply to any text message):
    s/old/new/       — replace first occurrence of 'old' with 'new'
    s/old/new/g      — replace all occurrences
    s/old/new/i      — case-insensitive replace (first)
    s/old/new/gi     — case-insensitive replace (all)

Supported delimiters: / : | _

This mirrors Marie's sed module, ported to python-telegram-bot v20 async.
"""

from __future__ import annotations

import logging
import re
import sre_constants
from typing import Optional, Tuple

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

MAX_MESSAGE_LENGTH: int = 4096

log = logging.getLogger(__name__)

_DELIMITERS = ("/", ":", "|", "_")
_SED_PATTERN = re.compile(
    r"^s([{delims}]).*?\1.*".format(delims="".join(re.escape(d) for d in _DELIMITERS))
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_sed(text: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse a sed expression ``s<delim>find<delim>replace[<delim>[flags]]``.

    Returns ``(find, replace, flags)`` or ``None`` on parse failure.
    Handles backslash-escaped delimiters inside each segment.
    """
    if len(text) < 3:
        return None
    delim = text[1]
    if delim not in _DELIMITERS:
        return None

    def _read_segment(s: str, start: int) -> Tuple[str, int]:
        """Read characters until an unescaped delimiter; return (segment, next_pos)."""
        buf = []
        i = start
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == delim:
                buf.append(delim)
                i += 2
            elif s[i] == delim:
                return "".join(buf), i + 1
            else:
                buf.append(s[i])
                i += 1
        return "".join(buf), i

    find, pos = _read_segment(text, 2)
    replace, pos = _read_segment(text, pos)
    flags = text[pos:].lower() if pos < len(text) else ""
    return find, replace, flags


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def sed_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Apply a sed substitution to the replied-to message."""
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return

    replied = msg.reply_to_message
    to_fix: Optional[str] = replied.text or replied.caption
    if not to_fix:
        return

    text = msg.text or msg.caption or ""
    result = _parse_sed(text)
    if not result:
        return

    find, replace, flags = result
    if not find:
        await msg.reply_text("You're trying to replace nothing with something?")
        return

    # Build regex flags.
    re_flags = 0
    if "i" in flags:
        re_flags |= re.IGNORECASE

    try:
        # Guard: don't let the bot parrot the whole message back verbatim (anti-abuse).
        whole_match = re.fullmatch(find, to_fix, flags=re_flags)
        if whole_match:
            await msg.reply_text(
                f"{update.effective_user.first_name} is trying to make me say stuff I don't wanna say!"
            )
            return

        if "g" in flags:
            new_text = re.sub(find, replace, to_fix, flags=re_flags).strip()
        else:
            new_text = re.sub(find, replace, to_fix, count=1, flags=re_flags).strip()

    except sre_constants.error:
        log.debug("sed: invalid regex %r from user %s", find, update.effective_user.id)
        await msg.reply_text("Invalid regex — do you even sed?")
        return

    if not new_text:
        return

    if len(new_text) >= MAX_MESSAGE_LENGTH:
        await msg.reply_text("The substitution result is too long for Telegram!")
        return

    await replied.reply_text(new_text)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register the sed regex handler."""
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION)
            & filters.REPLY
            & filters.Regex(_SED_PATTERN),
            sed_handler,
        ),
        group=12,
    )
    log.info("Plugin loaded: sed")
