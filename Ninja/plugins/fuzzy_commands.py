"""
plugins/fuzzy_commands.py — Fuzzy matching for bot commands.

Principle: A list of correct English command names.
The algorithm calculates similarity and suggests the correct command at ≥ 90% ratio.
No lists of potential errors — flexibility comes from the algorithm.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import is_user_admin
from core.helpers.fuzzy import levenshtein, normalize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Commands — (Display Label, Command, Admin Only)
# ---------------------------------------------------------------------------

COMMANDS: List[Tuple[str, str, bool]] = [

    # ── Settings and Help ──────────────────────────────────────────────────
    ("Settings",             "/settings",       True),
    ("Help",                 "/help",            False),

    # ── Administration ───────────────────────────────────────────────────────
    ("Ban",                  "/ban",             True),
    ("Temp Ban",             "/tban",            True),
    ("Unban",                "/unban",           True),
    ("Kick",                 "/kick",            True),
    ("Mute",                 "/mute",            True),
    ("Temp Mute",            "/tmute",           True),
    ("Unmute",               "/unmute",          True),
    ("Warn",                 "/warn",            True),
    ("View Warns",           "/warns",           False),
    ("Reset Warns",          "/resetwarn",       True),
    ("Lock Group",           "/lock",            True),
    ("Unlock Group",         "/unlock",          True),
    ("Pin Message",          "/pin",             True),
    ("Unpin",                "/unpin",           True),
    ("Promote",              "/promote",         True),
    ("Demote",               "/demote",          True),
    ("Admin List",           "/adminlist",       False),
    ("Purge",                "/purge",           True),
    ("Slowmode",             "/slowmode",        True),

    # ── Rules and Content ───────────────────────────────────────────────────
    ("Group Rules",          "/rules",           False),
    ("Set Rules",            "/setrules",        True),
    ("Welcome Message",      "/setwelcome",      True),
    ("Goodbye Message",      "/setgoodbye",      True),
    ("Log Channel",          "/setlog",          True),
    ("Warn Filters",         "/warnlist",        True),
    ("Message Filters",      "/filters",         True),
    ("Saved Notes",          "/notes",           False),
    ("Captcha Settings",     "/captcha",         True),

    # ── Protection ─────────────────────────────────────────────────────────
    ("Flood Limit",          "/setflood",        True),
    ("Anti-Links",           "/antilinks",       True),
    ("CAS Global Ban",       "/cas",             False),
    ("Global Ban",           "/gban",            True),
    ("Federations",          "/federation",      True),
    ("Anti-Raid",            "/antiraid",        True),

    # ── Tools ──────────────────────────────────────────────────────────────
    ("Group Stats",          "/stats",           False),
    ("Group Info",           "/chatinfo",        False),
    ("User Info",            "/userinfo",        False),
    ("Current Time",         "/time",            False),
    ("Translate",            "/tl",              False),
    ("Calculator",           "/calc",            False),
    ("Wikipedia Search",     "/wiki",            False),
    ("Report User",          "/report",          False),
    ("Backup",               "/backup",          True),
    ("Alive?",               "/alive",           False),

    # ── Economy ────────────────────────────────────────────────────────────
    ("Bank Balance",         "/balance",         False),
    ("Daily Reward",         "/daily",           False),
    ("Transfer",             "/transfer",        False),
    ("Rich List",            "/richlist",        False),
    ("Steal",                "/steal",           False),
    ("Open Bank Account",    "/openbank",        False),
    ("Request Loan",         "/loan",            False),
    ("Repay Loan",           "/repay",           False),
    ("Invest",               "/invest",          False),
    ("Weekly Salary",        "/salary",          False),
    ("Top Players",          "/top",             False),

    # ── Games ──────────────────────────────────────────────────────────────
    ("Ninja Game",           "/ninja",           False),
    ("Farm Game",            "/farm",            False),
    ("Castle Game",          "/castle",          False),
    ("Create Castle",        "/create_castle",   False),
    ("Create Farm",          "/create_farm",     False),
    ("Upgrade Castle",       "/upgrade_castle",  False),
    ("Upgrade Farm",         "/upgrade_farm",    False),
    ("Buy Army",             "/buy_army",        False),
    ("Start Battle",         "/start_battle",    False),
    ("Rescue Member",        "/rescue",          False),
    ("Assassinate",          "/assassinate",     False),
    ("Quiz",                 "/quiz",            False),
    ("Roll Dice",            "/roll",            False),
    ("Try Luck",             "/luck",            False),
    ("Trade",                "/trade",           False),
]

# ---------------------------------------------------------------------------
# Search — SequenceMatcher + Levenshtein
# ---------------------------------------------------------------------------

_POOL: List[Tuple[str, int]] = [
    (normalize(label), i) for i, (label, _, _) in enumerate(COMMANDS)
]

_THRESHOLD = 0.90
_MAX_LEN   = 30


def _match(text: str) -> Optional[Tuple[int, float]]:
    """
    Return (command index, ratio) if similarity exceeds 90%
    or if the difference is only one character (Levenshtein = 1).
    """
    norm = normalize(text)
    if not norm:
        return None

    best_idx: Optional[int] = None
    best_ratio = 0.0

    for kw_norm, idx in _POOL:
        ratio = SequenceMatcher(None, norm, kw_norm).ratio()
        passes = ratio >= _THRESHOLD or (
            len(norm) >= 3 and levenshtein(norm, kw_norm) == 1
        )
        if passes and ratio > best_ratio:
            best_ratio = ratio
            best_idx = idx

    if best_idx is not None:
        return best_idx, max(best_ratio, _THRESHOLD)
    return None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat    = update.effective_chat
    user    = update.effective_user
    message = update.effective_message
    if not user or not chat or not message:
        return

    text = (message.text or "").strip()
    if not text or text.startswith("/") or len(text) > _MAX_LEN:
        return

    result = _match(text)
    if result is None:
        return

    idx, _ = result
    label, command, admin_only = COMMANDS[idx]

    if admin_only and not await is_user_admin(chat, user.id):
        return

    base = command.split()[0]
    await message.reply_html(
        f"🔍 Did you mean <b>{label}</b>?\n"
        f"<code>{command}</code>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"▶️ {base}", switch_inline_query_current_chat=base)
        ]]),
        quote=True,
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            _handler,
        ),
        group=25,
    )
    logger.info("Plugin loaded: fuzzy_commands (%d commands)", len(COMMANDS))
