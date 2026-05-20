"""
plugins/gender.py — ميزة تحديد الجنس الترفيهية.

يستطيع كل عضو تحديد جنسه (ولد/بنت) أو حذفه، ويظهر في /info.
المشرفون يضبطون كلمات وردود خاصة بكل جنس تُفعَّل تلقائياً.

─────────────────────────────────────────────
أوامر الأعضاء (نصية — بدون /)
─────────────────────────────────────────────
  تحديد جنسي ولد       → تسجيل الجنس ذكر
  تحديد جنسي بنت       → تسجيل الجنس أنثى
  حذف جنسي             → حذف الجنس
  جنسي                  → عرض جنسك
  جنسه   (رد على رسالة) → عرض جنس الشخص المردود عليه

─────────────────────────────────────────────
أوامر المشرفين (نصية — بدون /)
─────────────────────────────────────────────
  عدد البنات            → عدد الإناث المسجلات في هذه المجموعة
  عدد العيال            → عدد الذكور المسجلين في هذه المجموعة

  اضف كلمات بنات <كلمة ...>  → إضافة كلمات مفتاحية للبنات
  اضف كلمات عيال <كلمة ...>  → إضافة كلمات مفتاحية للعيال
  حذف كلمات البنات      → مسح جميع كلمات البنات
  حذف كلمات العيال      → مسح جميع كلمات العيال
  حذف كلمه بنات <كلمة>  → حذف كلمة بنات بعينها
  حذف كلمه عيال <كلمة>  → حذف كلمة عيال بعينها
  كلمات البنات          → عرض قائمة كلمات البنات
  كلمات العيال          → عرض قائمة كلمات العيال

  اضف رد للبنات <النص>  → إضافة رد جديد للبنات
  اضف رد للعيال <النص>  → إضافة رد جديد للعيال
  حذف رد للبنات <النص>  → حذف رد بنات بعينه
  حذف رد للعيال <النص>  → حذف رد عيال بعينه
  مسح ردود البنات       → مسح جميع ردود البنات
  مسح ردود العيال       → مسح جميع ردود العيال
  ردود البنات           → عرض قائمة ردود البنات
  ردود العيال           → عرض قائمة ردود العيال

─────────────────────────────────────────────
آلية الكلمات والردود
─────────────────────────────────────────────
  عندما يرسل عضو رسالة تحتوي كلمة مفتاحية من جنس معين،
  يختار البوت رداً عشوائياً من قائمة ردود ذلك الجنس ويرسله.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Optional

from sqlalchemy import delete, func, select
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.helpers.chat_status import user_admin_no_reply
from database.engine import get_session
from database.models import Chat, User
from database.models_extra import GenderKeyword, GenderResponse, UserGender

log = logging.getLogger(__name__)

# ── رسائل الاستجابة الافتراضية ──────────────────────────────────────────────

_MALE_EMOJI   = "👦"
_FEMALE_EMOJI = "👧"
_NO_GENDER    = "❓"

GENDER_LABEL = {
    "male":   f"{_MALE_EMOJI} ولد",
    "female": f"{_FEMALE_EMOJI} بنت",
}


# ── مساعدات داخلية ──────────────────────────────────────────────────────────

async def _ensure_user_and_chat(session, chat_id: int, user_id: int) -> None:
    """أنشئ سجلات User و Chat في DB إن لم تكن موجودة."""
    if not await session.get(User, user_id):
        session.add(User(id=user_id, first_name="", is_bot=False))
    if not await session.get(Chat, chat_id):
        session.add(Chat(id=chat_id))
    await session.flush()


async def _get_user_gender(session, user_id: int) -> Optional[str]:
    """أعد جنس المستخدم من DB أو None."""
    row = await session.get(UserGender, user_id)
    return row.gender if row else None


def _is_admin(update: Update) -> bool:
    """تحقق مبسط من صلاحيات المشرف — يعتمد على PTB cached admin list."""
    return user_admin_no_reply(update)


def _words_from_text(text: str, prefix: str) -> list[str]:
    """استخرج الكلمات بعد البادئة من النص."""
    after = text[len(prefix):].strip()
    return [w.strip() for w in after.split() if w.strip()]


def _response_text_from(text: str, prefix: str) -> str:
    """استخرج نص الرد بعد البادئة."""
    return text[len(prefix):].strip()


# ── معالجات الأعضاء ─────────────────────────────────────────────────────────

async def _handle_set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """تحديد جنسي ولد / تحديد جنسي بنت"""
    text  = (update.effective_message.text or "").strip()
    user  = update.effective_user
    chat  = update.effective_chat

    if "ولد" in text:
        gender = "male"
    elif "بنت" in text:
        gender = "female"
    else:
        return

    async with get_session() as session:
        await _ensure_user_and_chat(session, chat.id, user.id)
        row = await session.get(UserGender, user.id)
        if row:
            row.gender = gender
        else:
            session.add(UserGender(user_id=user.id, gender=gender))
        await session.commit()

    label = GENDER_LABEL[gender]
    await update.effective_message.reply_html(
        f"✅ تم تحديد جنسك كـ <b>{label}</b>."
    )


async def _handle_delete_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """حذف جنسي"""
    user = update.effective_user
    async with get_session() as session:
        row = await session.get(UserGender, user.id)
        if row:
            await session.delete(row)
            await session.commit()
            await update.effective_message.reply_html("🗑 تم حذف جنسك من السجل.")
        else:
            await update.effective_message.reply_html("ℹ️ لم تحدد جنسك أصلاً.")


async def _handle_my_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جنسي"""
    user = update.effective_user
    async with get_session() as session:
        gender = await _get_user_gender(session, user.id)

    if gender:
        label = GENDER_LABEL[gender]
        await update.effective_message.reply_html(
            f"👤 جنسك: <b>{label}</b>"
        )
    else:
        await update.effective_message.reply_html(
            f"❓ لم تحدد جنسك بعد.\n"
            f"اكتب <b>تحديد جنسي ولد</b> أو <b>تحديد جنسي بنت</b>."
        )


