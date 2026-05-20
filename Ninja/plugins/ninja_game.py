"""
plugins/ninja_game.py — لعبة النينجا 🥷 (اغتيال + اختطاف)

الأوامر:
  /my_ninja        — عرض ملفك الشخصي ومستواك
  /assassinate     — محاولة اغتيال لاعب آخر (رد على رسالته)
  /kidnap          — خطف لاعب (رد على رسالته)
  /pay_ransom      — دفع الفدية للإفراج عن نفسك أو آخر
  /rescue          — إنقاذ مخطوف (رد على رسالته)
  /escape_kidnap   — محاولة الهرب وحدك (خطر!)
  /ninja_rank      — أفضل 10 نينجا في المجموعة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
مستويات النينجا (بناءً على XP):
  🥷 مبتدئ    0   XP
  ⚔️ متدرب   50   XP
  🗡 نينجا   150  XP
  🌑 الظل    350  XP
  🔥 ماستر   700  XP
  💀 أسطورة 1200  XP

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
نقاط XP:
  قتل ناجح    → +15 XP
  خطف ناجح   → +25 XP
  إنقاذ ناجح → +20 XP
  هرب ناجح   → +10 XP

الصحة:
  كل لاعب لديه ❤️ × 3 (تتجدد 1 كل 6 ساعات)
  الموت (الصحة = 0) يمنع من الاغتيال لحين التجدد
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.game_wallet import add_coins, deduct_coins, get_wallet
from database.engine import get_session
from database.ninja_models import (
    LEVEL_ORDER,
    LEVEL_XP,
    KidnapRecord,
    KidnapStatus,
    NinjaLevel,
    NinjaProfile,
    xp_to_level,
    xp_to_next,
)

logger = logging.getLogger(__name__)

_utcnow = lambda: datetime.now(tz=timezone.utc)

# ---------------------------------------------------------------------------
# ثوابت اللعبة
# ---------------------------------------------------------------------------
MAX_HEALTH          = 3
HEALTH_REGEN_HOURS  = 6       # ساعات بين كل تجديد صحة

ASSASSINATE_CD_MIN  = 60      # cooldown الاغتيال (دقيقة)
KIDNAP_CD_MIN       = 180     # cooldown الخطف (دقيقة)
KIDNAP_DURATION_H   = 4       # مدة الاختطاف بالساعات
RESCUE_CD_MIN       = 90      # cooldown الإنقاذ (دقيقة)
ESCAPE_CD_MIN       = 120     # cooldown الهرب

XP_KILL    = 15
XP_KIDNAP  = 25
XP_RESCUE  = 20
XP_ESCAPE  = 10
XP_SURVIVE = 5   # XP للضحية التي نجت من الاغتيال

BASE_RANSOM = 40  # عملات أساسية للفدية
MAX_RANSOM  = 120

HEARTS = ["❤️", "🧡", "💛"]     # ألوان الصحة


# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def _heart_bar(health: int) -> str:
    filled   = "❤️" * max(0, health)
    empty    = "🖤" * max(0, MAX_HEALTH - health)
    return filled + empty


def _xp_bar(xp: int) -> str:
    level = xp_to_level(xp)
    idx   = LEVEL_ORDER.index(level)
    if idx == len(LEVEL_ORDER) - 1:
        return "██████ (MAX)"
    next_xp = LEVEL_XP[LEVEL_ORDER[idx + 1]]
    curr_xp = LEVEL_XP[level]
    progress = (xp - curr_xp) / max(1, next_xp - curr_xp)
    filled   = int(progress * 10)
    return "█" * filled + "░" * (10 - filled) + f" {xp}/{next_xp}"


async def _get_profile(session, user_id: int, chat_id: int) -> Optional[NinjaProfile]:
    r = await session.execute(
        select(NinjaProfile).where(
            NinjaProfile.user_id == user_id,
            NinjaProfile.chat_id == chat_id,
        )
    )
    return r.scalar_one_or_none()


async def _get_or_create_profile(
    session, user_id: int, chat_id: int,
    first_name: str = "", username: Optional[str] = None,
) -> NinjaProfile:
    profile = await _get_profile(session, user_id, chat_id)
    if profile is None:
        profile = NinjaProfile(
            user_id    = user_id,
            chat_id    = chat_id,
            first_name = first_name,
            username   = username,
        )
        session.add(profile)
        await session.flush()
    else:
        profile.first_name = first_name or profile.first_name
        profile.username   = username
    return profile


async def _get_active_kidnap_as_victim(session, user_id: int, chat_id: int) -> Optional[KidnapRecord]:
    r = await session.execute(
        select(KidnapRecord).where(
            KidnapRecord.victim_id == user_id,
            KidnapRecord.chat_id   == chat_id,
            KidnapRecord.status    == KidnapStatus.ACTIVE,
        )
    )
    return r.scalar_one_or_none()


def _regen_health(profile: NinjaProfile) -> bool:
    """تجديد الصحة إن انقضى الوقت اللازم. يُعيد True إن تغيّرت الصحة."""
    if profile.health >= MAX_HEALTH:
        return False
    now = _utcnow()
    last = profile.last_health_regen or profile.created_at
    hours_passed = (now - last).total_seconds() / 3600
    to_add = int(hours_passed // HEALTH_REGEN_HOURS)
    if to_add <= 0:
        return False
    profile.health = min(MAX_HEALTH, profile.health + to_add)
    profile.last_health_regen = now
    return True


def _attack_roll(attacker: NinjaProfile, defender: NinjaProfile) -> tuple[int, int]:
    """عشوائية المعركة. يعيد (نقاط المهاجم، نقاط المدافع)."""
    ap = attacker.level.power + random.randint(0, 30)
    dp = defender.level.power + random.randint(0, 25)
    return ap, dp


def _ransom_amount(kidnapper_level: NinjaLevel) -> int:
    idx    = LEVEL_ORDER.index(kidnapper_level)
    amount = BASE_RANSOM + idx * 15
    return min(amount, MAX_RANSOM)


def _cd_remaining_text(bot_data: dict, key: str, cooldown_min: int) -> Optional[str]:
    last: Optional[datetime] = bot_data.get(key)
    if not last:
        return None
    elapsed = (_utcnow() - last).total_seconds()
    if elapsed >= cooldown_min * 60:
        return None
    remaining_m = int((cooldown_min * 60 - elapsed) / 60)
    return f"{remaining_m} دقيقة"


def _add_xp(profile: NinjaProfile, amount: int) -> bool:
    """يضيف XP ويحدّث المستوى. يُعيد True إن ارتقى المستوى."""
    old_level = profile.level
    profile.xp += amount
    new_level  = xp_to_level(profile.xp)
    profile.level = new_level
    return new_level != old_level


# ---------------------------------------------------------------------------
# الأوامر
# ---------------------------------------------------------------------------

async def cmd_my_ninja(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        profile = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        _regen_health(profile)
        kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)

    xp_remaining, next_threshold = xp_to_next(profile.xp)
    kidnap_line = ""
    if kidnap:
        remaining = kidnap.expires_at - _utcnow() if kidnap.expires_at else timedelta(0)
        m = int(remaining.total_seconds() / 60) if remaining.total_seconds() > 0 else 0
        kidnap_line = (
            f"\n🔒 <b>مخطوف!</b> الفدية: {kidnap.ransom_coins} عملة\n"
            f"يُفرج عنك تلقائياً بعد {m} دقيقة إن لم تُدفع الفدية."
        )

    level_line = (
        "🏆 أعلى مستوى! — لا يوجد ما هو أعلى"
        if profile.level == NinjaLevel.LEGEND
        else f"📈 XP للمستوى التالي: {xp_remaining}"
    )

    await update.message.reply_text(
        f"🥷 <b>ملف النينجا — {user.first_name}</b>\n"
        f"{'━'*26}\n"
        f"🎖 المستوى: <b>{profile.level.arabic}</b>\n"
        f"⚡ XP: <b>{profile.xp}</b>   {_xp_bar(profile.xp)}\n"
        f"💗 الصحة: {_heart_bar(profile.health)}\n\n"
        f"🗡 اغتيالات ناجحة: <b>{profile.kills}</b>\n"
        f"💀 وفيات:          <b>{profile.deaths}</b>\n"
        f"🔗 عمليات خطف:    <b>{profile.kidnaps_done}</b>\n"
        f"🦸 إنقاذات:        <b>{profile.rescues}</b>\n"
        f"🏃 هروبات ناجحة:   <b>{profile.kidnaps_survived}</b>\n"
        f"❌ هجمات فاشلة:   <b>{profile.failed_attacks}</b>\n\n"
        f"{level_line}"
        f"{kidnap_line}"
    )


async def cmd_assassinate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رد على رسالة الهدف وأرسل /assassinate"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🗡 ردّ على رسالة الهدف واستخدم /assassinate")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤦 لا يمكنك اغتيال نفسك!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 البوتات لا تموت!")
        return

    # فحص cooldown
    cd_key = f"ninja_atk_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, ASSASSINATE_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ يجب الانتظار {wait} قبل محاولة اغتيال أخرى.")
        return

    async with get_session() as session:
        attacker = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        defender = await _get_or_create_profile(
            session, target.id, chat_id, target.first_name, target.username
        )
        _regen_health(attacker)
        _regen_health(defender)

        # فحص صحة المهاجم
        if attacker.health <= 0:
            hours_needed = HEALTH_REGEN_HOURS
            await msg.reply_text(
                f"💔 أنت مُنهَك ولا تستطيع القتال!\n"
                f"صحتك تتجدد كل {hours_needed} ساعات — استرح أولاً."
            )
            return

        # فحص اختطاف المهاجم
        my_kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if my_kidnap:
            await msg.reply_text(
                "🔒 أنت مخطوف ولا يمكنك القتال!\n"
                f"ادفع الفدية ({my_kidnap.ransom_coins} عملة) بـ /pay_ransom أو انتظر انقضاء المدة."
            )
            return

        # المعركة
        a_roll, d_roll = _attack_roll(attacker, defender)
        attacker_won   = a_roll > d_roll

        if attacker_won:
            # نجاح الاغتيال
            defender.health = max(0, defender.health - 1)
            attacker.kills += 1
            leveled_up = _add_xp(attacker, XP_KILL)

            if defender.health <= 0:
                defender.deaths += 1
                death_line = f"💀 <b>{target.first_name} لقي حتفه!</b> صحته = 0 — ينتظر التجدد."
                defender.last_health_regen = _utcnow()
            else:
                death_line = f"❤️ صحة {target.first_name} المتبقية: {_heart_bar(defender.health)}"

            level_line = f"\n🆙 <b>ارتقيت للمستوى {attacker.level.arabic}!</b>" if leveled_up else ""
            result = (
                f"🗡 <b>اغتيال ناجح!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} ({a_roll}) ضرب {target.first_name} ({d_roll})\n\n"
                f"{death_line}\n"
                f"⚡ حصلت على {XP_KILL} XP — إجمالي: {attacker.xp}"
                f"{level_line}"
            )
        else:
            # فشل الهجوم — المهاجم يخسر صحة
            attacker.health = max(0, attacker.health - 1)
            attacker.failed_attacks += 1
            _add_xp(defender, XP_SURVIVE)

            if attacker.health <= 0:
                attacker.deaths += 1
                attacker_line = f"💀 {user.first_name} لقي حتفه أثناء الهجوم!"
            else:
                attacker_line = f"❤️ صحتك المتبقية: {_heart_bar(attacker.health)}"

            result = (
                f"🛡 <b>الهجوم فشل!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} ({a_roll}) خُذل أمام {target.first_name} ({d_roll})\n\n"
                f"{attacker_line}\n"
                f"⚡ {target.first_name} ربح {XP_SURVIVE} XP للدفاع"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result)


async def cmd_kidnap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رد على رسالة الهدف واستخدم /kidnap"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🔗 ردّ على رسالة الهدف واستخدم /kidnap")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤦 لا يمكنك خطف نفسك!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 لا يمكن خطف البوتات!")
        return

    # فحص cooldown
    cd_key = f"ninja_kd_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, KIDNAP_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ لا يمكنك الخطف مجدداً قبل {wait}.")
        return

    async with get_session() as session:
        kidnapper = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        victim = await _get_or_create_profile(
            session, target.id, chat_id, target.first_name, target.username
        )
        _regen_health(kidnapper)

        # فحص أن المهاجم ليس مخطوفاً
        my_kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if my_kidnap:
            await msg.reply_text("🔒 أنت مخطوف أصلاً! لا يمكنك خطف أحد الآن.")
            return

        # فحص صحة الخاطف
        if kidnapper.health <= 0:
            await msg.reply_text("💔 أنت مُنهَك ولا تستطيع الخطف! استرح حتى تتجدد صحتك.")
            return

        # فحص أن الضحية ليست مخطوفة بالفعل
        existing = await _get_active_kidnap_as_victim(session, target.id, chat_id)
        if existing:
            await msg.reply_text(
                f"🔒 {target.first_name} مخطوف بالفعل!\n"
                f"الخاطف: {existing.kidnapper_name}"
            )
            return

        # عشوائية الخطف
        k_roll = kidnapper.level.power + random.randint(0, 35)
        v_roll = victim.level.power   + random.randint(0, 25)

        if k_roll > v_roll:
            # نجاح الخطف
            ransom = _ransom_amount(kidnapper.level)
            now    = _utcnow()
            record = KidnapRecord(
                chat_id        = chat_id,
                kidnapper_id   = user.id,
                kidnapper_name = user.first_name,
                victim_id      = target.id,
                victim_name    = target.first_name,
                ransom_coins   = ransom,
                status         = KidnapStatus.ACTIVE,
                kidnapped_at   = now,
                expires_at     = now + timedelta(hours=KIDNAP_DURATION_H),
            )
            session.add(record)
            victim.is_kidnapped = True
            kidnapper.kidnaps_done += 1
            leveled_up = _add_xp(kidnapper, XP_KIDNAP)

            level_line = f"\n🆙 <b>ارتقيت للمستوى {kidnapper.level.arabic}!</b>" if leveled_up else ""
            result = (
                f"🔗 <b>تمت عملية الخطف!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} خطف {target.first_name}!\n\n"
                f"💰 الفدية: <b>{ransom} عملة</b>\n"
                f"⏳ يُفرج عنه تلقائياً بعد {KIDNAP_DURATION_H} ساعات\n\n"
                f"للإفراج: /pay_ransom\n"
                f"للإنقاذ (بقوة): ردّ على رسالة المخطوف وأرسل /rescue"
                f"{level_line}"
            )
        else:
            # فشل الخطف
            kidnapper.health = max(0, kidnapper.health - 1)
            kidnapper.failed_attacks += 1
            if kidnapper.health <= 0:
                kidnapper.deaths += 1
                fail_line = f"💀 {user.first_name} قُبض عليه وفقد وعيه أثناء المحاولة!"
            else:
                fail_line = f"❤️ صحتك المتبقية: {_heart_bar(kidnapper.health)}"

            result = (
                f"🛡 <b>فشلت محاولة الخطف!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{target.first_name} انتبه وأفشل المخطط ({k_roll} vs {v_roll})\n\n"
                f"{fail_line}"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result)


async def cmd_pay_ransom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pay_ransom          — تحرير نفسك
    /pay_ransom @username — تحرير شخص آخر
    """
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        # هل الدافع يفدي نفسه أم آخر؟
        kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)

        if not kidnap:
            # تحقق من وجود مخطوف في المجموعة يريد تحريره
            await msg.reply_text(
                "ℹ️ لست مخطوفاً.\n"
                "لتحرير شخص مخطوف، ردّ على رسالته واستخدم /rescue"
            )
            return

        wallet = await deduct_coins(session, user.id, kidnap.ransom_coins)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await msg.reply_text(
                f"💸 رصيدك غير كافٍ لدفع الفدية!\n"
                f"الفدية: {kidnap.ransom_coins} عملة | رصيدك: {w.coins} عملة\n\n"
                f"طرق أخرى: /escape_kidnap (خطر) أو انتظر {KIDNAP_DURATION_H} ساعات."
            )
            return

        kidnap.status      = KidnapStatus.RANSOMED
        kidnap.released_at = _utcnow()

        # تحديث ملف الضحية
        victim_profile = await _get_profile(session, user.id, chat_id)
        if victim_profile:
            victim_profile.is_kidnapped = False

        # إضافة الفدية للخاطف
        await add_coins(session, kidnap.kidnapper_id, kidnap.ransom_coins)

        remaining = wallet.coins

    await msg.reply_text(
        f"💰 <b>تم دفع الفدية!</b>\n\n"
        f"دفعت {kidnap.ransom_coins} عملة للخاطف {kidnap.kidnapper_name}\n"
        f"🔓 أنت حر الآن!\n"
        f"💰 رصيدك المتبقي: {remaining} عملة"
    )


