"""
plugins/fuzzy_commands.py — مطابقة ضبابية لأوامر البوت.

المبدأ: قائمة بالأسماء العربية الصحيحة للأوامر فقط.
الخوارزمية تحسب التشابه وتقترح الأمر الصحيح عند نسبة ≥ 90%.
لا توجد قوائم بالأخطاء المحتملة — المرونة تأتي من الخوارزمية.
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
# الأوامر — (الاسم العربي الصحيح، الأمر، للمشرفين فقط)
# ---------------------------------------------------------------------------

COMMANDS: List[Tuple[str, str, bool]] = [

    # ── الإعدادات والمساعدة ─────────────────────────────────────────────────
    ("الإعدادات",             "/settings",       True),
    ("المساعدة",              "/help",            False),

    # ── الإدارة ─────────────────────────────────────────────────────────────
    ("حظر",                   "/ban",             True),
    ("حظر مؤقت",             "/tban",            True),
    ("رفع الحظر",            "/unban",           True),
    ("طرد",                   "/kick",            True),
    ("كتم",                   "/mute",            True),
    ("كتم مؤقت",             "/tmute",           True),
    ("رفع الكتم",            "/unmute",          True),
    ("تحذير",                "/warn",            True),
    ("عرض التحذيرات",       "/warns",           False),
    ("مسح التحذيرات",       "/resetwarn",       True),
    ("قفل المجموعة",         "/lock",            True),
    ("فتح المجموعة",         "/unlock",          True),
    ("تثبيت رسالة",         "/pin",             True),
    ("إلغاء التثبيت",       "/unpin",           True),
    ("ترقية لمشرف",          "/promote",         True),
    ("إلغاء الترقية",       "/demote",          True),
    ("قائمة المشرفين",      "/adminlist",       False),
    ("حذف الرسائل",          "/purge",           True),
    ("الوضع البطيء",         "/slowmode",        True),

    # ── القواعد والمحتوى ────────────────────────────────────────────────────
    ("قواعد المجموعة",      "/rules",           False),
    ("تعيين القواعد",       "/setrules",        True),
    ("رسالة الترحيب",      "/setwelcome",      True),
    ("رسالة الوداع",        "/setgoodbye",      True),
    ("قناة السجلات",        "/setlog",          True),
    ("فلاتر التحذير",       "/warnlist",        True),
    ("فلاتر الرسائل",       "/filters",         True),
    ("الملاحظات المحفوظة",  "/notes",           False),
    ("إعدادات الكابتشا",    "/captcha",         True),

    # ── الحماية ─────────────────────────────────────────────────────────────
    ("حد الفيضان",           "/setflood",        True),
    ("مكافحة الروابط",      "/antilinks",       True),
    ("الحظر العالمي CAS",   "/cas",             False),
    ("حظر عالمي",            "/gban",            True),
    ("الاتحادات",            "/federation",      True),
    ("مكافحة الغارات",      "/antiraid",        True),

    # ── الأدوات ─────────────────────────────────────────────────────────────
    ("إحصائيات المجموعة",   "/stats",           False),
    ("معلومات المجموعة",    "/chatinfo",        False),
    ("معلوماتي الشخصية",   "/userinfo",        False),
    ("الوقت الحالي",        "/time",            False),
    ("ترجمة النص",           "/tl",              False),
    ("الآلة الحاسبة",       "/calc",            False),
    ("بحث في ويكيبيديا",   "/wiki",            False),
    ("الإبلاغ عن مستخدم",  "/report",          False),
    ("نسخ احتياطي",          "/backup",          True),
    ("البوت شغال؟",          "/alive",           False),

    # ── الاقتصاد ────────────────────────────────────────────────────────────
    ("رصيدي في البنك",      "/balance",         False),
    ("المكافأة اليومية",    "/daily",           False),
    ("تحويل رصيد",          "/transfer",        False),
    ("قائمة الأثرياء",      "/richlist",        False),
    ("سرقة الرصيد",         "/steal",           False),
    ("فتح حساب بنكي",      "/openbank",        False),
    ("طلب قرض",             "/loan",            False),
    ("سداد القرض",          "/repay",           False),
    ("استثمار الرصيد",      "/invest",          False),
    ("الراتب الأسبوعي",     "/salary",          False),
    ("ترتيب اللاعبين",      "/top",             False),

    # ── الألعاب ─────────────────────────────────────────────────────────────
    ("لعبة النينجا",        "/ninja",           False),
    ("لعبة المزرعة",        "/farm",            False),
    ("لعبة القلعة",         "/castle",          False),
    ("إنشاء القلعة",        "/create_castle",   False),
    ("إنشاء المزرعة",       "/create_farm",     False),
    ("ترقية القلعة",        "/upgrade_castle",  False),
    ("ترقية المزرعة",       "/upgrade_farm",    False),
    ("شراء الجيش",          "/buy_army",        False),
    ("بدء المعركة",         "/start_battle",    False),
    ("إنقاذ عضو",           "/rescue",          False),
    ("تنفيذ اغتيال",        "/assassinate",     False),
    ("مسابقة أسئلة",        "/quiz",            False),
    ("رمي النرد",            "/roll",            False),
    ("تجربة الحظ",          "/luck",            False),
    ("تبادل تجاري",         "/trade",           False),
]

# ---------------------------------------------------------------------------
# البحث — SequenceMatcher + Levenshtein
# ---------------------------------------------------------------------------

_POOL: List[Tuple[str, int]] = [
    (normalize(label), i) for i, (label, _, _) in enumerate(COMMANDS)
]

_THRESHOLD = 0.90
_MAX_LEN   = 30


def _match(text: str) -> Optional[Tuple[int, float]]:
    """
    أرجع (فهرس الأمر، النسبة) إذا تجاوز التشابه 90%
    أو إذا كان الفرق حرفاً واحداً فقط (Levenshtein = 1).
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
# المعالج
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
        f"🔍 هل تقصد <b>{label}</b>؟\n"
        f"<code>{command}</code>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"▶️ {base}", switch_inline_query_current_chat=base)
        ]]),
        quote=True,
    )


# ---------------------------------------------------------------------------
# تسجيل
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
