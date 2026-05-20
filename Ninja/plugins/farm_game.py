"""
plugins/farm_game.py — لعبة المزرعة الكاملة 🌾

تشترك مع لعبة القلاع في نظام المحفظة العامة (Wallet.coins).
الذهب المستخدم هنا هو عملة المحفظة فقط — لا علاقة له بذهب القلعة.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الأوامر:
  /create_farm          — إنشاء مزرعتك
  /my_farm              — عرض حالة مزرعتك وأراضيها
  /farm_shop            — عرض البذور وأسعارها
  /plant <محصول> <رقم>  — زراعة محصول في قطعة أرض
  /plant_all <محصول>    — زراعة جميع الأراضي الفارغة بمحصول واحد
  /harvest              — حصاد كل المحاصيل الناضجة
  /my_harvest           — عرض مخزون المحاصيل
  /sell <محصول> <كمية>  — بيع محصول بالعملات
  /sell_all             — بيع جميع المحاصيل في المخزون
  /upgrade_farm         — تطوير المزرعة (يضيف أراضي جديدة)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
جدول المحاصيل:
  قمح   (wheat)  — 5 عملات/زراعة   | 30 دقيقة  | يُباع بـ 15  عملة
  شعير  (barley) — 8 عملات/زراعة   | 45 دقيقة  | يُباع بـ 25  عملة
  طماطم (tomato) — 15 عملات/زراعة  | 90 دقيقة  | يُباع بـ 50  عملة
  تفاح  (apple)  — 30 عملات/زراعة  | 3 ساعات   | يُباع بـ 110 عملة
  عنب   (grape)  — 50 عملات/زراعة  | 6 ساعات   | يُباع بـ 200 عملة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
مستويات المزرعة:
  مستوى 1 — 3 أراضٍ  (مجاني)
  مستوى 2 — 5 أراضٍ  (200 عملة)
  مستوى 3 — 8 أراضٍ  (500 عملة)
  مستوى 4 — 12 أرضاً (1000 عملة)
  مستوى 5 — 16 أرضاً (2000 عملة)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.game_wallet import add_coins, deduct_coins, get_wallet
from database.engine import get_session
from database.farm_models import CropType, Farm, FarmInventory, FarmPlot

logger = logging.getLogger(__name__)

_utcnow = lambda: datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# ثوابت اللعبة
# ---------------------------------------------------------------------------

# {crop_key: (ar_name, emoji, plant_cost, grow_minutes, sell_price)}
CROPS: dict[str, tuple[str, str, int, int, int]] = {
    "wheat":  ("قمح",   "🌾",  5,  30,  15),
    "barley": ("شعير",  "🌿",  8,  45,  25),
    "tomato": ("طماطم", "🍅", 15,  90,  50),
    "apple":  ("تفاح",  "🍎", 30, 180, 110),
    "grape":  ("عنب",   "🍇", 50, 360, 200),
}

CROP_ALIASES: dict[str, str] = {
    "wheat": "wheat", "قمح": "wheat",
    "barley": "barley", "شعير": "barley",
    "tomato": "tomato", "طماطم": "tomato",
    "apple": "apple", "تفاح": "apple",
    "grape": "grape", "عنب": "grape",
}

# {current_level: (plots_count, upgrade_cost)}  — plots_count بعد التطوير
FARM_LEVELS: dict[int, tuple[int, int]] = {
    1: (3,    0),     # ابتدائي — مجاني
    2: (5,  200),
    3: (8,  500),
    4: (12, 1000),
    5: (16, 2000),
}
MAX_FARM_LEVEL = 5


# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def _crop_ar(key: str) -> str:
    return CROPS[key][0] if key in CROPS else key


def _crop_emoji(key: str) -> str:
    return CROPS[key][1] if key in CROPS else "🌱"


async def _get_farm(session, user_id: int, chat_id: int) -> Optional[Farm]:
    r = await session.execute(
        select(Farm).where(Farm.user_id == user_id, Farm.chat_id == chat_id)
    )
    return r.scalar_one_or_none()


async def _get_plots(session, farm_id: int) -> list[FarmPlot]:
    r = await session.execute(
        select(FarmPlot).where(FarmPlot.farm_id == farm_id).order_by(FarmPlot.plot_number)
    )
    return list(r.scalars().all())


async def _get_or_create_inventory(session, user_id: int, chat_id: int) -> FarmInventory:
    r = await session.execute(
        select(FarmInventory).where(
            FarmInventory.user_id == user_id,
            FarmInventory.chat_id == chat_id,
        )
    )
    inv = r.scalar_one_or_none()
    if inv is None:
        inv = FarmInventory(user_id=user_id, chat_id=chat_id)
        session.add(inv)
        await session.flush()
    return inv


def _plots_for_level(level: int) -> int:
    return FARM_LEVELS.get(level, (3, 0))[0]


def _time_remaining(ready_at: datetime) -> str:
    diff = ready_at - _utcnow()
    if diff.total_seconds() <= 0:
        return "جاهز ✅"
    h, rem = divmod(int(diff.total_seconds()), 3600)
    m = rem // 60
    return f"{h}س {m}د" if h else f"{m}د"


def _render_plots(plots: list[FarmPlot]) -> str:
    lines = []
    for p in plots:
        if not p.crop:
            lines.append(f"  [{p.plot_number}] 🟫 فارغة")
        elif p.can_harvest:
            lines.append(f"  [{p.plot_number}] {_crop_emoji(p.crop.value)} {_crop_ar(p.crop.value)} — ✅ جاهز للحصاد")
        else:
            lines.append(
                f"  [{p.plot_number}] {_crop_emoji(p.crop.value)} {_crop_ar(p.crop.value)}"
                f" — ⏳ {_time_remaining(p.ready_at)}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# الأوامر
# ---------------------------------------------------------------------------

async def cmd_create_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        existing = await _get_farm(session, user.id, chat_id)
        if existing:
            plots = await _get_plots(session, existing.id)
            await update.message.reply_text(
                f"🌾 مزرعتك موجودة بالفعل!\n"
                f"المستوى: {existing.level} | الأراضي: {len(plots)}\n"
                f"استخدم /my_farm لعرض التفاصيل."
            )
            return

        farm = Farm(user_id=user.id, chat_id=chat_id, level=1)
        session.add(farm)
        await session.flush()

        num_plots = _plots_for_level(1)
        for i in range(1, num_plots + 1):
            session.add(FarmPlot(farm_id=farm.id, plot_number=i))

    await update.message.reply_text(
        f"🌾 <b>تم إنشاء مزرعتك!</b>\n\n"
        f"لديك <b>{num_plots} أراضٍ</b> جاهزة للزراعة.\n\n"
        f"💡 ابدأ بـ /farm_shop لمعرفة البذور\n"
        f"ثم: /plant wheat 1  (زرع قمح في الأرض رقم 1)"
    )


async def cmd_my_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        plots  = await _get_plots(session, farm.id)
        wallet = await get_wallet(session, user.id)

        ready_count = sum(1 for p in plots if p.can_harvest)
        empty_count = sum(1 for p in plots if not p.crop)

        upgrade_line = ""
        if farm.level < MAX_FARM_LEVEL:
            next_cost = FARM_LEVELS[farm.level + 1][1]
            upgrade_line = f"\n⬆️ تطوير للمستوى {farm.level+1}: {next_cost} عملة — /upgrade_farm"

        text = (
            f"🌾 <b>مزرعة {user.first_name}</b>\n"
            f"{'━'*28}\n"
            f"📊 المستوى: <b>{farm.level}</b> | الأراضي: <b>{len(plots)}</b>\n"
            f"💰 رصيدك: <b>{wallet.coins:,} عملة</b>\n\n"
            f"✅ جاهزة للحصاد: {ready_count}  |  🟫 فارغة: {empty_count}\n\n"
            f"<b>حالة الأراضي:</b>\n"
            f"{_render_plots(plots)}"
            f"{upgrade_line}"
        )
    await update.message.reply_text(text)


async def cmd_farm_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "🌱 <b>متجر البذور</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "المحصول       | التكلفة | الوقت  | سعر البيع",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for key, (ar, em, cost, mins, sell) in CROPS.items():
        h, m = divmod(mins, 60)
        time_str = f"{h}س {m}د" if h else f"{m}د"
        lines.append(f"{em} {ar:6} ({key:6}) | {cost:5} | {time_str:6} | {sell} عملة")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "📌 الزراعة: <code>/plant wheat 1</code>",
        "📌 زراعة الكل: <code>/plant_all tomato</code>",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_plant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 الاستخدام: <code>/plant &lt;محصول&gt; &lt;رقم الأرض&gt;</code>\n"
            "مثال: <code>/plant tomato 2</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop)
    if not crop_key:
        await update.message.reply_text("❌ محصول غير معروف. استخدم /farm_shop لعرض الخيارات.")
        return

    try:
        plot_num = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ رقم الأرض يجب أن يكون رقماً.")
        return

    ar_name, emoji, cost, grow_mins, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        plots = await _get_plots(session, farm.id)
        max_plot = len(plots)
        if plot_num < 1 or plot_num > max_plot:
            await update.message.reply_text(
                f"❌ رقم الأرض يجب أن يكون بين 1 و {max_plot}.\n"
                f"مزرعتك تحتوي على {max_plot} أراضٍ."
            )
            return

        plot = next((p for p in plots if p.plot_number == plot_num), None)
        if plot and plot.crop:
            status = "✅ جاهز للحصاد" if plot.can_harvest else f"⏳ {_time_remaining(plot.ready_at)}"
            await update.message.reply_text(
                f"🌱 الأرض [{plot_num}] مشغولة بـ {_crop_emoji(plot.crop.value)} {_crop_ar(plot.crop.value)}\n"
                f"الحالة: {status}\n"
                f"احصد أولاً بـ /harvest"
            )
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"💸 رصيدك غير كافٍ!\n"
                f"تكلفة الزراعة: {cost} عملة | لديك: {w.coins} عملة"
            )
            return

        now = _utcnow()
        plot.crop      = CropType(crop_key)
        plot.planted_at = now
        plot.ready_at   = now + timedelta(minutes=grow_mins)
        plot.is_ready   = False
        remaining_coins = wallet.coins

    h, m = divmod(grow_mins, 60)
    time_str = f"{h} ساعة و{m} دقيقة" if h and m else (f"{h} ساعة" if h else f"{m} دقيقة")
    await update.message.reply_text(
        f"{emoji} <b>تمت الزراعة!</b>\n\n"
        f"الأرض [{plot_num}]: {ar_name}\n"
        f"⏳ تنضج بعد: <b>{time_str}</b>\n"
        f"💰 رصيدك المتبقي: {remaining_coins:,} عملة\n\n"
        f"استخدم /harvest عند نضج المحصول."
    )


async def cmd_plant_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """زرع جميع الأراضي الفارغة بنفس المحصول."""
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args:
        await update.message.reply_text(
            "📌 الاستخدام: <code>/plant_all &lt;محصول&gt;</code>\n"
            "مثال: <code>/plant_all wheat</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop)
    if not crop_key:
        await update.message.reply_text("❌ محصول غير معروف. استخدم /farm_shop لعرض الخيارات.")
        return

    ar_name, emoji, cost, grow_mins, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        plots  = await _get_plots(session, farm.id)
        empty  = [p for p in plots if not p.crop]

        if not empty:
            await update.message.reply_text(
                "🌱 جميع أراضيك مشغولة بالفعل!\n"
                "احصد أولاً بـ /harvest"
            )
            return

        total_cost = cost * len(empty)
        wallet = await deduct_coins(session, user.id, total_cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            can_afford = w.coins // cost
            await update.message.reply_text(
                f"💸 رصيدك غير كافٍ لزراعة {len(empty)} أراضٍ!\n"
                f"المطلوب: {total_cost} عملة | لديك: {w.coins} عملة\n\n"
                f"يمكنك زراعة {can_afford} أرض فقط — استخدم /plant"
            )
            return

        now = _utcnow()
        ready_at = now + timedelta(minutes=grow_mins)
        for p in empty:
            p.crop       = CropType(crop_key)
            p.planted_at = now
            p.ready_at   = ready_at
            p.is_ready   = False

        remaining_coins = wallet.coins

    h, m = divmod(grow_mins, 60)
    time_str = f"{h}س {m}د" if h else f"{m}د"
    await update.message.reply_text(
        f"{emoji} <b>تمت زراعة {len(empty)} أراضٍ بـ {ar_name}!</b>\n\n"
        f"التكلفة الإجمالية: {total_cost} عملة\n"
        f"⏳ تنضج بعد: <b>{time_str}</b>\n"
        f"💰 رصيدك المتبقي: {remaining_coins:,} عملة"
    )


async def cmd_harvest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        plots = await _get_plots(session, farm.id)
        ready = [p for p in plots if p.can_harvest]

        if not ready:
            not_ready = [p for p in plots if p.crop and not p.can_harvest]
            if not_ready:
                soonest = min(p.ready_at for p in not_ready)
                await update.message.reply_text(
                    f"⏳ لا توجد محاصيل ناضجة بعد.\n"
                    f"أقرب حصاد بعد: <b>{_time_remaining(soonest)}</b>"
                )
            else:
                await update.message.reply_text("🟫 جميع أراضيك فارغة — ازرع أولاً بـ /plant")
            return

        inv = await _get_or_create_inventory(session, user.id, chat_id)
        harvested: dict[str, int] = {}

        for p in ready:
            key = p.crop.value
            harvested[key] = harvested.get(key, 0) + 1
            setattr(inv, key, getattr(inv, key) + 1)
            p.crop       = None
            p.planted_at = None
            p.ready_at   = None
            p.is_ready   = False

    lines = [f"🌾 <b>تم الحصاد!</b>\n"]
    for key, count in harvested.items():
        ar, em, _, _, sell = CROPS[key]
        lines.append(f"  {em} {ar}: <b>{count}</b> وحدة (تُباع بـ {sell * count} عملة)")

    lines.append("\n💡 بيع المحصول: /sell_all  أو  /sell wheat 5")
    await update.message.reply_text("\n".join(lines))


async def cmd_my_harvest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return
        inv = await _get_or_create_inventory(session, user.id, chat_id)

    total_value = 0
    lines = [f"🏪 <b>مخزون {user.first_name}</b>\n━━━━━━━━━━━━━━━━"]
    for key, (ar, em, _, _, sell) in CROPS.items():
        qty = getattr(inv, key)
        val = qty * sell
        total_value += val
        mark = f"  — يُباع بـ {val} عملة" if qty > 0 else ""
        lines.append(f"{em} {ar}: <b>{qty}</b>{mark}")

    lines.append(f"\n💰 القيمة الإجمالية: <b>{total_value} عملة</b>")
    lines.append("🛒 بيع الكل: /sell_all")
    await update.message.reply_text("\n".join(lines))


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 الاستخدام: <code>/sell &lt;محصول&gt; &lt;كمية&gt;</code>\n"
            "مثال: <code>/sell tomato 3</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop)
    if not crop_key:
        await update.message.reply_text("❌ محصول غير معروف. استخدم /my_harvest لعرض مخزونك.")
        return

    try:
        qty = int(context.args[1])
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ الكمية يجب أن تكون رقماً موجباً.")
        return

    ar_name, emoji, _, _, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        inv = await _get_or_create_inventory(session, user.id, chat_id)
        available = getattr(inv, crop_key)

        if available < qty:
            await update.message.reply_text(
                f"❌ لديك {available} {emoji} {ar_name} فقط في المخزون.\n"
                f"احصد المزيد بـ /harvest"
            )
            return

        total_earned = qty * sell_price
        setattr(inv, crop_key, available - qty)
        wallet = await add_coins(session, user.id, total_earned)
        new_balance = wallet.coins

    await update.message.reply_text(
        f"🛒 <b>تم البيع!</b>\n\n"
        f"{emoji} {ar_name}: <b>{qty}</b> وحدة × {sell_price} = <b>{total_earned} عملة</b>\n"
        f"💰 رصيدك الجديد: <b>{new_balance:,} عملة</b>"
    )


async def cmd_sell_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        inv   = await _get_or_create_inventory(session, user.id, chat_id)
        total = 0
        sold_lines = []

        for key, (ar, em, _, _, sell) in CROPS.items():
            qty = getattr(inv, key)
            if qty > 0:
                earned = qty * sell
                total += earned
                sold_lines.append(f"  {em} {ar}: {qty} × {sell} = {earned} عملة")
                setattr(inv, key, 0)

        if total == 0:
            await update.message.reply_text(
                "🏪 مخزونك فارغ!\n"
                "احصد محاصيلك أولاً بـ /harvest"
            )
            return

        wallet = await add_coins(session, user.id, total)
        new_balance = wallet.coins

    await update.message.reply_text(
        f"🛒 <b>تم بيع جميع المحاصيل!</b>\n\n"
        + "\n".join(sold_lines) +
        f"\n\n💰 إجمالي المكتسب: <b>{total} عملة</b>\n"
        f"رصيدك الجديد: <b>{new_balance:,} عملة</b>"
    )


async def cmd_upgrade_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 أنشئ مزرعتك أولاً بـ /create_farm")
            return

        if farm.level >= MAX_FARM_LEVEL:
            await update.message.reply_text(
                f"🌟 مزرعتك بلغت أعلى مستوى ({MAX_FARM_LEVEL})!\n"
                f"لديك {_plots_for_level(MAX_FARM_LEVEL)} أرضاً — لا يوجد تطوير إضافي."
            )
            return

        next_level = farm.level + 1
        new_plots_total, upgrade_cost = FARM_LEVELS[next_level]
        current_plots_total = _plots_for_level(farm.level)
        plots_to_add = new_plots_total - current_plots_total

        wallet = await deduct_coins(session, user.id, upgrade_cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"💸 رصيدك غير كافٍ!\n"
                f"تكلفة التطوير: {upgrade_cost} عملة | لديك: {w.coins} عملة"
            )
            return

        farm.level = next_level
        plots = await _get_plots(session, farm.id)
        last_num = max((p.plot_number for p in plots), default=0)
        for i in range(1, plots_to_add + 1):
            session.add(FarmPlot(farm_id=farm.id, plot_number=last_num + i))

        remaining_coins = wallet.coins

    await update.message.reply_text(
        f"⬆️ <b>تمت ترقية المزرعة!</b>\n\n"
        f"المستوى الجديد: <b>{next_level}</b>\n"
        f"أراضٍ جديدة: <b>+{plots_to_add}</b> (الإجمالي: {new_plots_total})\n"
        f"التكلفة: {upgrade_cost} عملة\n"
        f"💰 رصيدك المتبقي: {remaining_coins:,} عملة"
    )


# ---------------------------------------------------------------------------
# تسجيل الإضافة
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    application.add_handler(CommandHandler("create_farm",  cmd_create_farm))
    application.add_handler(CommandHandler("my_farm",      cmd_my_farm))
    application.add_handler(CommandHandler("farm_shop",    cmd_farm_shop))
    application.add_handler(CommandHandler("plant",        cmd_plant))
    application.add_handler(CommandHandler("plant_all",    cmd_plant_all))
    application.add_handler(CommandHandler("harvest",      cmd_harvest))
    application.add_handler(CommandHandler("my_harvest",   cmd_my_harvest))
    application.add_handler(CommandHandler("sell",         cmd_sell))
    application.add_handler(CommandHandler("sell_all",     cmd_sell_all))
    application.add_handler(CommandHandler("upgrade_farm", cmd_upgrade_farm))
    logger.info("farm_game plugin registered — 10 commands.")