async def _handle_his_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جنسه — بالرد على رسالة"""
    msg = update.effective_message
    reply = msg.reply_to_message

    if not reply or not reply.from_user:
        await msg.reply_html("↩️ رد على رسالة شخص لترى جنسه.")
        return

    target = reply.from_user
    async with get_session() as session:
        gender = await _get_user_gender(session, target.id)

    name = target.full_name
    if gender:
        label = GENDER_LABEL[gender]
        await msg.reply_html(f"👤 <b>{name}</b>: {label}")
    else:
        await msg.reply_html(f"❓ <b>{name}</b> لم يحدد جنسه بعد.")


# ── معالجات المشرفين — إحصاء ────────────────────────────────────────────────

async def _handle_count_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عدد البنات"""
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    async with get_session() as session:
        result = await session.execute(
            select(func.count()).where(GenderKeyword.gender_type == "female")
        )
        count = (await session.execute(
            select(func.count()).select_from(UserGender).where(UserGender.gender == "female")
        )).scalar() or 0
    await update.effective_message.reply_html(f"{_FEMALE_EMOJI} عدد البنات المسجلات: <b>{count}</b>")


async def _handle_count_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عدد العيال"""
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    async with get_session() as session:
        count = (await session.execute(
            select(func.count()).select_from(UserGender).where(UserGender.gender == "male")
        )).scalar() or 0
    await update.effective_message.reply_html(f"{_MALE_EMOJI} عدد العيال المسجلين: <b>{count}</b>")


# ── معالجات المشرفين — كلمات مفتاحية ───────────────────────────────────────

async def _add_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str, prefix: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    words   = _words_from_text(update.effective_message.text or "", prefix)
    if not words:
        await update.effective_message.reply_html("✏️ اكتب الكلمات بعد الأمر.")
        return

    added = 0
    async with get_session() as session:
        if not await session.get(Chat, chat_id):
            session.add(Chat(id=chat_id))
            await session.flush()
        for word in words:
            exists = await session.execute(
                select(GenderKeyword).where(
                    GenderKeyword.chat_id    == chat_id,
                    GenderKeyword.keyword    == word.lower(),
                    GenderKeyword.gender_type == gender,
                )
            )
            if not exists.scalar_one_or_none():
                session.add(GenderKeyword(chat_id=chat_id, keyword=word.lower(), gender_type=gender))
                added += 1
        await session.commit()

    emoji = _FEMALE_EMOJI if gender == "female" else _MALE_EMOJI
    label = "البنات" if gender == "female" else "العيال"
    await update.effective_message.reply_html(
        f"{emoji} تمت إضافة <b>{added}</b> كلمة لـ{label}."
    )


async def _handle_add_kw_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_keywords(update, context, "female", "اضف كلمات بنات")


async def _handle_add_kw_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_keywords(update, context, "male", "اضف كلمات عيال")


async def _delete_all_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    async with get_session() as session:
        result = await session.execute(
            delete(GenderKeyword).where(
                GenderKeyword.chat_id == chat_id,
                GenderKeyword.gender_type == gender,
            )
        )
        await session.commit()
        deleted = result.rowcount

    label = "البنات" if gender == "female" else "العيال"
    await update.effective_message.reply_html(
        f"🗑 تم حذف <b>{deleted}</b> كلمة من قائمة {label}."
    )


async def _handle_del_all_kw_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_all_keywords(update, context, "female")


async def _handle_del_all_kw_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_all_keywords(update, context, "male")


async def _delete_one_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str, prefix: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    text    = update.effective_message.text or ""
    word    = text[len(prefix):].strip().lower()
    if not word:
        await update.effective_message.reply_html("✏️ اكتب الكلمة التي تريد حذفها.")
        return

    async with get_session() as session:
        result = await session.execute(
            delete(GenderKeyword).where(
                GenderKeyword.chat_id    == chat_id,
                GenderKeyword.keyword    == word,
                GenderKeyword.gender_type == gender,
            )
        )
        await session.commit()

    label = "البنات" if gender == "female" else "العيال"
    if result.rowcount:
        await update.effective_message.reply_html(f"✅ تم حذف كلمة <b>{word}</b> من {label}.")
    else:
        await update.effective_message.reply_html(f"⚠️ الكلمة <b>{word}</b> غير موجودة في قائمة {label}.")


async def _handle_del_one_kw_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_one_keyword(update, context, "female", "حذف كلمه بنات")


async def _handle_del_one_kw_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_one_keyword(update, context, "male", "حذف كلمه عيال")


async def _list_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    async with get_session() as session:
        rows = (await session.execute(
            select(GenderKeyword.keyword).where(
                GenderKeyword.chat_id    == chat_id,
                GenderKeyword.gender_type == gender,
            ).order_by(GenderKeyword.keyword)
        )).scalars().all()

    label = "البنات" if gender == "female" else "العيال"
    emoji = _FEMALE_EMOJI if gender == "female" else _MALE_EMOJI
    if not rows:
        await update.effective_message.reply_html(f"{emoji} لا توجد كلمات مضافة لـ{label} بعد.")
        return

    lines = "\n".join(f"• <code>{kw}</code>" for kw in rows)
    await update.effective_message.reply_html(
        f"{emoji} <b>كلمات {label}:</b>\n{lines}"
    )


async def _handle_list_kw_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_keywords(update, context, "female")


async def _handle_list_kw_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_keywords(update, context, "male")


# ── معالجات المشرفين — ردود ─────────────────────────────────────────────────

async def _add_response(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str, prefix: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id  = update.effective_chat.id
    response = _response_text_from(update.effective_message.text or "", prefix)
    if not response:
        await update.effective_message.reply_html("✏️ اكتب نص الرد بعد الأمر.")
        return

    async with get_session() as session:
        if not await session.get(Chat, chat_id):
            session.add(Chat(id=chat_id))
            await session.flush()
        session.add(GenderResponse(chat_id=chat_id, gender_type=gender, response_text=response))
        await session.commit()

    label = "البنات" if gender == "female" else "العيال"
    emoji = _FEMALE_EMOJI if gender == "female" else _MALE_EMOJI
    await update.effective_message.reply_html(
        f"{emoji} تمت إضافة رد جديد لـ{label}:\n<i>{response}</i>"
    )


async def _handle_add_resp_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_response(update, context, "female", "اضف رد للبنات")


async def _handle_add_resp_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_response(update, context, "male", "اضف رد للعيال")


async def _delete_response(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str, prefix: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id  = update.effective_chat.id
    response = _response_text_from(update.effective_message.text or "", prefix)
    if not response:
        await update.effective_message.reply_html("✏️ اكتب نص الرد الذي تريد حذفه.")
        return

    async with get_session() as session:
        result = await session.execute(
            delete(GenderResponse).where(
                GenderResponse.chat_id    == chat_id,
                GenderResponse.gender_type == gender,
                GenderResponse.response_text == response,
            )
        )
        await session.commit()

    label = "البنات" if gender == "female" else "العيال"
    if result.rowcount:
        await update.effective_message.reply_html(f"✅ تم حذف الرد من قائمة {label}.")
    else:
        await update.effective_message.reply_html(f"⚠️ الرد غير موجود في قائمة {label}.")


async def _handle_del_resp_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_response(update, context, "female", "حذف رد للبنات")


async def _handle_del_resp_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _delete_response(update, context, "male", "حذف رد للعيال")


async def _clear_responses(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    async with get_session() as session:
        result = await session.execute(
            delete(GenderResponse).where(
                GenderResponse.chat_id    == chat_id,
                GenderResponse.gender_type == gender,
            )
        )
        await session.commit()

    label = "البنات" if gender == "female" else "العيال"
    await update.effective_message.reply_html(
        f"🗑 تم مسح <b>{result.rowcount}</b> رد من قائمة {label}."
    )


async def _handle_clear_resp_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _clear_responses(update, context, "female")


async def _handle_clear_resp_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _clear_responses(update, context, "male")


async def _list_responses(update: Update, context: ContextTypes.DEFAULT_TYPE, gender: str) -> None:
    if not _is_admin(update):
        await update.effective_message.reply_html("🔒 هذا الأمر للمشرفين فقط.")
        return
    chat_id = update.effective_chat.id
    async with get_session() as session:
        rows = (await session.execute(
            select(GenderResponse.id, GenderResponse.response_text).where(
                GenderResponse.chat_id    == chat_id,
                GenderResponse.gender_type == gender,
            ).order_by(GenderResponse.id)
        )).all()

    label = "البنات" if gender == "female" else "العيال"
    emoji = _FEMALE_EMOJI if gender == "female" else _MALE_EMOJI
    if not rows:
        await update.effective_message.reply_html(f"{emoji} لا توجد ردود مضافة لـ{label} بعد.")
        return

    lines = "\n".join(f"<b>{i+1}.</b> {row.response_text}" for i, row in enumerate(rows))
    await update.effective_message.reply_html(
        f"{emoji} <b>ردود {label}:</b>\n\n{lines}"
    )


async def _handle_list_resp_female(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_responses(update, context, "female")


async def _handle_list_resp_male(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_responses(update, context, "male")


# ── معالج تفعيل الكلمات المفتاحية تلقائياً ─────────────────────────────────

async def _handle_keyword_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    يفحص كل رسالة في المجموعة ويرد تلقائياً إذا تطابقت مع كلمة مفتاحية.
    يختار البوت رداً عشوائياً من قائمة ردود الجنس المقابل للكلمة.
    """
    msg     = update.effective_message
    text    = (msg.text or msg.caption or "").strip().lower()
    chat_id = update.effective_chat.id

    if not text:
        return

    async with get_session() as session:
        all_keywords = (await session.execute(
            select(GenderKeyword.keyword, GenderKeyword.gender_type).where(
                GenderKeyword.chat_id == chat_id
            )
        )).all()

        if not all_keywords:
            return

        matched_gender: Optional[str] = None
        for kw, g_type in all_keywords:
            pattern = r"(^|\s|[^\w])" + re.escape(kw) + r"($|\s|[^\w])"
            if re.search(pattern, text, re.IGNORECASE):
                matched_gender = g_type
                break

        if not matched_gender:
            return

        responses = (await session.execute(
            select(GenderResponse.response_text).where(
                GenderResponse.chat_id    == chat_id,
                GenderResponse.gender_type == matched_gender,
            )
        )).scalars().all()

    if not responses:
        return

    reply_text = random.choice(responses)
    try:
        await msg.reply_html(reply_text)
    except Exception as exc:
        log.warning("gender: failed to send response: %s", exc)