async def cmd_rescue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ردّ على رسالة المخطوف واستخدم /rescue لمحاولة إنقاذه"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🦸 ردّ على رسالة المخطوف واستخدم /rescue")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤔 لإنقاذ نفسك استخدم /escape_kidnap")
        return

    cd_key = f"ninja_res_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, RESCUE_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ يجب الانتظار {wait} قبل محاولة إنقاذ أخرى.")
        return

    async with get_session() as session:
        kidnap = await _get_active_kidnap_as_victim(session, target.id, chat_id)
        if not kidnap:
            await msg.reply_text(f"ℹ️ {target.first_name} ليس مخطوفاً.")
            return

        rescuer = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        _regen_health(rescuer)

        if rescuer.health <= 0:
            await msg.reply_text("💔 أنت مُنهَك ولا تستطيع الإنقاذ! استرح أولاً.")
            return

        # عشوائية الإنقاذ
        r_roll = rescuer.level.power + random.randint(0, 40)
        g_roll = LEVEL_ORDER.index(NinjaLevel.TRAINEE) * 20 + random.randint(20, 50)

        if r_roll > g_roll:
            # إنقاذ ناجح
            kidnap.status      = KidnapStatus.RESCUED
            kidnap.released_at = _utcnow()

            victim_profile = await _get_profile(session, target.id, chat_id)
            if victim_profile:
                victim_profile.is_kidnapped = False

            rescuer.rescues += 1
            leveled_up = _add_xp(rescuer, XP_RESCUE)

            level_line = f"\n🆙 <b>ارتقيت للمستوى {rescuer.level.arabic}!</b>" if leveled_up else ""
            result = (
                f"🦸 <b>إنقاذ ناجح!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{user.first_name} أنقذ {target.first_name} من قبضة {kidnap.kidnapper_name}!\n\n"
                f"⚡ حصلت على {XP_RESCUE} XP — إجمالي: {rescuer.xp}"
                f"{level_line}"
            )
        else:
            # فشل الإنقاذ
            rescuer.health = max(0, rescuer.health - 1)
            rescuer.failed_attacks += 1
            if rescuer.health <= 0:
                rescuer.deaths += 1
                fail_line = f"💀 {user.first_name} قُبض عليه أثناء محاولة الإنقاذ!"
            else:
                fail_line = f"❤️ صحتك المتبقية: {_heart_bar(rescuer.health)}"

            result = (
                f"❌ <b>فشل الإنقاذ!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{user.first_name} لم يتمكن من تحرير {target.first_name}!\n\n"
                f"{fail_line}"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result)


