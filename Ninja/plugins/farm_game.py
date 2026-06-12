"""
plugins/farm_game.py — Complete Farm Game 🌾

Shares the global wallet system with the Castle game (Wallet.coins).
Gold used here is purely wallet currency — unrelated to Castle gold.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commands:
  /create_farm          — Create your farm
  /my_farm              — View your farm status and plots
  /farm_shop            — View seeds and prices
  /plant <crop> <num>   — Plant a crop in a plot
  /plant_all <crop>     — Plant one crop in all empty plots
  /harvest              — Harvest all ripe crops
  /my_harvest           — View crop inventory
  /sell <crop> <qty>    — Sell a crop for coins
  /sell_all             — Sell all crops in inventory
  /upgrade_farm         — Upgrade the farm (adds new plots)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Crop Table:
  Wheat   (wheat)  — 5 coins/plant   | 30 mins   | Sells for 15  coins
  Barley  (barley) — 8 coins/plant   | 45 mins   | Sells for 25  coins
  Tomato  (tomato) — 15 coins/plant  | 90 mins   | Sells for 50  coins
  Apple   (apple)  — 30 coins/plant  | 3 hours   | Sells for 110 coins
  Grape   (grape)  — 50 coins/plant  | 6 hours   | Sells for 200 coins

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Farm Levels:
  Level 1 — 3 plots  (Free)
  Level 2 — 5 plots  (200 coins)
  Level 3 — 8 plots  (500 coins)
  Level 4 — 12 plots (1000 coins)
  Level 5 — 16 plots (2000 coins)
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
# Game Constants
# ---------------------------------------------------------------------------

# {crop_key: (name, emoji, plant_cost, grow_minutes, sell_price)}
CROPS: dict[str, tuple[str, str, int, int, int]] = {
    "wheat":  ("Wheat",   "🌾",  5,  30,  15),
    "barley": ("Barley",  "🌿",  8,  45,  25),
    "tomato": ("Tomato",  "🍅", 15,  90,  50),
    "apple":  ("Apple",   "🍎", 30, 180, 110),
    "grape":  ("Grape",   "🍇", 50, 360, 200),
}

CROP_ALIASES: dict[str, str] = {
    "wheat": "wheat", "wheat_ar": "wheat",
    "barley": "barley", "barley_ar": "barley",
    "tomato": "tomato", "tomato_ar": "tomato",
    "apple": "apple", "apple_ar": "apple",
    "grape": "grape", "grape_ar": "grape",
}

# {current_level: (plots_count, upgrade_cost)} — plots_count after upgrade
FARM_LEVELS: dict[int, tuple[int, int]] = {
    1: (3,    0),     # Initial — Free
    2: (5,  200),
    3: (8,  500),
    4: (12, 1000),
    5: (16, 2000),
}
MAX_FARM_LEVEL = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crop_name(key: str) -> str:
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
        return "Ready ✅"
    h, rem = divmod(int(diff.total_seconds()), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _render_plots(plots: list[FarmPlot]) -> str:
    lines = []
    for p in plots:
        if not p.crop:
            lines.append(f"  [{p.plot_number}] 🟫 Empty")
        elif p.can_harvest:
            lines.append(f"  [{p.plot_number}] {_crop_emoji(p.crop.value)} {_crop_name(p.crop.value)} — ✅ Ready to harvest")
        else:
            lines.append(
                f"  [{p.plot_number}] {_crop_emoji(p.crop.value)} {_crop_name(p.crop.value)}"
                f" — ⏳ {_time_remaining(p.ready_at)}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_create_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        existing = await _get_farm(session, user.id, chat_id)
        if existing:
            plots = await _get_plots(session, existing.id)
            await update.message.reply_text(
                f"🌾 Your farm already exists!\n"
                f"Level: {existing.level} | Plots: {len(plots)}\n"
                f"Use /my_farm to see details."
            )
            return

        farm = Farm(user_id=user.id, chat_id=chat_id, level=1)
        session.add(farm)
        await session.flush()

        num_plots = _plots_for_level(1)
        for i in range(1, num_plots + 1):
            session.add(FarmPlot(farm_id=farm.id, plot_number=i))

    await update.message.reply_text(
        f"🌾 <b>Your farm has been created!</b>\n\n"
        f"You have <b>{num_plots} plots</b> ready for planting.\n\n"
        f"💡 Start with /farm_shop to see seeds\n"
        f"Then: /plant wheat 1  (plant wheat in plot #1)"
    )


async def cmd_my_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        plots  = await _get_plots(session, farm.id)
        wallet = await get_wallet(session, user.id)

        ready_count = sum(1 for p in plots if p.can_harvest)
        empty_count = sum(1 for p in plots if not p.crop)

        upgrade_line = ""
        if farm.level < MAX_FARM_LEVEL:
            next_cost = FARM_LEVELS[farm.level + 1][1]
            upgrade_line = f"\n⬆️ Upgrade to Level {farm.level+1}: {next_cost} coins — /upgrade_farm"

        text = (
            f"🌾 <b>{user.first_name}'s Farm</b>\n"
            f"{'━'*28}\n"
            f"📊 Level: <b>{farm.level}</b> | Plots: <b>{len(plots)}</b>\n"
            f"💰 Balance: <b>{wallet.coins:,} coins</b>\n\n"
            f"✅ Ready to harvest: {ready_count}  |  🟫 Empty: {empty_count}\n\n"
            f"<b>Plot Status:</b>\n"
            f"{_render_plots(plots)}"
            f"{upgrade_line}"
        )
    await update.message.reply_text(text)


async def cmd_farm_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "🌱 <b>Seed Shop</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "Crop          | Cost  | Time   | Sell Price",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for key, (name, em, cost, mins, sell) in CROPS.items():
        h, m = divmod(mins, 60)
        time_str = f"{h}h {m}m" if h else f"{m}m"
        lines.append(f"{em} {name:10} ({key:6}) | {cost:5} | {time_str:6} | {sell} coins")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "📌 Planting: <code>/plant wheat 1</code>",
        "📌 Plant All: <code>/plant_all tomato</code>",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_plant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 Usage: <code>/plant &lt;crop&gt; &lt;plot_num&gt;</code>\n"
            "Example: <code>/plant tomato 2</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop) or (raw_crop if raw_crop in CROPS else None)
    if not crop_key:
        await update.message.reply_text("❌ Unknown crop. Use /farm_shop to see options.")
        return

    try:
        plot_num = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Plot number must be a number.")
        return

    name, emoji, cost, grow_mins, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        plots = await _get_plots(session, farm.id)
        max_plot = len(plots)
        if plot_num < 1 or plot_num > max_plot:
            await update.message.reply_text(
                f"❌ Plot number must be between 1 and {max_plot}.\n"
                f"Your farm has {max_plot} plots."
            )
            return

        plot = next((p for p in plots if p.plot_number == plot_num), None)
        if not plot:
            await update.message.reply_text(f"❌ Plot {plot_num} not found.")
            return

        if plot.crop:
            status = "✅ Ready to harvest" if plot.can_harvest else f"⏳ {_time_remaining(plot.ready_at)}"
            await update.message.reply_text(
                f"🌱 Plot [{plot_num}] is occupied by {_crop_emoji(plot.crop.value)} {_crop_name(plot.crop.value)}\n"
                f"Status: {status}\n"
                f"Harvest first with /harvest"
            )
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance.\n"
                f"Planting cost: {cost} coins | You have: {w.coins} coins"
            )
            return

        now = _utcnow()
        plot.crop      = CropType(crop_key)
        plot.planted_at = now
        plot.ready_at   = now + timedelta(minutes=grow_mins)
        plot.is_ready   = False
        remaining_coins = wallet.coins

    h, m = divmod(grow_mins, 60)
    time_str = f"{h} hours and {m} minutes" if h and m else (f"{h} hours" if h else f"{m} minutes")
    await update.message.reply_text(
        f"{emoji} <b>Planted!</b>\n\n"
        f"Plot [{plot_num}]: {name}\n"
        f"⏳ Matures in: <b>{time_str}</b>\n"
        f"💰 Remaining balance: {remaining_coins:,} coins\n\n"
        f"Use /harvest when the crop is ready."
    )


async def cmd_plant_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plant the same crop in all empty plots."""
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args:
        await update.message.reply_text(
            "📌 Usage: <code>/plant_all &lt;crop&gt;</code>\n"
            "Example: <code>/plant_all wheat</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop) or (raw_crop if raw_crop in CROPS else None)
    if not crop_key:
        await update.message.reply_text("❌ Unknown crop. Use /farm_shop to see options.")
        return

    name, emoji, cost, grow_mins, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        plots  = await _get_plots(session, farm.id)
        empty  = [p for p in plots if not p.crop]

        if not empty:
            await update.message.reply_text(
                "🌱 All your plots are currently occupied!\n"
                "Harvest first with /harvest"
            )
            return

        total_cost = cost * len(empty)
        wallet = await deduct_coins(session, user.id, total_cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            can_afford = w.coins // cost
            await update.message.reply_text(
                f"❌ Insufficient balance to plant {len(empty)} plots!\n"
                f"Required: {total_cost} coins | You have: {w.coins} coins\n\n"
                f"You can only afford {can_afford} plots — use /plant"
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
    time_str = f"{h}h {m}m" if h else f"{m}m"
    await update.message.reply_text(
        f"{emoji} <b>Planted {len(empty)} plots of {name}!</b>\n\n"
        f"Total Cost: {total_cost} coins\n"
        f"⏳ Matures in: <b>{time_str}</b>\n"
        f"💰 Remaining balance: {remaining_coins:,} coins"
    )


async def cmd_harvest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        plots = await _get_plots(session, farm.id)
        ready = [p for p in plots if p.can_harvest]

        if not ready:
            not_ready = [p for p in plots if p.crop and not p.can_harvest]
            if not_ready:
                soonest = min(p.ready_at for p in not_ready)
                await update.message.reply_text(
                    f"⏳ No crops are ready yet.\n"
                    f"Next harvest in: <b>{_time_remaining(soonest)}</b>"
                )
            else:
                await update.message.reply_text("🟫 All your plots are empty — plant first with /plant")
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

    lines = [f"🌾 <b>Harvest complete!</b>\n"]
    for key, count in harvested.items():
        name, em, _, _, sell = CROPS[key]
        lines.append(f"  {em} {name}: <b>{count}</b> units (sells for {sell * count} coins)")

    lines.append("\n💡 Selling crops: /sell_all  or  /sell wheat 5")
    await update.message.reply_text("\n".join(lines))


async def cmd_my_harvest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return
        inv = await _get_or_create_inventory(session, user.id, chat_id)

    total_value = 0
    lines = [f"🏪 <b>{user.first_name}'s Inventory</b>\n━━━━━━━━━━━━━━━━"]
    for key, (name, em, _, _, sell) in CROPS.items():
        qty = getattr(inv, key)
        val = qty * sell
        total_value += val
        mark = f"  — sells for {val} coins" if qty > 0 else ""
        lines.append(f"{em} {name}: <b>{qty}</b>{mark}")

    lines.append(f"\n💰 Total Value: <b>{total_value} coins</b>")
    lines.append("🛒 Sell All: /sell_all")
    await update.message.reply_text("\n".join(lines))


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 Usage: <code>/sell &lt;crop&gt; &lt;qty&gt;</code>\n"
            "Example: <code>/sell tomato 3</code>"
        )
        return

    raw_crop = context.args[0].lower()
    crop_key = CROP_ALIASES.get(raw_crop) or (raw_crop if raw_crop in CROPS else None)
    if not crop_key:
        await update.message.reply_text("❌ Unknown crop. Use /my_harvest to see your inventory.")
        return

    try:
        qty = int(context.args[1])
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Quantity must be a positive number.")
        return

    name, emoji, _, _, sell_price = CROPS[crop_key]

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        inv = await _get_or_create_inventory(session, user.id, chat_id)
        available = getattr(inv, crop_key)

        if available < qty:
            await update.message.reply_text(
                f"❌ You only have {available} {emoji} {name} in inventory.\n"
                f"Harvest more with /harvest"
            )
            return

        total_earned = qty * sell_price
        setattr(inv, crop_key, available - qty)
        wallet = await add_coins(session, user.id, total_earned)
        new_balance = wallet.coins

    await update.message.reply_text(
        f"🛒 <b>Sale complete!</b>\n\n"
        f"{emoji} {name}: <b>{qty}</b> units × {sell_price} = <b>{total_earned} coins</b>\n"
        f"💰 New balance: <b>{new_balance:,} coins</b>"
    )


async def cmd_sell_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        inv   = await _get_or_create_inventory(session, user.id, chat_id)
        total = 0
        sold_lines = []

        for key, (name, em, _, _, sell) in CROPS.items():
            qty = getattr(inv, key)
            if qty > 0:
                earned = qty * sell
                total += earned
                sold_lines.append(f"  {em} {name}: {qty} × {sell} = {earned} coins")
                setattr(inv, key, 0)

        if total == 0:
            await update.message.reply_text(
                "🏪 Your inventory is empty!\n"
                "Harvest your crops first with /harvest"
            )
            return

        wallet = await add_coins(session, user.id, total)
        new_balance = wallet.coins

    await update.message.reply_text(
        f"🛒 <b>All crops sold!</b>\n\n"
        + "\n".join(sold_lines) +
        f"\n\n💰 Total earned: <b>{total} coins</b>\n"
        f"💰 New balance: <b>{new_balance:,} coins</b>"
    )


async def cmd_upgrade_farm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        farm = await _get_farm(session, user.id, chat_id)
        if not farm:
            await update.message.reply_text("🌾 Create your farm first with /create_farm")
            return

        if farm.level >= MAX_FARM_LEVEL:
            await update.message.reply_text("✅ Your farm is already at the maximum level!")
            return

        next_level = farm.level + 1
        num_plots, cost = FARM_LEVELS[next_level]

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance.\n"
                f"Upgrade cost: {cost} coins | You have: {w.coins} coins"
            )
            return

        farm.level = next_level
        # Add new plots
        current_plots = await _get_plots(session, farm.id)
        for i in range(len(current_plots) + 1, num_plots + 1):
            session.add(FarmPlot(farm_id=farm.id, plot_number=i))

    await update.message.reply_text(
        f"⬆️ <b>Farm Upgraded!</b>\n\n"
        f"New Level: <b>{next_level}</b>\n"
        f"Total Plots: <b>{num_plots}</b>\n"
        f"💰 Remaining balance: {wallet.coins:,} coins"
    )


# ---------------------------------------------------------------------------
# Register
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
