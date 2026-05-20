"""
plugins/help_menu.py — Interactive button-based help menu.

The /help command opens a beautiful inline keyboard menu inspired by
Group Help bot.  Each category button reveals the commands for that
section without leaving the chat or opening a new message.

Navigation flow:
  /help  →  Main Menu (category buttons)
         →  [tap category]  →  Category detail (commands + Back button)
         →  [tap Back]      →  Main Menu

In group chats the bot sends the menu as a reply in the group itself
(no need to go to PM — it's all inline).

Categories:
  🛡 Protection   — Spam, flood, CAPTCHA, links, raids
  🚫 Moderation   — Bans, warns, mutes, global bans
  🔒 Locks        — Content type locks
  📝 Content      — Filters, blacklist, notes, rules
  👋 Welcome      — Welcome/goodbye messages
  📋 Admin        — Admin tools, purge, approve, federation
  📊 Stats & Info — Statistics, user info, timezones
  ⚙️ Settings     — Bot settings panel, night mode, reports
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

log = logging.getLogger(__name__)

_CB = "help"

# ---------------------------------------------------------------------------
# Help content — each category: (emoji, title, list of (command, description))
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, tuple[str, str, list[tuple[str, str]]]] = {
    "protection": (
        "🛡", "Protection",
        [
            ("/antilinks <off|invite|all>",   "Block Telegram invite links or all URLs"),
            ("/antilinkaction <action>",       "Set action: delete / +warn / +mute / +ban"),
            ("/antiraid <on|off>",             "Enable mass-join raid detection"),
            ("/raidthreshold <n>",             "Joins needed to trigger a raid (default 10)"),
            ("/raidwindow <seconds>",          "Detection window in seconds (default 60)"),
            ("/raidlockdown <seconds>",        "How long lockdown lasts after a raid"),
            ("/raidkick <on|off>",             "Kick raiders automatically on detection"),
            ("/setflood <n|off>",              "Max consecutive messages before action"),
            ("/flood",                         "Show current flood limit"),
            ("/captcha",                       "Show CAPTCHA settings"),
            ("/setcaptcha <button|math|text>", "Set CAPTCHA type for new members"),
        ],
    ),
    "moderation": (
        "🚫", "Moderation",
        [
            ("/ban [@user] [reason]",          "Permanently ban a user"),
            ("/unban [@user]",                 "Unban a user"),
            ("/kick [@user]",                  "Kick (but don't ban) a user"),
            ("/tempban [@user] <time>",        "Temporary ban (e.g. 1h, 2d)"),
            ("/warn [@user] [reason]",         "Issue a warning"),
            ("/rmwarn [@user]",                "Remove last warning"),
            ("/warns [@user]",                 "Show warn count"),
            ("/warnlimit <n>",                 "Set max warns before action"),
            ("/warnaction <ban|kick|mute>",    "Action on reaching warn limit"),
            ("/mute [@user]",                  "Mute a user indefinitely"),
            ("/unmute [@user]",                "Unmute a user"),
            ("/tmute [@user] <time>",          "Temporary mute (e.g. 30m, 1h)"),
            ("/gban [@user] [reason]",         "Global ban across all groups"),
            ("/ungban [@user]",                "Lift global ban"),
            ("/gbanlist",                      "List all globally banned users"),
        ],
    ),
    "locks": (
        "🔒", "Locks",
        [
            ("/lock <type>",    "Lock a content type (see list below)"),
            ("/unlock <type>",  "Unlock a content type"),
            ("/locks",          "Show all lock statuses"),
            ("",                "─── Content types ───"),
            ("",                "text · photo · video · audio"),
            ("",                "voice · document · sticker · gif"),
            ("",                "url · forward · game · poll"),
            ("",                "button · inline"),
        ],
    ),
    "content": (
        "📝", "Content",
        [
            ("/filter <word> <reply>",         "Auto-reply when keyword is sent"),
            ("/rmfilter <word>",               "Remove a keyword filter"),
            ("/filters",                       "List all keyword filters"),
            ("/addblacklist <word>",           "Add word to blacklist (auto-delete)"),
            ("/rmblacklist <word>",            "Remove word from blacklist"),
            ("/blacklist",                     "Show blacklist"),
            ("/save <name> <content>",         "Save a note"),
            ("/get <name>  or  #name",         "Retrieve a saved note"),
            ("/notes",                         "List all saved notes"),
            ("/clear <name>",                  "Delete a note"),
            ("/setrules <text>",               "Set group rules"),
            ("/rules",                         "Show group rules"),
            ("/clearrules",                    "Delete group rules"),
        ],
    ),
    "welcome": (
        "👋", "Welcome",
        [
            ("/setwelcome <text>",             "Set a custom welcome message"),
            ("/welcome",                       "Show current welcome settings"),
            ("/setgoodbye <text>",             "Set a custom goodbye message"),
            ("/welcome on|off",                "Enable or disable welcome messages"),
        ],
    ),
    "admin": (
        "📋", "Admin Tools",
        [
            ("/promote [@user]",               "Grant admin rights"),
            ("/demote [@user]",                "Revoke admin rights"),
            ("/pin",                           "Pin the replied message"),
            ("/unpin",                         "Unpin the pinned message"),
            ("/invitelink",                    "Generate a new invite link"),
            ("/purge",                         "Delete messages from reply to here"),
            ("/del",                           "Delete the replied message"),
            ("/purgefrom <n>",                 "Delete the last n messages"),
            ("/zombies",                       "Kick all deleted accounts"),
            ("/approve [@user]",               "Exempt user from all filters"),
            ("/disapprove [@user]",            "Remove filter exemption"),
            ("/approved",                      "List all approved users"),
            ("/report [reason]",               "Report a message to admins (reply)"),
            ("/reports <on|off>",              "Toggle report feature"),
            ("/joinfed <fed_id>",              "Join a ban federation"),
            ("/leavefed",                      "Leave current federation"),
            ("/fban [@user]",                  "Ban across all federation groups"),
            ("/funban [@user]",                "Lift federation ban"),
            ("/newfed <name>",                 "Create a federation (private chat)"),
        ],
    ),
    "stats": (
        "📊", "Stats & Info",
        [
            ("/stats",                         "Group activity stats (4 views)"),
            ("/id [@user]",                    "Show Telegram ID"),
            ("/info [@user]",                  "Detailed user profile"),
            ("/chatinfo",                      "Detailed group info"),
            ("/staff",                         "List all group admins"),
            ("/settimezone <city>",            "Set your timezone (e.g. Aden, London)"),
            ("/mytimezone",                    "Show your timezone and local time"),
            ("/cleartimezone",                 "Remove your timezone data"),
            ("Share location (private chat)",  "Auto-detect timezone from GPS"),
        ],
    ),
    "settings": (
        "⚙️", "Settings",
        [
            ("/settings",                      "Open interactive settings panel"),
            ("/nightmode <on|off>",            "Enable automatic night restrictions"),
            ("/nighthours <HH:MM> <HH:MM>",   "Set night start and end times"),
            ("/nighttimezone <city>",          "Set timezone for night mode"),
            ("/disable <command>",             "Disable a command in this group"),
            ("/enable <command>",              "Re-enable a command"),
            ("/disabled",                      "List disabled commands"),
            ("/setlog",                        "Set the log channel"),
            ("/unsetlog",                      "Remove the log channel"),
            ("/setlang <code>",                "Set group language (en/ar/es/fr/de/ru)"),
            ("/shield",                        "Emergency lockdown — silence all members"),
            ("/unshield",                      "Lift the lockdown"),
            ("/shieldstatus",                  "Check shield state"),
            ("/checkperms",                    "Show bot's current permissions"),
        ],
    ),
    "advanced": (
        "🔧", "Advanced",
        [
            ("/gban [@user] [reason]",         "Global ban across all groups (owner)"),
            ("/ungban [@user]",                "Lift global ban (owner)"),
            ("/broadcast <text>",              "Broadcast message to all groups (owner)"),
            ("/spamwatch <on|off>",            "Toggle SpamWatch integration"),
            ("/antiastro <on|off>",            "Block astrology/horoscope spam"),
            ("/adaptive_captcha <on|off>",     "Enable risk-based adaptive CAPTCHA"),
            ("/protect <on|off>",              "Channel post forwarding protection"),
            ("/appeal",                        "Submit a ban appeal (PM bot)"),
            ("/addrss <url>",                  "Subscribe chat to an RSS feed"),
            ("/removerss <url>",               "Unsubscribe from an RSS feed"),
            ("/listrss",                       "List active RSS subscriptions"),
        ],
    ),
    "games": (
        "🎮", "Games & Economy",
        [
            ("/start_ninja",           "Begin your ninja career"),
            ("/ninja_profile",         "View your ninja stats"),
            ("/assassinate [@user]",   "Attempt to assassinate another ninja"),
            ("/kidnap [@user]",        "Kidnap a ninja for ransom"),
            ("/wallet",                "Check your virtual balance"),
            ("/daily",                 "Claim your daily reward"),
            ("/transfer @user <amt>",  "Send virtual currency to someone"),
            ("/create_farm",           "Start your virtual farm"),
            ("/plant <crop>",          "Plant a crop on your farm"),
            ("/harvest",               "Harvest ready crops"),
            ("/farm",                  "View your farm status"),
            ("/create_castle",         "Found your castle kingdom"),
            ("/duel [@user]",          "Challenge someone to a duel"),
            ("/alliance [@user]",      "Form an alliance with another castle"),
            ("/leaderboard",           "Game leaderboard for this chat"),
        ],
    ),
    "social": (
        "💬", "Social & Fun",
        [
            ("/afk [reason]",                  "Mark yourself as AFK"),
            ("/setme <text>",                  "Set your personal info bio"),
            ("/me [@user]",                    "Show a user's self-set info"),
            ("/setbio <text> (reply)",         "Admin: set bio for a user"),
            ("/bio [@user]",                   "Show a user's admin-set bio"),
            ("/runs",                          "Get a random running-away string"),
            ("/slap [@user]",                  "Slap someone with a random item"),
            ("/wiki <query>",                  "Wikipedia quick lookup"),
            ("/ud <term>",                     "Urban Dictionary definition"),
            ("/calc <expr>",                   "Safe math expression evaluator"),
            ("/getsticker",                    "Get sticker file_id and pack info"),
            ("/stickerpack",                   "Deep-link to a sticker pack"),
            ("/sticker2img",                   "Convert a sticker to PNG"),
            ("/kang [@emoji]",                 "Add a sticker/image to your kang pack"),
            ("s/<old>/<new>",                  "Regex substitution on your last message"),
        ],
    ),
    "leaderboard": (
        "📈", "Leaderboard",
        [
            ("/topusers [N]",                  "Top N most active users (last 30 days)"),
            ("/mytop",                         "Your personal rank in this chat"),
            ("/chatlist",                      "List all chats the bot is in (owner)"),
        ],
    ),
    "pm": (
        "🔗", "PM Connection",
        [
            ("/connect <chat_id>",             "Connect PM to a group you admin"),
            ("/disconnect",                    "End the PM connection"),
            ("/connected",                     "Show currently connected group"),
            ("/tagall [reason]",               "Mention all group admins"),
            ("/tagadmins [reason]",            "Alias for /tagall"),
        ],
    ),
    "scheduler": (
        "⏰", "Scheduler",
        [
            ("/schedule <time> <text>",        "Schedule a message to this chat"),
            ("/listschedules",                 "List all scheduled messages"),
            ("/cancelschedule <id>",           "Cancel a scheduled message by ID"),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _main_keyboard() -> InlineKeyboardMarkup:
    """Build the 4×2 category grid keyboard."""
    rows = []
    items = list(CATEGORIES.items())
    for i in range(0, len(items), 2):
        row = []
        for key, (emoji, title, _) in items[i: i + 2]:
            row.append(InlineKeyboardButton(
                f"{emoji} {title}",
                callback_data=f"{_CB}:cat:{key}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("✖ Close", callback_data=f"{_CB}:close")])
    return InlineKeyboardMarkup(rows)


def _category_keyboard(key: str) -> InlineKeyboardMarkup:
    """Build the back button for a category view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("« Back to Menu", callback_data=f"{_CB}:main")],
        [InlineKeyboardButton("✖ Close", callback_data=f"{_CB}:close")],
    ])