async def cmd_escape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """محاولة الهرب من الاختطاف بمفردك — خطر لكن بدون فدية!"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return

    cd_key = f"ninja_esc_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, ESCAPE_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ محاولة الهرب تحتاج شحن — انتظر {wait}.")
        return

    async with get_session() as session:
        kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if not kidnap:
            await msg.reply_text("ℹ️ لست مخطوفاً — لا حاجة للهرب!")
            return

        profile = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        _regen_health(profile)

        # 50% فرصة هرب، تزيد مع المستوى
        idx          = LEVEL_ORDER.index(profile.level)
        escape_chance = 40 + idx * 10   # 40%→40%→50%→60%→70%→80%→90%
        escaped       = random.randint(1, 100) <= escape_chance

        if escaped:
            kidnap.status      = KidnapStatus.ESCAPED
            kidnap.released_at = _utcnow()
            profile.is_kidnapped = False
            profile.kidnaps_survived += 1
            leveled_up = _add_xp(profile, XP_ESCAPE)

            level_line = f"\n🆙 <b>ارتقيت للمستوى {profile.level.arabic}!</b>" if leveled_up else ""
            result = (
                f"🏃 <b>هربت بنجاح!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"فررت من قبضة {kidnap.kidnapper_name}!\n\n"
                f"⚡ +{XP_ESCAPE} XP — إجمالي: {profile.xp}"
                f"{level_line}"
            )
        else:
            # فشل الهرب — تُضاف أيام للاختطاف
            profile.health = max(0, profile.health - 1)
            if kidnap.expires_at:
                kidnap.expires_at += timedelta(hours=2)

            if profile.health <= 0:
                profile.deaths += 1
                fail_line = "💀 أُصبت وسقطت!"
            else:
                fail_line = f"❤️ صحتك المتبقية: {_heart_bar(profile.health)}"

            result = (
                f"❌ <b>فشلت محاولة الهرب!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"قُبض عليك وأُضيفت ساعتان لمدة الاختطاف!\n\n"
                f"{fail_line}"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result)


async def cmd_ninja_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("🥷 هذه اللعبة تعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        r = await session.execute(
            select(NinjaProfile)
            .where(NinjaProfile.chat_id == chat_id)
            .order_by(NinjaProfile.xp.desc())
            .limit(10)
        )
        top = list(r.scalars().all())

    if not top:
        await update.message.reply_text("🥷 لا يوجد نينجا في هذه المجموعة بعد!\nاستخدم /assassinate أو /kidnap للبدء.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 <b>قائمة النينجا</b>\n━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(top):
        tag  = f"@{p.username}" if p.username else p.first_name
        med  = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{med} {tag} — {p.level.arabic}\n"
            f"    ⚡ {p.xp} XP | 🗡 {p.kills} قتل | 🔗 {p.kidnaps_done} خطف"
        )

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# تسجيل الإضافة
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("my_ninja",      cmd_my_ninja))
    application.add_handler(CommandHandler("assassinate",   cmd_assassinate))
    application.add_handler(CommandHandler("kidnap",        cmd_kidnap))
    application.add_handler(CommandHandler("pay_ransom",    cmd_pay_ransom))
    application.add_handler(CommandHandler("rescue",        cmd_rescue))
    application.add_handler(CommandHandler("escape_kidnap", cmd_escape))
    application.add_handler(CommandHandler("ninja_rank",    cmd_ninja_rank))
    logger.info("ninja_game plugin registered — 7 commands.")