# ── جدول التوجيه النصي ──────────────────────────────────────────────────────

def _text_starts(prefix: str):
    """فلتر: الرسالة تبدأ بالنص المحدد (غير حساس لحالة الأحرف)."""
    return filters.TEXT & filters.Regex(re.compile(r"^" + re.escape(prefix), re.IGNORECASE))


_ROUTES: list[tuple] = [
    # أوامر الأعضاء
    ("تحديد جنسي",        _handle_set_gender),
    ("حذف جنسي",          _handle_delete_gender),
    ("جنسي",              _handle_my_gender),
    ("جنسه",              _handle_his_gender),

    # إحصاء (مشرف)
    ("عدد البنات",         _handle_count_female),
    ("عدد العيال",         _handle_count_male),

    # كلمات مفتاحية (مشرف)
    ("اضف كلمات بنات",    _handle_add_kw_female),
    ("اضف كلمات عيال",    _handle_add_kw_male),
    ("حذف كلمات البنات",  _handle_del_all_kw_female),
    ("حذف كلمات العيال",  _handle_del_all_kw_male),
    ("حذف كلمه بنات",     _handle_del_one_kw_female),
    ("حذف كلمه عيال",     _handle_del_one_kw_male),
    ("كلمات البنات",       _handle_list_kw_female),
    ("كلمات العيال",       _handle_list_kw_male),

    # ردود (مشرف)
    ("اضف رد للبنات",     _handle_add_resp_female),
    ("اضف رد للعيال",     _handle_add_resp_male),
    ("حذف رد للبنات",     _handle_del_resp_female),
    ("حذف رد للعيال",     _handle_del_resp_male),
    ("مسح ردود البنات",   _handle_clear_resp_female),
    ("مسح ردود العيال",   _handle_clear_resp_male),
    ("ردود البنات",        _handle_list_resp_female),
    ("ردود العيال",        _handle_list_resp_male),
]


# ── نقطة التسجيل ────────────────────────────────────────────────────────────

async def register(application: Application) -> None:
    """تسجيل جميع معالجات ميزة الجنس."""

    # 1. معالجات الأوامر النصية — أولوية عالية (group=5)
    for prefix, handler_fn in _ROUTES:
        application.add_handler(
            MessageHandler(
                _text_starts(prefix),
                handler_fn,
            ),
            group=5,
        )

    # 2. معالج الكلمات المفتاحية التلقائية — أولوية منخفضة (group=15)
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS,
            _handle_keyword_trigger,
        ),
        group=15,
    )

    log.info("gender: plugin registered (%d text routes + keyword trigger)", len(_ROUTES))