def _main_text(bot_name: str) -> str:
    return (
        f"<b>🥷 {bot_name}</b>\n"
        f"<i>Professional group management — protection, moderation, games & more.</i>\n\n"
        f"<b>📂 Choose a category to explore commands:</b>"
    )


def _category_text(key: str) -> str:
    emoji, title, commands = CATEGORIES[key]
    lines = [f"<b>{emoji} {title}</b>\n"]
    for cmd, desc in commands:
        if not cmd:
            lines.append(f"\n<i>{desc}</i>")
        elif desc:
            lines.append(f"<code>{cmd}</code>\n  ↳ {desc}\n")
        else:
            lines.append(f"<code>{cmd}</code>")
    lines.append("\n<i>Tap « Back to return to the menu.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /help command
# ---------------------------------------------------------------------------

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Open the interactive help menu.

    In group chats: sends the menu inline in the group.
    With /help <category>: jumps directly to that category.
    """
    message = update.effective_message
    args = context.args or []

    # Try to get bot name for the header.
    try:
        me = await context.bot.get_me()
        bot_name = me.first_name
    except Exception:
        bot_name = "Ninja Bot"

    if args:
        key = args[0].lower()
        if key in CATEGORIES:
            text = _category_text(key)
            keyboard = _category_keyboard(key)
            await message.reply_html(text, reply_markup=keyboard)
            return
        else:
            valid = ", ".join(sorted(CATEGORIES.keys()))
            await message.reply_text(f"Unknown category. Valid: {valid}")
            return

    await message.reply_html(
        _main_text(bot_name),
        reply_markup=_main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def help_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle menu navigation presses."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    parts = data.split(":")

    # help:close
    if len(parts) >= 2 and parts[1] == "close":
        try:
            await query.message.delete()
        except BadRequest:
            pass
        return

    # help:main
    if len(parts) >= 2 and parts[1] == "main":
        try:
            me = await context.bot.get_me()
            bot_name = me.first_name
        except Exception:
            bot_name = "Ninja Bot"
        try:
            await query.edit_message_text(
                _main_text(bot_name),
                parse_mode=ParseMode.HTML,
                reply_markup=_main_keyboard(),
            )
        except BadRequest:
            pass
        return

    # help:cat:<key>
    if len(parts) >= 3 and parts[1] == "cat":
        key = parts[2]
        if key not in CATEGORIES:
            return
        try:
            await query.edit_message_text(
                _category_text(key),
                parse_mode=ParseMode.HTML,
                reply_markup=_category_keyboard(key),
            )
        except BadRequest:
            pass
        return


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def start_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    رسالة ترحيب عند /start — تعريف بالبوت وأبرز مميزاته.
    تعمل في المجموعات والخاص على حد سواء.
    """
    message = update.effective_message
    user = update.effective_user
    chat_type = update.effective_chat.type if update.effective_chat else "private"

    try:
        me = await context.bot.get_me()
        bot_name = me.first_name
    except Exception:
        bot_name = "Ninja Bot"

    if chat_type == "private":
        text = (
            f"🥷 <b>أهلاً {user.first_name}!</b>\n\n"
            f"أنا <b>{bot_name}</b> — بوت إدارة مجموعات تيليجرام المتكامل.\n\n"
            f"<b>🛡 الحماية</b>\n"
            f"  مكافحة سبام · كابتشا · مكافحة الغارات · فلتر بايز الذكي\n\n"
            f"<b>🚫 الإدارة</b>\n"
            f"  بان · ميوت · إنذارات · فيدريشن · تنظيف\n\n"
            f"<b>💰 الاقتصاد</b>\n"
            f"  بنك وهمي · راتب · استثمار · سرقة · سطو جماعي · قروض\n\n"
            f"<b>🎮 الألعاب</b>\n"
            f"  نينجا · مزرعة · قلعة · مسابقات تخمين\n\n"
            f"أضفني لمجموعتك ومنحني صلاحيات المشرف لأبدأ العمل!\n\n"
            f"<i>اكتب /help لقائمة الأوامر الكاملة.</i>"
        )
    else:
        text = (
            f"🥷 <b>مرحباً {user.first_name}!</b>\n\n"
            f"أنا <b>{bot_name}</b> — بوت الإدارة المتكامل.\n"
            f"اكتب /help لقائمة جميع الأوامر."
        )

    await message.reply_html(text)


async def register(application: Application) -> None:
    """Register /start, /help commands and navigation callbacks."""
    # استبعاد deep-links مثل /start rules_... حتى تلتقطها plugins أخرى
    application.add_handler(
        CommandHandler("start", start_cmd, filters=~filters.Regex(r"rules_"))
    )
    application.add_handler(CommandHandler("help",  help_cmd))
    application.add_handler(
        CallbackQueryHandler(help_callback, pattern=rf"^{_CB}:")
    )
    log.info("Plugin loaded: help_menu")
