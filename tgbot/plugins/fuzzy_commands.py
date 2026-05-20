"""
plugins/fuzzy_commands.py — مطابقة ضبابية شاملة لجميع أوامر البوت.

يستمع لكل رسالة نصية قصيرة (≤ 35 حرفاً، لا تبدأ بـ /).
إذا تجاوزت نسبة التشابه مع أي كلمة مفتاحية 90% يقترح البوت الأمر الصحيح.

يعمل مع: الإدارة، الحماية، الاقتصاد، الألعاب، والأوامر العامة.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from core.helpers.chat_status import is_user_admin
from core.helpers.fuzzy import build_lookup, levenshtein, normalize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# سجل الأوامر الشامل
# ---------------------------------------------------------------------------

REGISTRY: List[Dict] = [

    # ── الإعدادات ──────────────────────────────────────────────────────────
    {
        "keywords": ["الإعدادات", "الاعدادات", "اعدادات", "إعدادات",
                     "اعدادت", "الاعدادت", "اعداد", "settings", "سيتينج"],
        "command": "/settings",
        "description": "إعدادات المجموعة",
        "admin_only": True,
    },

    # ── المساعدة ────────────────────────────────────────────────────────────
    {
        "keywords": ["مساعدة", "المساعدة", "مساعده", "مساعدت", "مساعد",
                     "help", "هيلب", "اوامر", "الاوامر", "أوامر", "الأوامر"],
        "command": "/help",
        "description": "قائمة الأوامر",
        "admin_only": False,
    },

    # ── الحظر ───────────────────────────────────────────────────────────────
    {
        "keywords": ["حظر", "الحظر", "بان", "ban"],
        "command": "/ban [مستخدم] [سبب]",
        "description": "حظر مستخدم من المجموعة",
        "admin_only": True,
    },
    {
        "keywords": ["حظر مؤقت", "بان مؤقت", "تبان", "tban", "بان وقتي"],
        "command": "/tban [مستخدم] <مدة> [سبب]",
        "description": "حظر مؤقت (10m / 2h / 3d)",
        "admin_only": True,
    },
    {
        "keywords": ["رفع حظر", "فك حظر", "فك البان", "انبان", "unban"],
        "command": "/unban [مستخدم]",
        "description": "رفع الحظر عن مستخدم",
        "admin_only": True,
    },

    # ── الطرد ───────────────────────────────────────────────────────────────
    {
        "keywords": ["طرد", "الطرد", "كيك", "kick"],
        "command": "/kick [مستخدم] [سبب]",
        "description": "طرد مستخدم (يمكنه العودة)",
        "admin_only": True,
    },

    # ── الكتم ───────────────────────────────────────────────────────────────
    {
        "keywords": ["كتم", "الكتم", "ميوت", "mute"],
        "command": "/mute [مستخدم] [سبب]",
        "description": "كتم مستخدم",
        "admin_only": True,
    },
    {
        "keywords": ["كتم مؤقت", "تيميوت", "tmute", "ميوت وقتي"],
        "command": "/tmute [مستخدم] <مدة> [سبب]",
        "description": "كتم مؤقت",
        "admin_only": True,
    },
    {
        "keywords": ["رفع كتم", "فك كتم", "انميوت", "unmute"],
        "command": "/unmute [مستخدم]",
        "description": "رفع الكتم",
        "admin_only": True,
    },

    # ── التحذيرات ───────────────────────────────────────────────────────────
    {
        "keywords": ["تحذير", "وارن", "warn"],
        "command": "/warn [مستخدم] [سبب]",
        "description": "إصدار تحذير",
        "admin_only": True,
    },
    {
        "keywords": ["تحذيرات", "سجل تحذيرات", "وارنز", "warns"],
        "command": "/warns [مستخدم]",
        "description": "عرض سجل التحذيرات",
        "admin_only": False,
    },
    {
        "keywords": ["مسح تحذيرات", "إعادة تعيين تحذيرات", "ريست وارن", "resetwarn"],
        "command": "/resetwarn [مستخدم]",
        "description": "مسح جميع تحذيرات المستخدم",
        "admin_only": True,
    },
    {
        "keywords": ["فلتر تحذير", "فلاتر التحذير", "كلمات تحذير", "warnlist"],
        "command": "/warnlist",
        "description": "قائمة فلاتر التحذير التلقائي",
        "admin_only": True,
    },

    # ── القفل ───────────────────────────────────────────────────────────────
    {
        "keywords": ["قفل", "قفل المجموعة", "لوك", "lock"],
        "command": "/lock [نوع]",
        "description": "قفل نوع من الرسائل",
        "admin_only": True,
    },
    {
        "keywords": ["فتح القفل", "رفع القفل", "انلوك", "unlock"],
        "command": "/unlock [نوع]",
        "description": "رفع القفل",
        "admin_only": True,
    },

    # ── التثبيت ─────────────────────────────────────────────────────────────
    {
        "keywords": ["تثبيت", "تثبت", "بين", "pin"],
        "command": "/pin",
        "description": "تثبيت رسالة",
        "admin_only": True,
    },
    {
        "keywords": ["إلغاء تثبيت", "فك التثبيت", "انبين", "unpin"],
        "command": "/unpin",
        "description": "إلغاء تثبيت الرسالة",
        "admin_only": True,
    },

    # ── الترقية والتخفيض ────────────────────────────────────────────────────
    {
        "keywords": ["ترقية", "تعيين مشرف", "ادمن", "برومت", "promote"],
        "command": "/promote [مستخدم]",
        "description": "ترقية مستخدم لمشرف",
        "admin_only": True,
    },
    {
        "keywords": ["إلغاء مشرف", "تخفيض", "ديموت", "demote"],
        "command": "/demote [مستخدم]",
        "description": "إلغاء صلاحيات المشرف",
        "admin_only": True,
    },
    {
        "keywords": ["المشرفون", "المشرفين", "قائمة المشرفين", "ادمن ليست", "adminlist"],
        "command": "/adminlist",
        "description": "قائمة مشرفي المجموعة",
        "admin_only": False,
    },

    # ── الحذف ───────────────────────────────────────────────────────────────
    {
        "keywords": ["تنظيف", "مسح رسائل", "حذف رسائل", "بيرج", "purge"],
        "command": "/purge",
        "description": "حذف الرسائل (رد على أول رسالة)",
        "admin_only": True,
    },

    # ── القواعد ─────────────────────────────────────────────────────────────
    {
        "keywords": ["القواعد", "قواعد", "قوانين", "القوانين", "رولز", "rules"],
        "command": "/rules",
        "description": "قواعد المجموعة",
        "admin_only": False,
    },
    {
        "keywords": ["ضبط القواعد", "تعيين قواعد", "كتابة قواعد", "setrules"],
        "command": "/setrules <النص>",
        "description": "ضبط قواعد المجموعة",
        "admin_only": True,
    },

    # ── الترحيب والوداع ─────────────────────────────────────────────────────
    {
        "keywords": ["رسالة ترحيب", "ترحيب", "ويلكم", "setwelcome"],
        "command": "/setwelcome <النص>",
        "description": "ضبط رسالة الترحيب",
        "admin_only": True,
    },
    {
        "keywords": ["رسالة وداع", "وداع", "جودباي", "setgoodbye"],
        "command": "/setgoodbye <النص>",
        "description": "ضبط رسالة الوداع",
        "admin_only": True,
    },

    # ── الوضع البطيء ────────────────────────────────────────────────────────
    {
        "keywords": ["وضع بطيء", "سلو", "سلوموود", "slowmode"],
        "command": "/slowmode <ثوانٍ>",
        "description": "تفعيل الوضع البطيء",
        "admin_only": True,
    },

    # ── قناة السجلات ────────────────────────────────────────────────────────
    {
        "keywords": ["قناة السجلات", "قناة اللوج", "لوج", "setlog"],
        "command": "/setlog",
        "description": "ضبط قناة تسجيل الإجراءات",
        "admin_only": True,
    },

    # ── البث ────────────────────────────────────────────────────────────────
    {
        "keywords": ["بث", "بث رسالة", "برودكاست", "broadcast"],
        "command": "/broadcast <الرسالة>",
        "description": "إرسال رسالة لجميع المجموعات",
        "admin_only": True,
    },

    # ── الكابتشا ────────────────────────────────────────────────────────────
    {
        "keywords": ["كابتشا", "تحقق", "كاپتشا", "captcha"],
        "command": "/captcha",
        "description": "إعدادات التحقق من الأعضاء",
        "admin_only": True,
    },

    # ── الفلاتر ─────────────────────────────────────────────────────────────
    {
        "keywords": ["الفلاتر", "فلاتر", "المرشحات", "filters"],
        "command": "/filters",
        "description": "قائمة الفلاتر النشطة",
        "admin_only": True,
    },

    # ── الملاحظات ───────────────────────────────────────────────────────────
    {
        "keywords": ["الملاحظات", "ملاحظات", "نوتس", "notes"],
        "command": "/notes",
        "description": "الملاحظات المحفوظة",
        "admin_only": False,
    },

    # ── التقارير ────────────────────────────────────────────────────────────
    {
        "keywords": ["تقرير", "ريبورت", "report"],
        "command": "/report",
        "description": "الإبلاغ عن مستخدم للمشرفين",
        "admin_only": False,
    },
    {
        "keywords": ["التقارير", "قائمة التقارير", "اعدادات تقارير", "reports"],
        "command": "/reports",
        "description": "إعدادات نظام التقارير",
        "admin_only": True,
    },

    # ── الإحصائيات ──────────────────────────────────────────────────────────
    {
        "keywords": ["إحصائيات", "احصائيات", "احصاء", "ستاتس", "stats"],
        "command": "/stats",
        "description": "إحصائيات المجموعة",
        "admin_only": False,
    },

    # ── معلومات ─────────────────────────────────────────────────────────────
    {
        "keywords": ["معلومات المجموعة", "معلومات المجموعه", "تشات انفو", "chatinfo"],
        "command": "/chatinfo",
        "description": "معلومات المجموعة",
        "admin_only": False,
    },
    {
        "keywords": ["معلوماتي", "ملفي", "معلومات عني", "يوزر انفو", "userinfo"],
        "command": "/userinfo",
        "description": "معلوماتك الشخصية",
        "admin_only": False,
    },

    # ── الوقت والمنطقة الزمنية ──────────────────────────────────────────────
    {
        "keywords": ["الوقت", "وقت", "تايم", "time"],
        "command": "/time [المدينة]",
        "description": "عرض التوقيت الحالي",
        "admin_only": False,
    },

    # ── الترجمة ─────────────────────────────────────────────────────────────
    {
        "keywords": ["ترجمة", "ترجم", "ترانسليت", "tl"],
        "command": "/tl <النص>",
        "description": "ترجمة النص",
        "admin_only": False,
    },

    # ── الحاسبة ─────────────────────────────────────────────────────────────
    {
        "keywords": ["حساب", "احسب", "حاسبة", "كالك", "calc"],
        "command": "/calc <العملية>",
        "description": "آلة حاسبة",
        "admin_only": False,
    },

    # ── ويكيبيديا ───────────────────────────────────────────────────────────
    {
        "keywords": ["ويكي", "ويكيبيديا", "wiki"],
        "command": "/wiki <الموضوع>",
        "description": "بحث في ويكيبيديا",
        "admin_only": False,
    },

    # ── الاقتصاد ────────────────────────────────────────────────────────────
    {
        "keywords": ["رصيد", "محفظة", "محفظه", "بالانس", "balance", "كاش", "cash"],
        "command": "/balance",
        "description": "رصيدك في البنك",
        "admin_only": False,
    },
    {
        "keywords": ["يومي", "مكافأة يومية", "مكافأه يوميه", "ديلي", "daily"],
        "command": "/daily",
        "description": "استلام المكافأة اليومية",
        "admin_only": False,
    },
    {
        "keywords": ["تحويل", "تحويل رصيد", "ترانسفر", "transfer"],
        "command": "/transfer [مستخدم] <مبلغ>",
        "description": "تحويل رصيد لمستخدم",
        "admin_only": False,
    },
    {
        "keywords": ["الأثرياء", "أغنياء", "قائمة الأثرياء", "ريتش ليست", "richlist"],
        "command": "/richlist",
        "description": "أغنى أعضاء المجموعة",
        "admin_only": False,
    },
    {
        "keywords": ["سرقة", "سرق", "سرقه", "ستيل", "steal"],
        "command": "/steal [مستخدم]",
        "description": "سرقة رصيد عضو",
        "admin_only": False,
    },
    {
        "keywords": ["بنك", "فتح بنك", "اوبن بانك", "openbank"],
        "command": "/openbank",
        "description": "فتح حساب بنكي",
        "admin_only": False,
    },
    {
        "keywords": ["قرض", "اقتراض", "لون", "loan"],
        "command": "/loan <المبلغ>",
        "description": "طلب قرض",
        "admin_only": False,
    },
    {
        "keywords": ["سداد", "سداد قرض", "ريباي", "repay"],
        "command": "/repay <المبلغ>",
        "description": "سداد القرض",
        "admin_only": False,
    },
    {
        "keywords": ["استثمار", "استثمر", "انفست", "invest"],
        "command": "/invest <المبلغ>",
        "description": "استثمار رصيدك",
        "admin_only": False,
    },
    {
        "keywords": ["راتب", "مرتب", "سالاري", "salary"],
        "command": "/salary",
        "description": "استلام الراتب",
        "admin_only": False,
    },
    {
        "keywords": ["أفضل لاعب", "ترتيب", "توب", "top"],
        "command": "/top",
        "description": "ترتيب أفضل اللاعبين",
        "admin_only": False,
    },

    # ── الألعاب ─────────────────────────────────────────────────────────────
    {
        "keywords": ["نينجا", "لعبة النينجا", "لعبة نينجا", "ninja"],
        "command": "/ninja",
        "description": "لعبة النينجا",
        "admin_only": False,
    },
    {
        "keywords": ["مزرعة", "لعبة المزرعة", "لعبة مزرعه", "فارم", "farm"],
        "command": "/farm",
        "description": "لعبة المزرعة",
        "admin_only": False,
    },
    {
        "keywords": ["قلعة", "لعبة القلعة", "لعبة قلعه", "كاسل", "castle"],
        "command": "/castle",
        "description": "لعبة القلعة",
        "admin_only": False,
    },
    {
        "keywords": ["مسابقة", "كيز", "أسئلة", "اسئله", "كويز", "quiz"],
        "command": "/quiz",
        "description": "بدء مسابقة أسئلة",
        "admin_only": False,
    },
    {
        "keywords": ["نرد", "رمي نرد", "رول", "roll"],
        "command": "/roll",
        "description": "رمي النرد",
        "admin_only": False,
    },
    {
        "keywords": ["ثروة", "حظ", "لاك", "luck"],
        "command": "/luck",
        "description": "تجربة الحظ",
        "admin_only": False,
    },
    {
        "keywords": ["المزارعون", "مزارعون", "زراعة", "farm_top"],
        "command": "/top",
        "description": "ترتيب اللاعبين",
        "admin_only": False,
    },
    {
        "keywords": ["تجارة", "تجار", "ترايد", "trade"],
        "command": "/trade [مستخدم]",
        "description": "بدء تبادل تجاري",
        "admin_only": False,
    },

    # ── الحماية ─────────────────────────────────────────────────────────────
    {
        "keywords": ["الفيضان", "فيضان رسائل", "انتيفلود", "setflood"],
        "command": "/setflood <عدد>",
        "description": "ضبط حد الفيضان",
        "admin_only": True,
    },
    {
        "keywords": ["مكافحة روابط", "انتي لينك", "روابط", "antilinks"],
        "command": "/antilinks",
        "description": "إعدادات مكافحة الروابط",
        "admin_only": True,
    },
    {
        "keywords": ["كاس", "قائمة حظر عالمية", "cas"],
        "command": "/cas",
        "description": "التحقق من قاعدة الحظر العالمي CAS",
        "admin_only": False,
    },
    {
        "keywords": ["حظر عالمي", "غبان", "gban"],
        "command": "/gban [مستخدم] [سبب]",
        "description": "حظر عالمي عبر الاتحادات",
        "admin_only": True,
    },
    {
        "keywords": ["اتحاد", "فيدريشن", "فيدرالية", "federation"],
        "command": "/federation",
        "description": "إدارة الاتحادات",
        "admin_only": True,
    },
    {
        "keywords": ["الغارة", "انتيريد", "غارة", "antiraid"],
        "command": "/antiraid",
        "description": "إعدادات مكافحة الغارات",
        "admin_only": True,
    },

    # ── RSS ─────────────────────────────────────────────────────────────────
    {
        "keywords": ["ار اس اس", "rss", "تلقيمات", "تغذيات"],
        "command": "/rss",
        "description": "إدارة تغذيات RSS",
        "admin_only": True,
    },

    # ── عن البوت ────────────────────────────────────────────────────────────
    {
        "keywords": ["البوت حي", "البوت شغال", "بينج", "alive", "ping"],
        "command": "/alive",
        "description": "التحقق من تشغيل البوت",
        "admin_only": False,
    },
    {
        "keywords": ["مصدر", "عن البوت", "سورس", "source"],
        "command": "/source",
        "description": "معلومات عن البوت",
        "admin_only": False,
    },
    {
        "keywords": ["سرعة", "اختبار سرعة", "سبيد", "speedtest"],
        "command": "/speedtest",
        "description": "اختبار سرعة السيرفر",
        "admin_only": True,
    },
]

# ---------------------------------------------------------------------------
# بناء هياكل البيانات عند تحميل الوحدة
# ---------------------------------------------------------------------------

_LOOKUP: Dict[str, int] = build_lookup(REGISTRY)

_POOL: List[Tuple[str, int]] = [
    (normalize(kw), idx)
    for idx, entry in enumerate(REGISTRY)
    for kw in entry["keywords"]
]

_MAX_LEN = 35
_THRESHOLD = 0.90


def _find_entry(text: str) -> Optional[Tuple[Dict, float]]:
    """
    ابحث عن مدخل مطابق للنص المُدخَل عبر ثلاث مراحل:
      1. مطابقة مباشرة بعد التطبيع (فورية).
      2. SequenceMatcher ≥ 90%  (كلمات متشابهة).
      3. Levenshtein = 1  (خطأ إملائي مفرد في كلمة ≥ 3 أحرف).
    """
    norm = normalize(text)
    if not norm:
        return None

    # ── 1. مطابقة مباشرة ──────────────────────────────────────────────────
    if norm in _LOOKUP:
        return REGISTRY[_LOOKUP[norm]], 1.0

    # ── 2 + 3. مطابقة ضبابية ──────────────────────────────────────────────
    best_idx: Optional[int] = None
    best_ratio = 0.0

    for kw_norm, idx in _POOL:
        ratio = SequenceMatcher(None, norm, kw_norm).ratio()

        # المعيار الأول: نسبة SequenceMatcher فوق العتبة
        passes = ratio >= _THRESHOLD

        # المعيار الثاني: خطأ إملائي مفرد (Levenshtein = 1)
        # يُفعَّل فقط للكلمات القصيرة التي لا تصل للعتبة
        if not passes and len(norm) >= 3:
            passes = levenshtein(norm, kw_norm) == 1

        if passes and ratio > best_ratio:
            best_ratio = ratio
            best_idx = idx

    if best_idx is not None:
        # اعرض النسبة الفعلية أو العتبة كحد أدنى
        return REGISTRY[best_idx], max(best_ratio, _THRESHOLD)
    return None


async def _fuzzy_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not user or not chat or not message:
        return

    text = (message.text or "").strip()

    if not text or text.startswith("/") or text.startswith("@"):
        return
    if len(text) > _MAX_LEN:
        return

    result = _find_entry(text)
    if result is None:
        return

    entry, ratio = result

    if entry["admin_only"] and not await is_user_admin(chat, user.id):
        return

    cmd = entry["command"]
    desc = entry["description"]
    base_cmd = cmd.split()[0]

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"▶️ {base_cmd}",
            switch_inline_query_current_chat=base_cmd,
        )
    ]])

    await message.reply_html(
        f"🔍 هل تقصد: <b>{cmd}</b>؟\n"
        f"<i>{desc}</i>",
        reply_markup=keyboard,
        quote=True,
    )

    logger.debug(
        "fuzzy: '%s' → '%s' (%.0f%%) chat=%d",
        text, base_cmd, ratio * 100, chat.id,
    )


async def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
            _fuzzy_handler,
        ),
        group=25,
    )
    logger.info(
        "Plugin loaded: fuzzy_commands (%d entries, %d keywords, threshold=%.0f%%)",
        len(REGISTRY),
        len(_POOL),
        _THRESHOLD * 100,
    )
