"""
plugins/castle_game.py — لعبة مملكة القلاع الكاملة.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الأوامر العامة:
  /create_castle        — إنشاء قلعة جديدة
  /my_castle            — عرض تفاصيل قلعتك
  /resource_shop        — متجر الموارد وأسعارها
  /buy_resource <م> <ك> — شراء موارد (wood/stone/food/gold)
  /my_resources         — مستودع مواردك
  /upgrade_castle       — تطوير القلعة (يرفع المستوى)

الجيش:
  /create_barracks      — إنشاء المعسكر
  /buy_army <عدد>       — شراء جنود (max 500 لكل عملية)
  /upgrade_army         — تحويل الجنود لنقاط قوة

التنقيب والحصانة:
  /dig                  — التنقيب عن الكنز (كل ساعتين)
  /immunity             — تفعيل / تعطيل الحصانة
  /my_immunity          — مدة الحصانة المتبقية

المبارزات:
  /duel                 — مبارزة (بالرد على مستخدم)
  /join_battle          — الانضمام للمعركة الكبرى
  /fighters             — عرض المشاركين في المعركة الحالية
  /top_rulers           — قائمة الحكام الفائزين

التحالف:
  /alliance @مستخدم    — إرسال طلب تحالف للغارة
  /alliance_requests    — عرض طلبات التحالف الواردة

(للمشرفين فقط):
  /start_battle <دقائق> — بدء معركة كبرى جديدة
  /end_battle           — إنهاء المعركة وإعلان الفائز

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الذهب داخل اللعبة (CastleResources.gold) ≠ عملات المحفظة (Wallet.coins)
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from database.engine import get_session
from database.game_models import (
    AllianceRequest,
    AllianceStatus,
    Barracks,
    BattleParticipant,
    Castle,
    CastleResources,
    GlobalBattle,
    ImmunityCard,
    RulerTitle,
    TreasureHunt,
)
from core.game_wallet import add_coins, deduct_coins, get_wallet

logger = logging.getLogger(__name__)

_utcnow = lambda: datetime.now(tz=timezone.utc)

# ---------------------------------------------------------------------------
# ثوابت اللعبة
# ---------------------------------------------------------------------------

# سعر الموارد بالعملات (coins من المحفظة)
# 3 وحدات من الخشب/الحجر/الطعام = عملة واحدة
# 1 وحدة ذهب القلعة = عملة واحدة
RESOURCE_PRICES: dict[str, tuple[int, int, str]] = {
    # key: (units_per_coin, max_per_purchase, arabic_name)
    "wood":  (3, 60, "خشب 🪵"),
    "stone": (3, 60, "حجر 🪨"),
    "food":  (3, 60, "طعام 🌾"),
    "gold":  (1, 25, "ذهب 🏅"),
}

RESOURCE_ALIASES: dict[str, str] = {
    "wood": "wood", "خشب": "wood",
    "stone": "stone", "حجر": "stone",
    "food": "food", "طعام": "food",
    "gold": "gold", "ذهب": "gold",
}

# متطلبات تطوير القلعة لكل مستوى {current_level: (wood, stone, food, gold, cooldown_minutes)}
UPGRADE_REQUIREMENTS: dict[int, tuple[int, int, int, int, int]] = {
    1:  (15,  10,   0,   0,  30),
    2:  (25,  20,  10,   5,  45),
    3:  (40,  30,  20,  10,  60),
    4:  (60,  50,  30,  15,  90),
    5:  (80,  70,  50,  20, 120),
    6:  (110, 90,  80,  30, 180),
    7:  (140, 120, 100, 40, 240),
    8:  (170, 150, 130, 50, 360),
    9:  (210, 200, 160, 65, 480),
}

MAX_CASTLE_LEVEL = 10
SOLDIERS_PER_PURCHASE = 100   # وحدة الشراء
ARMY_PURCHASE_COST   = 5      # coins لكل 100 جندي
MAX_SOLDIERS_PER_BUY = 500    # أقصى عدد جنود لكل عملية شراء
SOLDIERS_PER_POWER   = 1000   # جندي = نقطة قوة واحدة

DIG_COOLDOWN_HOURS = 2
IMMUNITY_DURATION  = timedelta(hours=24)

DUEL_WIN_REWARD    = 20  # عملات للفائز بالمبارزة
BATTLE_WIN_REWARD  = 100 # عملات للفائز بالمعركة الكبرى

# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def _user_tag(update: Update) -> str:
    u = update.effective_user
    return f"@{u.username}" if u.username else u.first_name


async def _get_castle(session, user_id: int, chat_id: int) -> Optional[Castle]:
    r = await session.execute(
        select(Castle).where(Castle.user_id == user_id, Castle.chat_id == chat_id)
    )
    return r.scalar_one_or_none()


async def _get_or_create_resources(session, user_id: int, chat_id: int) -> CastleResources:
    r = await session.execute(
        select(CastleResources).where(
            CastleResources.user_id == user_id,
            CastleResources.chat_id == chat_id,
        )
    )
    res = r.scalar_one_or_none()
    if res is None:
        res = CastleResources(user_id=user_id, chat_id=chat_id)
        session.add(res)
        await session.flush()
    return res


async def _get_barracks(session, user_id: int, chat_id: int) -> Optional[Barracks]:
    r = await session.execute(
        select(Barracks).where(Barracks.user_id == user_id, Barracks.chat_id == chat_id)
    )
    return r.scalar_one_or_none()


async def _get_immunity(session, user_id: int, chat_id: int) -> Optional[ImmunityCard]:
    r = await session.execute(
        select(ImmunityCard).where(
            ImmunityCard.user_id == user_id,
            ImmunityCard.chat_id == chat_id,
        )
    )
    return r.scalar_one_or_none()


async def _get_active_battle(session, chat_id: int) -> Optional[GlobalBattle]:
    r = await session.execute(
        select(GlobalBattle).where(
            GlobalBattle.chat_id == chat_id,
            GlobalBattle.is_active == True,
        )
    )
    return r.scalar_one_or_none()


def _level_title(level: int) -> str:
    titles = {
        1: "قرية صغيرة 🏚️",
        2: "بلدة ناشئة 🏘️",
        3: "مدينة متنامية 🏙️",
        4: "حصن محكم 🏰",
        5: "مقاطعة قوية ⚔️",
        6: "إمارة راسخة 🛡️",
        7: "مملكة ممتدة 👑",
        8: "إمبراطورية صاعدة 🌟",
        9: "دولة عظمى 🔱",
        10: "حاكم 🤴",
    }
    return titles.get(level, "مجهول")


# ---------------------------------------------------------------------------
# الأوامر: القلعة الأساسية
# ---------------------------------------------------------------------------

async def cmd_create_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    args = context.args
    castle_name = " ".join(args).strip() if args else f"قلعة {user.first_name}"
    if len(castle_name) > 30:
        await update.message.reply_text("⚠️ اسم القلعة طويل جداً (الحد 30 حرفاً).")
        return

    async with get_session() as session:
        existing = await _get_castle(session, user.id, chat_id)
        if existing:
            await update.message.reply_text(
                f"🏰 لديك قلعة بالفعل: <b>{existing.name}</b>\n"
                f"استخدم /my_castle لعرض تفاصيلها."
            )
            return

        castle = Castle(user_id=user.id, chat_id=chat_id, name=castle_name)
        session.add(castle)
        await _get_or_create_resources(session, user.id, chat_id)

    await update.message.reply_text(
        f"🏰 <b>تم إنشاء قلعتك!</b>\n\n"
        f"الاسم: <b>{castle_name}</b>\n"
        f"المستوى: <b>1 — {_level_title(1)}</b>\n\n"
        f"💡 ابدأ بشراء الموارد عبر /resource_shop\n"
        f"💰 رصيدك: 100 عملة — استخدم /wallet"
    )


async def cmd_my_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text(
                "🏚️ ليس لديك قلعة بعد.\nاستخدم /create_castle لإنشائها."
            )
            return

        res      = await _get_or_create_resources(session, user.id, chat_id)
        barracks = await _get_barracks(session, user.id, chat_id)
        immunity = await _get_immunity(session, user.id, chat_id)
        wallet   = await get_wallet(session, user.id)

        imm_line = "لا توجد حصانة"
        if immunity and immunity.is_active:
            diff = immunity.active_until - _utcnow()
            h, m = divmod(int(diff.total_seconds() / 60), 60)
            imm_line = f"🛡️ محصّن لمدة {h}س {m}د"
        elif immunity and immunity.cards > 0:
            imm_line = f"🛡️ {immunity.cards} بطاقة (غير مفعّلة)"

        upgrade_line = ""
        if castle.level < MAX_CASTLE_LEVEL:
            req = UPGRADE_REQUIREMENTS.get(castle.level)
            if req:
                w, s, f, g, cd = req
                upgrade_line = (
                    f"\n📋 <b>متطلبات التطوير للمستوى {castle.level+1}:</b>\n"
                    f"  خشب {w} | حجر {s} | طعام {f} | ذهب {g}\n"
                    f"  ⏱ انتظار {cd} دقيقة بعد كل تطوير"
                )

        army_line = "لا يوجد معسكر (أنشئه بـ /create_barracks)" if not barracks else (
            f"⚔️ الجيش: {barracks.soldiers:,} جندي | قوة: {barracks.power_level} نقطة"
        )

        text = (
            f"🏰 <b>{castle.name}</b>\n"
            f"{'━' * 28}\n"
            f"📊 المستوى: <b>{castle.level}</b> — {_level_title(castle.level)}\n"
            f"💰 المحفظة: <b>{wallet.coins:,} عملة</b>\n\n"
            f"🪵 خشب: {res.wood}  |  🪨 حجر: {res.stone}\n"
            f"🌾 طعام: {res.food}  |  🏅 ذهب: {res.gold}\n\n"
            f"{army_line}\n"
            f"🔒 الحصانة: {imm_line}"
            f"{upgrade_line}"
        )
    await update.message.reply_text(text)


async def cmd_resource_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛒 <b>متجر الموارد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "الموارد تُشترى بعملات المحفظة 💰\n\n"
        "🪵 <b>خشب</b>   — 3 وحدات / عملة   (حد 60)\n"
        "🪨 <b>حجر</b>   — 3 وحدات / عملة   (حد 60)\n"
        "🌾 <b>طعام</b>  — 3 وحدات / عملة   (حد 60)\n"
        "🏅 <b>ذهب</b>   — 1 وحدة  / عملة   (حد 25)\n\n"
        "📝 طريقة الشراء:\n"
        "<code>/buy_resource wood 30</code>\n"
        "<code>/buy_resource gold 10</code>\n\n"
        "⚠️ الذهب هنا هو مورد القلعة، وليس عملة المحفظة."
    )
    await update.message.reply_text(text)


async def cmd_buy_resource(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 الاستخدام: <code>/buy_resource &lt;مورد&gt; &lt;عدد&gt;</code>\n"
            "مثال: <code>/buy_resource wood 30</code>"
        )
        return

    raw_res = context.args[0].lower()
    res_key = RESOURCE_ALIASES.get(raw_res)
    if not res_key:
        await update.message.reply_text("❌ مورد غير معروف. الأنواع: wood, stone, food, gold")
        return

    try:
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ العدد يجب أن يكون رقماً موجباً.")
        return

    units_per_coin, max_buy, ar_name = RESOURCE_PRICES[res_key]
    if amount > max_buy:
        await update.message.reply_text(
            f"❌ لا يمكن شراء أكثر من {max_buy} وحدة في المرة الواحدة."
        )
        return

    cost = -(-amount // units_per_coin)  # ceiling division

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"💸 رصيدك غير كافٍ!\n"
                f"تحتاج: {cost} عملة | لديك: {w.coins} عملة"
            )
            return

        res = await _get_or_create_resources(session, user.id, chat_id)
        setattr(res, res_key, getattr(res, res_key) + amount)
        remaining_coins = wallet.coins

    await update.message.reply_text(
        f"✅ اشتريت <b>{amount} {ar_name}</b> بـ <b>{cost} عملة</b>\n"
        f"💰 رصيدك المتبقي: {remaining_coins:,} عملة\n"
        f"استخدم /my_resources لعرض مخزونك."
    )


async def cmd_my_resources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return
        res = await _get_or_create_resources(session, user.id, chat_id)

    await update.message.reply_text(
        f"🏪 <b>مستودع {user.first_name}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🪵 خشب:  <b>{res.wood}</b>\n"
        f"🪨 حجر:  <b>{res.stone}</b>\n"
        f"🌾 طعام: <b>{res.food}</b>\n"
        f"🏅 ذهب:  <b>{res.gold}</b>  (مورد القلعة)\n\n"
        f"💡 لشراء المزيد: /resource_shop"
    )


async def cmd_upgrade_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        if castle.level >= MAX_CASTLE_LEVEL:
            await update.message.reply_text(
                f"👑 قلعتك بلغت أعلى مستوى!\n"
                f"أنت <b>{_level_title(MAX_CASTLE_LEVEL)}</b> — لا يوجد تطوير إضافي."
            )
            return

        req = UPGRADE_REQUIREMENTS[castle.level]
        need_w, need_s, need_f, need_g, cooldown_min = req

        # فحص الـ cooldown
        if castle.last_upgraded_at:
            elapsed = _utcnow() - castle.last_upgraded_at
            if elapsed < timedelta(minutes=cooldown_min):
                remaining = timedelta(minutes=cooldown_min) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                m = rem // 60
                await update.message.reply_text(
                    f"⏳ يجب الانتظار {cooldown_min} دقيقة بين كل تطوير.\n"
                    f"الوقت المتبقي: <b>{h}س {m}د</b>"
                )
                return

        res = await _get_or_create_resources(session, user.id, chat_id)

        missing = []
        if res.wood  < need_w: missing.append(f"خشب ({res.wood}/{need_w})")
        if res.stone < need_s: missing.append(f"حجر ({res.stone}/{need_s})")
        if res.food  < need_f: missing.append(f"طعام ({res.food}/{need_f})")
        if res.gold  < need_g: missing.append(f"ذهب ({res.gold}/{need_g})")

        if missing:
            await update.message.reply_text(
                f"❌ <b>موارد غير كافية للتطوير:</b>\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\n\n🛒 اشترِ المزيد من /resource_shop"
            )
            return

        res.wood  -= need_w
        res.stone -= need_s
        res.food  -= need_f
        res.gold  -= need_g
        castle.level += 1
        castle.last_upgraded_at = _utcnow()
        new_level = castle.level

        if new_level == MAX_CASTLE_LEVEL:
            ruler = await session.execute(
                select(RulerTitle).where(
                    RulerTitle.user_id == user.id,
                    RulerTitle.chat_id == chat_id,
                )
            )
            ruler = ruler.scalar_one_or_none()
            if ruler:
                ruler.wins += 1
                ruler.last_win_at = _utcnow()
                ruler.first_name = user.first_name
                ruler.username   = user.username
            else:
                session.add(RulerTitle(
                    user_id=user.id, chat_id=chat_id,
                    first_name=user.first_name, username=user.username,
                ))
            await add_coins(session, user.id, 50)

    if new_level == MAX_CASTLE_LEVEL:
        await update.message.reply_text(
            f"🎉🏆 <b>تهانينا يا {user.first_name}!</b>\n\n"
            f"وصلت قلعتك إلى أعلى مستوى!\n"
            f"لقبك الآن: <b>الحاكم 🤴</b>\n\n"
            f"🎁 مكافأة: +50 عملة أُضيفت لمحفظتك!\n"
            f"📋 شارك في /top_rulers لترى ترتيبك."
        )
    else:
        await update.message.reply_text(
            f"⬆️ <b>تم تطوير القلعة!</b>\n\n"
            f"المستوى الجديد: <b>{new_level}</b> — {_level_title(new_level)}\n\n"
            f"المستوى التالي يحتاج انتظار {UPGRADE_REQUIREMENTS.get(new_level, (0,0,0,0,0))[4]} دقيقة"
            if new_level < MAX_CASTLE_LEVEL else ""
        )


# ---------------------------------------------------------------------------
# الأوامر: الجيش والمعسكر
# ---------------------------------------------------------------------------

async def cmd_create_barracks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        existing = await _get_barracks(session, user.id, chat_id)
        if existing:
            await update.message.reply_text(
                f"⚔️ معسكرك موجود بالفعل!\n"
                f"جنودك: {existing.soldiers:,} | قوتك: {existing.power_level} نقطة\n"
                f"اشترِ جنوداً بـ /buy_army <عدد>"
            )
            return

        session.add(Barracks(user_id=user.id, chat_id=chat_id))

    await update.message.reply_text(
        f"⚔️ <b>تم إنشاء المعسكر!</b>\n\n"
        f"ابدأ بتجنيد جيشك: /buy_army 100\n"
        f"💡 كل 1000 جندي = نقطة قوة واحدة"
    )


async def cmd_buy_army(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args:
        await update.message.reply_text(
            f"📌 الاستخدام: <code>/buy_army &lt;عدد&gt;</code>\n"
            f"مثال: <code>/buy_army 200</code>\n"
            f"التكلفة: {ARMY_PURCHASE_COST} عملة لكل 100 جندي"
        )
        return

    try:
        amount = int(context.args[0])
        if amount <= 0 or amount % SOLDIERS_PER_PURCHASE != 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"❌ العدد يجب أن يكون مضاعفاً لـ {SOLDIERS_PER_PURCHASE} (مثل 100, 200, 300...)"
        )
        return

    if amount > MAX_SOLDIERS_PER_BUY:
        await update.message.reply_text(
            f"❌ لا يمكن شراء أكثر من {MAX_SOLDIERS_PER_BUY} جندي في المرة الواحدة."
        )
        return

    cost = (amount // SOLDIERS_PER_PURCHASE) * ARMY_PURCHASE_COST

    async with get_session() as session:
        barracks = await _get_barracks(session, user.id, chat_id)
        if not barracks:
            await update.message.reply_text("⚔️ أنشئ معسكرك أولاً بـ /create_barracks")
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"💸 رصيدك غير كافٍ!\n"
                f"تحتاج: {cost} عملة | لديك: {w.coins} عملة"
            )
            return

        barracks.soldiers += amount
        remaining_coins = wallet.coins

    await update.message.reply_text(
        f"⚔️ انضمّ <b>{amount:,} جندي</b> لجيشك!\n"
        f"التكلفة: {cost} عملة 💰\n"
        f"رصيدك المتبقي: {remaining_coins:,} عملة\n\n"
        f"💡 استخدم /upgrade_army لتحويل جنودك إلى نقاط قوة"
    )


async def cmd_upgrade_army(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        barracks = await _get_barracks(session, user.id, chat_id)
        if not barracks:
            await update.message.reply_text("⚔️ أنشئ معسكرك أولاً بـ /create_barracks")
            return

        new_power = barracks.soldiers // SOLDIERS_PER_POWER
        old_power = barracks.power_level

        if new_power <= old_power:
            needed = (old_power + 1) * SOLDIERS_PER_POWER
            still_need = needed - barracks.soldiers
            await update.message.reply_text(
                f"ℹ️ جيشك ({barracks.soldiers:,} جندي) = {old_power} نقطة قوة.\n"
                f"لرفع القوة لـ {old_power+1}: تحتاج {still_need:,} جندي إضافي."
            )
            return

        barracks.power_level = new_power

    await update.message.reply_text(
        f"💪 <b>تم تطوير الجيش!</b>\n\n"
        f"نقاط القوة: {old_power} ← <b>{new_power}</b>\n"
        f"جنودك: {barracks.soldiers:,}\n\n"
        f"القوة العسكرية تُستخدم في المبارزات والمعارك الكبرى."
    )


# ---------------------------------------------------------------------------
# التنقيب عن الكنز
# ---------------------------------------------------------------------------

async def cmd_dig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        r = await session.execute(
            select(TreasureHunt).where(
                TreasureHunt.user_id == user.id,
                TreasureHunt.chat_id == chat_id,
            )
        )
        hunt = r.scalar_one_or_none()
        now  = _utcnow()

        if hunt:
            elapsed = now - hunt.last_hunt_at
            if elapsed < timedelta(hours=DIG_COOLDOWN_HOURS):
                remaining = timedelta(hours=DIG_COOLDOWN_HOURS) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                m = rem // 60
                await update.message.reply_text(
                    f"⛏️ أنت تعبان من التنقيب!\n"
                    f"عُد بعد <b>{h} ساعة و{m} دقيقة</b>."
                )
                return

        # اختيار المكافأة عشوائياً
        reward_type = random.choices(
            ["wood", "stone", "food", "gold", "soldiers", "immunity", "coins"],
            weights=[20, 20, 20, 10, 15, 5, 10],
            k=1,
        )[0]

        res      = await _get_or_create_resources(session, user.id, chat_id)
        barracks = await _get_barracks(session, user.id, chat_id)
        reward_text = ""

        if reward_type == "wood":
            amount = random.randint(10, 35)
            res.wood += amount
            reward_text = f"🪵 وجدت <b>{amount} خشب</b>!"
        elif reward_type == "stone":
            amount = random.randint(10, 35)
            res.stone += amount
            reward_text = f"🪨 وجدت <b>{amount} حجر</b>!"
        elif reward_type == "food":
            amount = random.randint(10, 35)
            res.food += amount
            reward_text = f"🌾 وجدت <b>{amount} طعام</b>!"
        elif reward_type == "gold":
            amount = random.randint(3, 10)
            res.gold += amount
            reward_text = f"🏅 وجدت <b>{amount} ذهب</b> (مورد القلعة)!"
        elif reward_type == "soldiers" and barracks:
            amount = random.randint(50, 200)
            barracks.soldiers += amount
            reward_text = f"⚔️ وجدت <b>{amount} جندي</b> ينضمون لجيشك!"
        elif reward_type == "immunity":
            r2 = await session.execute(
                select(ImmunityCard).where(
                    ImmunityCard.user_id == user.id,
                    ImmunityCard.chat_id == chat_id,
                )
            )
            imm = r2.scalar_one_or_none()
            if imm:
                imm.cards += 1
            else:
                session.add(ImmunityCard(user_id=user.id, chat_id=chat_id, cards=1))
            reward_text = f"🛡️ وجدت <b>بطاقة حصانة</b>! استخدمها بـ /immunity"
        elif reward_type == "coins":
            amount = random.randint(5, 20)
            await add_coins(session, user.id, amount)
            reward_text = f"💰 وجدت <b>{amount} عملة</b> أُضيفت لمحفظتك!"
        else:
            amount = random.randint(10, 25)
            res.wood += amount
            reward_text = f"🪵 وجدت <b>{amount} خشب</b>!"

        if hunt:
            hunt.last_hunt_at = now
        else:
            session.add(TreasureHunt(user_id=user.id, chat_id=chat_id, last_hunt_at=now))

    await update.message.reply_text(
        f"⛏️ <b>نتيجة التنقيب...</b>\n\n"
        f"{reward_text}\n\n"
        f"⏰ يمكنك التنقيب مجدداً بعد {DIG_COOLDOWN_HOURS} ساعة."
    )


# ---------------------------------------------------------------------------
# الحصانة
# ---------------------------------------------------------------------------

async def cmd_immunity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        imm = await _get_immunity(session, user.id, chat_id)
        if not imm or imm.cards == 0:
            await update.message.reply_text(
                "🛡️ ليس لديك بطاقات حصانة.\n"
                "ابحث عنها عبر /dig"
            )
            return

        now = _utcnow()
        if imm.is_active:
            diff = imm.active_until - now
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            # إلغاء تفعيل
            imm.active_until = None
            msg = (
                f"🛡️ تم <b>إلغاء تفعيل</b> الحصانة.\n"
                f"كان متبقياً {h}س {m}د — البطاقة لم تُستهلك."
            )
            imm.cards += 1  # إعادة البطاقة
        else:
            # تفعيل
            imm.active_until = now + IMMUNITY_DURATION
            imm.cards -= 1
            msg = (
                f"🛡️ <b>تم تفعيل الحصانة!</b>\n"
                f"أنت محمي لمدة 24 ساعة.\n"
                f"بطاقاتك المتبقية: {imm.cards}"
            )

    await update.message.reply_text(msg)


async def cmd_my_immunity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        imm = await _get_immunity(session, user.id, chat_id)

    if not imm or (imm.cards == 0 and not imm.is_active):
        await update.message.reply_text(
            "🛡️ لا توجد حصانة نشطة ولا بطاقات مجمّعة.\n"
            "ابحث عن بطاقات بـ /dig"
        )
        return

    lines = [f"🛡️ <b>حصانة {user.first_name}</b>\n"]
    if imm.is_active:
        diff = imm.active_until - _utcnow()
        h, rem = divmod(int(diff.total_seconds()), 3600)
        m = rem // 60
        lines.append(f"✅ الحصانة <b>مفعّلة</b> — تنتهي بعد {h}س {m}د")
    else:
        lines.append("⭕ الحصانة غير مفعّلة")

    lines.append(f"🃏 البطاقات المتاحة: <b>{imm.cards}</b>")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# المبارزة (بالرد فقط)
# ---------------------------------------------------------------------------

async def cmd_duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    msg     = update.message
    if update.effective_chat.type == "private":
        await msg.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("⚔️ للمبارزة، ردّ على رسالة خصمك بـ /duel")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("⚔️ لا يمكنك مبارزة نفسك!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 لا يمكنك مبارزة بوت!")
        return

    async with get_session() as session:
        # فحص قلاع الطرفين
        my_castle = await _get_castle(session, user.id, chat_id)
        if not my_castle:
            await msg.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        their_castle = await _get_castle(session, target.id, chat_id)
        if not their_castle:
            await msg.reply_text(f"🏚️ {target.first_name} لا يملك قلعة في هذه المجموعة.")
            return

        # فحص حصانة الهدف
        their_imm = await _get_immunity(session, target.id, chat_id)
        if their_imm and their_imm.is_active:
            diff = their_imm.active_until - _utcnow()
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            await msg.reply_text(
                f"🛡️ {target.first_name} محمي بالحصانة!\n"
                f"تنتهي بعد {h}س {m}د — حاول لاحقاً."
            )
            return

        my_bar     = await _get_barracks(session, user.id, chat_id)
        their_bar  = await _get_barracks(session, target.id, chat_id)
        my_power   = (my_bar.power_level if my_bar else 0) + my_castle.level * 2
        their_power = (their_bar.power_level if their_bar else 0) + their_castle.level * 2

        # عشوائية طفيفة للإثارة
        my_roll    = my_power    + random.randint(0, max(1, my_power // 3))
        their_roll = their_power + random.randint(0, max(1, their_power // 3))

        attacker_won = my_roll >= their_roll

        if attacker_won:
            winner_id, loser_id = user.id, target.id
            winner_name, loser_name = user.first_name, target.first_name
            loser_bar = their_bar
        else:
            winner_id, loser_id = target.id, user.id
            winner_name, loser_name = target.first_name, user.first_name
            loser_bar = my_bar

        # خصم 10% من جنود الخاسر
        soldiers_lost = 0
        if loser_bar and loser_bar.soldiers > 0:
            soldiers_lost = max(10, loser_bar.soldiers // 10)
            loser_bar.soldiers = max(0, loser_bar.soldiers - soldiers_lost)
            loser_bar.power_level = loser_bar.soldiers // SOLDIERS_PER_POWER

        # مكافأة الفائز
        await add_coins(session, winner_id, DUEL_WIN_REWARD)

    emoji = "⚔️" if attacker_won else "🛡️"
    await msg.reply_text(
        f"⚔️ <b>نتيجة المبارزة!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🗡 {user.first_name}: {my_roll} نقطة\n"
        f"🗡 {target.first_name}: {their_roll} نقطة\n\n"
        f"{emoji} الفائز: <b>{winner_name}</b>!\n"
        f"💔 {loser_name} خسر {soldiers_lost:,} جندي\n"
        f"🎁 {winner_name} ربح {DUEL_WIN_REWARD} عملة 💰"
    )


# ---------------------------------------------------------------------------
# المعركة الكبرى
# ---------------------------------------------------------------------------

async def cmd_start_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """للمشرفين فقط — /start_battle <دقائق التسجيل>"""
    user    = update.effective_user
    chat    = update.effective_chat
    chat_id = chat.id
    if chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return

    try:
        mins = int(context.args[0]) if context.args else 10
        if mins < 1 or mins > 60:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("📌 الاستخدام: /start_battle <1-60 دقيقة>")
        return

    async with get_session() as session:
        existing = await _get_active_battle(session, chat_id)
        if existing:
            await update.message.reply_text("⚔️ يوجد معركة جارية بالفعل! استخدم /end_battle لإنهائها.")
            return

        battle = GlobalBattle(
            chat_id=chat_id,
            registration_ends_at=_utcnow() + timedelta(minutes=mins),
        )
        session.add(battle)

    await update.message.reply_text(
        f"⚔️ <b>بدأت المعركة الكبرى!</b>\n\n"
        f"🕐 التسجيل مفتوح لمدة <b>{mins} دقيقة</b>\n"
        f"انضم بـ /join_battle\n\n"
        f"بعد انتهاء التسجيل يُعلن المشرف الفائز بـ /end_battle"
    )


async def cmd_join_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ لا توجد معركة جارية الآن.")
            return

        if _utcnow() > battle.registration_ends_at:
            await update.message.reply_text(
                "⏰ انتهى وقت التسجيل!\n"
                "انتظر المعركة القادمة."
            )
            return

        r = await session.execute(
            select(BattleParticipant).where(
                BattleParticipant.battle_id == battle.id,
                BattleParticipant.user_id   == user.id,
            )
        )
        if r.scalar_one_or_none():
            await update.message.reply_text("✅ أنت مسجّل بالفعل في هذه المعركة!")
            return

        castle  = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        barracks = await _get_barracks(session, user.id, chat_id)
        army_power    = barracks.power_level if barracks else 0
        castle_level  = castle.level
        total_power   = castle_level * 10 + army_power

        session.add(BattleParticipant(
            battle_id   = battle.id,
            user_id     = user.id,
            chat_id     = chat_id,
            first_name  = user.first_name,
            username    = user.username,
            castle_level = castle_level,
            army_power   = army_power,
            total_power  = total_power,
        ))

    await update.message.reply_text(
        f"⚔️ <b>انضممت للمعركة الكبرى!</b>\n\n"
        f"قلعتك: مستوى {castle_level} ({_level_title(castle_level)})\n"
        f"قوتك: {total_power} نقطة (قلعة×10 + جيش)\n\n"
        f"استخدم /fighters لعرض المنافسين."
    )


async def cmd_fighters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ لا توجد معركة جارية الآن.")
            return

        r = await session.execute(
            select(BattleParticipant)
            .where(BattleParticipant.battle_id == battle.id)
            .order_by(BattleParticipant.total_power.desc())
        )
        participants = r.scalars().all()

        if not participants:
            await update.message.reply_text("📭 لم ينضم أحد بعد — استخدم /join_battle")
            return

        now      = _utcnow()
        if now < battle.registration_ends_at:
            diff = battle.registration_ends_at - now
            m = int(diff.total_seconds() / 60)
            time_line = f"⏰ التسجيل ينتهي بعد <b>{m} دقيقة</b>"
        else:
            time_line = "⏰ التسجيل <b>انتهى</b> — انتظر إعلان الفائز"

        lines = [f"⚔️ <b>المعركة الكبرى — {len(participants)} مبارز</b>\n{time_line}\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(participants[:10]):
            tag  = f"@{p.username}" if p.username else p.first_name
            med  = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{med} {tag} — قوة: <b>{p.total_power}</b>")

    await update.message.reply_text("\n".join(lines))


async def cmd_end_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """للمشرفين — إنهاء المعركة وإعلان الفائز."""
    user    = update.effective_user
    chat    = update.effective_chat
    chat_id = chat.id
    if chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ لا توجد معركة جارية الآن.")
            return

        r = await session.execute(
            select(BattleParticipant)
            .where(BattleParticipant.battle_id == battle.id)
            .order_by(BattleParticipant.total_power.desc())
        )
        participants = r.scalars().all()

        if not participants:
            battle.is_active = False
            battle.ended_at  = _utcnow()
            await update.message.reply_text("⚔️ انتهت المعركة — لم يشارك أحد.")
            return

        winner = participants[0]
        battle.is_active = False
        battle.ended_at  = _utcnow()

        # تسجيل اللقب
        r2 = await session.execute(
            select(RulerTitle).where(
                RulerTitle.user_id == winner.user_id,
                RulerTitle.chat_id == chat_id,
            )
        )
        ruler = r2.scalar_one_or_none()
        if ruler:
            ruler.wins += 1
            ruler.last_win_at = _utcnow()
            ruler.first_name  = winner.first_name
            ruler.username    = winner.username
        else:
            session.add(RulerTitle(
                user_id    = winner.user_id,
                chat_id    = chat_id,
                first_name = winner.first_name,
                username   = winner.username,
            ))

        await add_coins(session, winner.user_id, BATTLE_WIN_REWARD)

        # قائمة المراكز
        rank_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(participants[:5]):
            tag = f"@{p.username}" if p.username else p.first_name
            med = medals[i] if i < 3 else f"{i+1}."
            rank_lines.append(f"{med} {tag} — {p.total_power} نقطة")

    winner_tag = f"@{winner.username}" if winner.username else winner.first_name
    await update.message.reply_text(
        f"🏆 <b>انتهت المعركة الكبرى!</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"👑 <b>الحاكم: {winner_tag}</b>\n"
        f"قوة الفائز: {winner.total_power} نقطة\n"
        f"🎁 مكافأة: {BATTLE_WIN_REWARD} عملة 💰\n\n"
        f"<b>ترتيب المبارزين:</b>\n"
        + "\n".join(rank_lines)
    )


# ---------------------------------------------------------------------------
# قائمة الحكام
# ---------------------------------------------------------------------------

async def cmd_top_rulers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        r = await session.execute(
            select(RulerTitle)
            .where(RulerTitle.chat_id == chat_id)
            .order_by(RulerTitle.wins.desc(), RulerTitle.last_win_at.desc())
            .limit(10)
        )
        rulers = r.scalars().all()

    if not rulers:
        await update.message.reply_text(
            "📋 لا يوجد حكام بعد في هذه المجموعة.\n"
            "ابدأ معركة بـ /start_battle لتحديد الحاكم!"
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = ["👑 <b>قائمة الحكام</b>\n"]
    for i, r in enumerate(rulers):
        tag = f"@{r.username}" if r.username else r.first_name
        med = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{med} {tag} — {r.wins} انتصار")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# التحالف والغارات
# ---------------------------------------------------------------------------

async def cmd_alliance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    msg     = update.message
    if update.effective_chat.type == "private":
        await msg.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args:
        await msg.reply_text(
            "📌 الاستخدام: <code>/alliance @مستخدم</code>\n"
            "لإرسال طلب تحالف وتنفيذ غارة مشتركة على هدف من قائمة الحكام."
        )
        return

    target_username = context.args[0].lstrip("@")

    async with get_session() as session:
        my_castle = await _get_castle(session, user.id, chat_id)
        if not my_castle:
            await msg.reply_text("🏚️ أنشئ قلعتك أولاً بـ /create_castle")
            return

        # البحث عن المستخدم عبر الـ username في جدول الحكام
        r = await session.execute(
            select(RulerTitle).where(
                RulerTitle.chat_id == chat_id,
                RulerTitle.username == target_username,
            )
        )
        target_ruler = r.scalar_one_or_none()

        if not target_ruler:
            await msg.reply_text(
                f"❌ لم يُعثر على @{target_username} في هذه المجموعة أو ليس لديه قلعة."
            )
            return

        if target_ruler.user_id == user.id:
            await msg.reply_text("❌ لا يمكنك إرسال طلب تحالف لنفسك.")
            return

        # فحص طلب معلّق مسبق
        r2 = await session.execute(
            select(AllianceRequest).where(
                AllianceRequest.chat_id       == chat_id,
                AllianceRequest.requester_id  == user.id,
                AllianceRequest.target_id     == target_ruler.user_id,
                AllianceRequest.status        == AllianceStatus.PENDING,
            )
        )
        if r2.scalar_one_or_none():
            await msg.reply_text("⏳ طلب التحالف مع هذا الشخص معلّق بالفعل.")
            return

        req = AllianceRequest(
            chat_id        = chat_id,
            requester_id   = user.id,
            requester_name = user.first_name,
            target_id      = target_ruler.user_id,
            target_name    = target_ruler.first_name,
        )
        session.add(req)
        req_id = None  # يُجلب بعد flush
        await session.flush()
        req_id = req.id

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"allianceaccept:{req_id}"),
        InlineKeyboardButton("❌ رفض",  callback_data=f"alliancereject:{req_id}"),
    ]])

    target_tag = f"@{target_username}"
    await msg.reply_text(
        f"🤝 <b>طلب تحالف</b>\n\n"
        f"{target_tag}، يطلب منك <b>{user.first_name}</b> التحالف لتنفيذ غارة مشتركة.\n\n"
        f"هل توافق؟",
        reply_markup=kb,
    )


async def _alliance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user    = query.from_user
    chat_id = query.message.chat_id
    data    = query.data

    await query.answer()

    action, req_id_str = data.split(":")
    req_id = int(req_id_str)

    async with get_session() as session:
        r = await session.execute(
            select(AllianceRequest).where(AllianceRequest.id == req_id)
        )
        req = r.scalar_one_or_none()

        if not req or req.status != AllianceStatus.PENDING:
            await query.edit_message_text("⚠️ هذا الطلب لم يعد صالحاً.")
            return

        if user.id != req.target_id:
            await query.answer("هذا الطلب ليس موجهاً إليك.", show_alert=True)
            return

        if action == "alliancereject":
            req.status = AllianceStatus.REJECTED
            await query.edit_message_text(
                f"❌ رفض {user.first_name} طلب التحالف من {req.requester_name}."
            )
            return

        req.status = AllianceStatus.ACCEPTED

        # حساب قوة التحالف المشتركة
        my_castle = await _get_castle(session, req.requester_id, chat_id)
        ally_castle = await _get_castle(session, req.target_id, chat_id)
        my_bar  = await _get_barracks(session, req.requester_id, chat_id)
        ally_bar = await _get_barracks(session, req.target_id, chat_id)

        combined_power = (
            ((my_castle.level  if my_castle  else 1) * 10 + (my_bar.power_level  if my_bar  else 0)) +
            ((ally_castle.level if ally_castle else 1) * 10 + (ally_bar.power_level if ally_bar else 0))
        )

        # ابحث عن أقوى منافس في قائمة الحكام
        r2 = await session.execute(
            select(RulerTitle)
            .where(
                RulerTitle.chat_id  == chat_id,
                RulerTitle.user_id  != req.requester_id,
                RulerTitle.user_id  != req.target_id,
            )
            .order_by(RulerTitle.wins.desc())
            .limit(1)
        )
        top_ruler = r2.scalar_one_or_none()

        if top_ruler:
            target_castle = await _get_castle(session, top_ruler.user_id, chat_id)
            target_bar    = await _get_barracks(session, top_ruler.user_id, chat_id)
            target_power  = (
                ((target_castle.level if target_castle else 1) * 10) +
                (target_bar.power_level if target_bar else 0)
            )
            raid_roll   = combined_power + random.randint(0, combined_power // 3)
            target_roll = target_power   + random.randint(0, max(1, target_power // 3))

            target_tag = f"@{top_ruler.username}" if top_ruler.username else top_ruler.first_name

            if raid_roll >= target_roll:
                # الغارة ناجحة
                if target_bar and target_bar.soldiers > 0:
                    lost = max(50, target_bar.soldiers // 5)
                    target_bar.soldiers = max(0, target_bar.soldiers - lost)
                    target_bar.power_level = target_bar.soldiers // SOLDIERS_PER_POWER

                await add_coins(session, req.requester_id, 30)
                await add_coins(session, req.target_id, 30)

                result_text = (
                    f"🎉 <b>الغارة نجحت!</b>\n"
                    f"قوة التحالف: {raid_roll} vs {target_roll}\n"
                    f"أضرار {target_tag}: {max(50, (target_bar.soldiers if target_bar else 0)//5 + max(50, 0))} جندي\n"
                    f"🎁 كل من المتحالفين ربح 30 عملة 💰"
                )
            else:
                result_text = (
                    f"💔 <b>الغارة فشلت!</b>\n"
                    f"قوة التحالف: {raid_roll} vs قوة {target_tag}: {target_roll}\n"
                    f"دافع {target_tag} عن قلعته!"
                )
        else:
            result_text = "ℹ️ لا يوجد هدف في قائمة الحكام لتنفيذ الغارة عليه."

    await query.edit_message_text(
        f"🤝 <b>تم قبول التحالف!</b>\n"
        f"{req.requester_name} + {req.target_name}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{result_text}"
    )


async def cmd_alliance_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        r = await session.execute(
            select(AllianceRequest).where(
                AllianceRequest.chat_id    == chat_id,
                AllianceRequest.target_id  == user.id,
                AllianceRequest.status     == AllianceStatus.PENDING,
            )
        )
        reqs = r.scalars().all()

    if not reqs:
        await update.message.reply_text("📭 لا توجد طلبات تحالف معلّقة.")
        return

    lines = [f"🤝 <b>طلبات التحالف الواردة ({len(reqs)})</b>\n"]
    for req in reqs:
        lines.append(f"• {req.requester_name} — /alliance_requests لقبول أو رفض")

    await update.message.reply_text(
        "\n".join(lines) + "\n\n"
        "💡 استخدم الأزرار في رسالة طلب التحالف للموافقة أو الرفض."
    )


# ---------------------------------------------------------------------------
# تسجيل الإضافة
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    # القلعة
    application.add_handler(CommandHandler("create_castle",    cmd_create_castle))
    application.add_handler(CommandHandler("my_castle",        cmd_my_castle))
    application.add_handler(CommandHandler("resource_shop",    cmd_resource_shop))
    application.add_handler(CommandHandler("buy_resource",     cmd_buy_resource))
    application.add_handler(CommandHandler("my_resources",     cmd_my_resources))
    application.add_handler(CommandHandler("upgrade_castle",   cmd_upgrade_castle))
    # الجيش
    application.add_handler(CommandHandler("create_barracks",  cmd_create_barracks))
    application.add_handler(CommandHandler("buy_army",         cmd_buy_army))
    application.add_handler(CommandHandler("upgrade_army",     cmd_upgrade_army))
    # الكنز والحصانة
    application.add_handler(CommandHandler("dig",              cmd_dig))
    application.add_handler(CommandHandler("immunity",         cmd_immunity))
    application.add_handler(CommandHandler("my_immunity",      cmd_my_immunity))
    # المبارزات
    application.add_handler(CommandHandler("duel",             cmd_duel))
    application.add_handler(CommandHandler("start_battle",     cmd_start_battle))
    application.add_handler(CommandHandler("join_battle",      cmd_join_battle))
    application.add_handler(CommandHandler("fighters",         cmd_fighters))
    application.add_handler(CommandHandler("end_battle",       cmd_end_battle))
    application.add_handler(CommandHandler("top_rulers",       cmd_top_rulers))
    # التحالف
    application.add_handler(CommandHandler("alliance",          cmd_alliance))
    application.add_handler(CommandHandler("alliance_requests", cmd_alliance_requests))
    # Callbacks
    application.add_handler(CallbackQueryHandler(_alliance_callback, pattern=r"^alliance(accept|reject):\d+$"))

    logger.info("castle_game plugin registered — %d commands.", 21)
