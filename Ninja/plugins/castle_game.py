"""
plugins/castle_game.py — Complete Castle Kingdom game.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
General Commands:
  /create_castle        — Create a new castle
  /my_castle            — View your castle details
  /resource_shop        — Resource shop and prices
  /buy_resource <type> <amount> — Buy resources (wood/stone/food/gold)
  /my_resources         — Your resource warehouse
  /upgrade_castle       — Upgrade castle (increases level)

Army:
  /create_barracks      — Create barracks
  /buy_army <count>       — Buy soldiers (max 500 per process)
  /upgrade_army         — Convert soldiers to power points

Mining and Immunity:
  /dig                  — Dig for treasure (every 2 hours)
  /immunity             — Enable / Disable immunity
  /my_immunity          — Remaining immunity duration

Duels:
  /duel                 — Duel (by replying to a user)
  /join_battle          — Join the grand battle
  /fighters             — View participants in the current battle
  /top_rulers           — List of winning rulers

Alliance:
  /alliance @user    — Send alliance request for raid
  /alliance_requests    — View incoming alliance requests

(Admins only):
  /start_battle <minutes> — Start a new grand battle
  /end_battle           — End the battle and announce the winner

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
In-game gold (CastleResources.gold) ≠ Wallet coins (Wallet.coins)
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
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
# Constants
# ---------------------------------------------------------------------------

# Resource prices in coins (Wallet.coins)
# 3 units of wood/stone/food = 1 coin
# 1 unit of Castle gold = 1 coin
RESOURCE_PRICES: dict[str, tuple[int, int, str]] = {
    # key: (units_per_coin, max_per_purchase, english_name)
    "wood":  (3, 60, "wood 🪵"),
    "stone": (3, 60, "stone 🪨"),
    "food":  (3, 60, "food 🌾"),
    "gold":  (1, 25, "gold 🏅"),
}

RESOURCE_ALIASES: dict[str, str] = {
    "wood":  "wood",
    "stone": "stone",
    "food":  "food",
    "gold":  "gold",
}

# Castle upgrade requirements per level {current_level: (wood, stone, food, gold, cooldown_minutes)}
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
SOLDIERS_PER_PURCHASE = 100   # Purchase unit
ARMY_PURCHASE_COST   = 5      # coins per 100 soldiers
MAX_SOLDIERS_PER_BUY = 500    # Max soldiers per purchase
SOLDIERS_PER_POWER   = 1000   # soldiers = 1 Power Point

DIG_COOLDOWN_HOURS = 2
IMMUNITY_DURATION  = timedelta(hours=24)

DUEL_COOLDOWN_MIN  = 30   # Minutes between duels with same opponent
DUEL_WIN_REWARD    = 20   # Coins for duel winner
BATTLE_WIN_REWARD  = 100  # Coins for great battle winner
GOLD_TO_COINS_RATE = 100  # 1 castle gold = 100 wallet coins

# ---------------------------------------------------------------------------
# Helpers
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
        1: "Small Village 🏚️",
        2: "Rising Town 🏘️",
        3: "Growing City 🏙️",
        4: "Secure Fortress 🏰",
        5: "Strong County ⚔️",
        6: "Established Principality 🛡️",
        7: "Extended Kingdom 👑",
        8: "Rising Empire 🌟",
        9: "Superpower 🔱",
        10: "Ruler 🤴",
    }
    return titles.get(level, "Unknown")


# ---------------------------------------------------------------------------
# Commands: Base Castle
# ---------------------------------------------------------------------------

async def cmd_create_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works in groups.")
        return

    args = context.args
    castle_name = " ".join(args).strip() if args else f"{user.first_name}'s Castle"
    if len(castle_name) > 30:
        await update.message.reply_text("⚠️ Castle name is too long (limit 30 characters).")
        return

    async with get_session() as session:
        existing = await _get_castle(session, user.id, chat_id)
        if existing:
            await update.message.reply_text(
                f"🏰 You already have a castle: <b>{existing.name}</b>\n"
                f"Use /my_castle to view details."
            )
            return

        castle = Castle(user_id=user.id, chat_id=chat_id, name=castle_name)
        session.add(castle)
        await _get_or_create_resources(session, user.id, chat_id)

    await update.message.reply_text(
        f"🏰 <b>Your castle has been created!</b>\n\n"
        f"Name: <b>{castle_name}</b>\n"
        f"Level: <b>1 — {_level_title(1)}</b>\n\n"
        f"💡 Start buying resources via /resource_shop\n"
        f"💰 Balance: 100 coins — use /wallet"
    )


async def cmd_my_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works in groups.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text(
                "🏚️ You don't have a castle yet.\nUse /create_castle to create one."
            )
            return

        res      = await _get_or_create_resources(session, user.id, chat_id)
        barracks = await _get_barracks(session, user.id, chat_id)
        immunity = await _get_immunity(session, user.id, chat_id)
        wallet   = await get_wallet(session, user.id)

        imm_line = "No immunity"
        if immunity and immunity.is_active:
            diff = immunity.active_until - _utcnow()
            h, m = divmod(int(diff.total_seconds() / 60), 60)
            imm_line = f"🛡️ Immune for {h}h {m}m"
        elif immunity and immunity.cards > 0:
            imm_line = f"🛡️ {immunity.cards} cards (not active)"

        upgrade_line = ""
        if castle.level < MAX_CASTLE_LEVEL:
            req = UPGRADE_REQUIREMENTS.get(castle.level)
            if req:
                w, s, f, g, cd = req
                upgrade_line = (
                    f"\n📋 <b>Upgrade Requirements for Level {castle.level+1}:</b>\n"
                    f"  Wood {w} | Stone {s} | Food {f} | Gold {g}\n"
                    f"  ⏱ Wait {cd} minutes after each upgrade"
                )

        army_line = "No barracks (create it with /create_barracks)" if not barracks else (
            f"⚔️ Army: {barracks.soldiers:,} soldiers | Power: {barracks.power_level} points"
        )

        text = (
            f"🏰 <b>{castle.name}</b>\n"
            f"{'━' * 28}\n"
            f"📊 Level: <b>{castle.level}</b> — {_level_title(castle.level)}\n"
            f"💰 Wallet: <b>{wallet.coins:,} coins</b>\n\n"
            f"🪵 Wood: {res.wood}  |  🪨 Stone: {res.stone}\n"
            f"🌾 Food: {res.food}  |  🏅 Gold: {res.gold}\n\n"
            f"{army_line}\n"
            f"🔒 Immunity: {imm_line}"
            f"{upgrade_line}"
        )
    await update.message.reply_text(text)


async def cmd_resource_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛒 <b>Resource Shop</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Resources are bought with wallet coins 💰\n\n"
        "🪵 <b>Wood</b>   — 3 units / coin   (limit 60)\n"
        "🪨 <b>Stone</b>   — 3 units / coin   (limit 60)\n"
        "🌾 <b>Food</b>  — 3 units / coin   (limit 60)\n"
        "🏅 <b>Gold</b>   — 1 unit  / coin   (limit 25)\n\n"
        "📝 How to buy:\n"
        "<code>/buy_resource wood 30</code>\n"
        "<code>/buy_resource gold 10</code>\n\n"
        "⚠️ Gold here is a castle resource, not wallet coins."
    )
    await update.message.reply_text(text)


async def cmd_buy_resource(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📌 Usage: <code>/buy_resource &lt;resource&gt; &lt;amount&gt;</code>\n"
            "Example: <code>/buy_resource wood 30</code>",
            parse_mode="HTML"
        )
        return

    raw_res = context.args[0].lower()
    res_key = RESOURCE_ALIASES.get(raw_res)
    if not res_key:
        await update.message.reply_text("❌ Unknown resource. Valid types: wood, stone, food, gold")
        return

    try:
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be a positive number.")
        return

    units_per_coin, max_buy, ar_name = RESOURCE_PRICES[res_key]
    if amount > max_buy:
        await update.message.reply_text(
            f"❌ Cannot buy more than {max_buy} units at once."
        )
        return

    cost = -(-amount // units_per_coin)  # ceiling division

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"❌ Insufficient balance!\n"
                f"Required: {cost} coins | You have: {w.coins} coins",
                parse_mode="HTML"
            )
            return

        res = await _get_or_create_resources(session, user.id, chat_id)
        setattr(res, res_key, getattr(res, res_key) + amount)
        remaining_coins = wallet.coins

    await update.message.reply_text(
        f"✅ Purchased <b>{amount} {ar_name}</b> for <b>{cost} coins</b>\n"
        f"💰 Remaining balance: {remaining_coins:,} coins\n"
        f"Use /my_resources to view your inventory.",
        parse_mode="HTML"
    )


async def cmd_my_resources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
            return
        res = await _get_or_create_resources(session, user.id, chat_id)

    await update.message.reply_text(
        f"🏪 <b>{user.first_name}'s Warehouse</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🪵 Wood:  <b>{res.wood}</b>\n"
        f"🪨 Stone: <b>{res.stone}</b>\n"
        f"🌾 Food:  <b>{res.food}</b>\n"
        f"🏅 Gold:  <b>{res.gold}</b>  (castle resource)\n\n"
        f"💡 Buy more: /resource_shop",
        parse_mode="HTML"
    )


async def cmd_upgrade_castle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
            return

        if castle.level >= MAX_CASTLE_LEVEL:
            await update.message.reply_text(
                f"👑 Your castle has reached the maximum level!\n"
                f"You are <b>{_level_title(MAX_CASTLE_LEVEL)}</b> — no further upgrades available.",
                parse_mode="HTML"
            )
            return

        req = UPGRADE_REQUIREMENTS[castle.level]
        need_w, need_s, need_f, need_g, cooldown_min = req

        # Check cooldown
        if castle.last_upgraded_at:
            elapsed = _utcnow() - castle.last_upgraded_at
            if elapsed < timedelta(minutes=cooldown_min):
                remaining = timedelta(minutes=cooldown_min) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                m = rem // 60
                await update.message.reply_text(
                    f"⏳ You must wait {cooldown_min} minutes between upgrades.\n"
                    f"Time remaining: <b>{h}h {m}m</b>",
                    parse_mode="HTML"
                )
                return

        res = await _get_or_create_resources(session, user.id, chat_id)

        missing = []
        if res.wood  < need_w: missing.append(f"Wood ({res.wood}/{need_w})")
        if res.stone < need_s: missing.append(f"Stone ({res.stone}/{need_s})")
        if res.food  < need_f: missing.append(f"Food ({res.food}/{need_f})")
        if res.gold  < need_g: missing.append(f"Gold ({res.gold}/{need_g})")

        if missing:
            await update.message.reply_text(
                f"❌ <b>Insufficient resources to upgrade:</b>\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\n\n🛒 Buy more from /resource_shop",
                parse_mode="HTML"
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
            f"🎉🏆 <b>Congratulations, {user.first_name}!</b>\n\n"
            f"Your castle has reached the maximum level!\n"
            f"Your title is now: <b>Ruler 🤴</b>\n\n"
            f"🎁 Reward: +50 coins added to your wallet!\n"
            f"📋 Join /top_rulers to see your rank."
        )
    else:
        await update.message.reply_text(
            f"⬆️ <b>Castle Upgraded!</b>\n\n"
            f"New Level: <b>{new_level}</b> — {_level_title(new_level)}\n\n"
            f"Next level requires a wait of {UPGRADE_REQUIREMENTS.get(new_level, (0,0,0,0,0))[4]} minutes"
            if new_level < MAX_CASTLE_LEVEL else ""
        )


# ---------------------------------------------------------------------------
# Commands: Army and Barracks
# ---------------------------------------------------------------------------

async def cmd_create_barracks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
            return

        existing = await _get_barracks(session, user.id, chat_id)
        if existing:
            await update.message.reply_text(
                f"⚔️ Your barracks already exists!\n"
                f"Soldiers: {existing.soldiers:,} | Power: {existing.power_level} points\n"
                f"Buy soldiers with /buy_army <amount>"
            )
            return

        session.add(Barracks(user_id=user.id, chat_id=chat_id))

    await update.message.reply_text(
        f"⚔️ <b>Barracks Created!</b>\n\n"
        f"Start recruiting your army: /buy_army 100\n"
        f"💡 Every 1000 soldiers = 1 Power Point"
    )


async def cmd_buy_army(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args:
        await update.message.reply_text(
            f"📌 Usage: <code>/buy_army &lt;amount&gt;</code>\n"
            f"Example: <code>/buy_army 200</code>\n"
            f"Cost: {ARMY_PURCHASE_COST} coins per 100 soldiers"
        )
        return

    try:
        amount = int(context.args[0])
        if amount <= 0 or amount % SOLDIERS_PER_PURCHASE != 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"❌ Amount must be a multiple of {SOLDIERS_PER_PURCHASE} (e.g., 100, 200, 300...)"
        )
        return

    if amount > MAX_SOLDIERS_PER_BUY:
        await update.message.reply_text(
            f"❌ You cannot buy more than {MAX_SOLDIERS_PER_BUY} soldiers at once."
        )
        return

    cost = (amount // SOLDIERS_PER_PURCHASE) * ARMY_PURCHASE_COST

    async with get_session() as session:
        barracks = await _get_barracks(session, user.id, chat_id)
        if not barracks:
            await update.message.reply_text("⚔️ Create your barracks first with /create_barracks")
            return

        wallet = await deduct_coins(session, user.id, cost)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await update.message.reply_text(
                f"💸 Insufficient balance!\n"
                f"Need: {cost} coins | You have: {w.coins} coins"
            )
            return

        barracks.soldiers += amount
        remaining_coins = wallet.coins

    await update.message.reply_text(
        f"⚔️ <b>{amount:,} soldiers</b> joined your army!\n"
        f"Cost: {cost} coins 💰\n"
        f"Remaining Balance: {remaining_coins:,} coins\n\n"
        f"💡 Use /upgrade_army to convert soldiers into Power Points"
    )


async def cmd_upgrade_army(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        barracks = await _get_barracks(session, user.id, chat_id)
        if not barracks:
            await update.message.reply_text("⚔️ Create your barracks first with /create_barracks")
            return

        new_power = barracks.soldiers // SOLDIERS_PER_POWER
        old_power = barracks.power_level

        if new_power <= old_power:
            needed = (old_power + 1) * SOLDIERS_PER_POWER
            still_need = needed - barracks.soldiers
            await update.message.reply_text(
                f"ℹ️ Your army ({barracks.soldiers:,} soldiers) = {old_power} Power Points.\n"
                f"To reach power {old_power+1}: you need {still_need:,} more soldiers."
            )
            return

        barracks.power_level = new_power

    await update.message.reply_text(
        f"💪 <b>Army Upgraded!</b>\n\n"
        f"Power Points: {old_power} ← <b>{new_power}</b>\n"
        f"Your Soldiers: {barracks.soldiers:,}\n\n"
        f"Military power is used in duels and great battles."
    )


# ---------------------------------------------------------------------------
# Treasure Digging
# ---------------------------------------------------------------------------

async def cmd_dig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
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
                    f"⛏️ You are tired from digging!\n"
                    f"Return after <b>{h} hours and {m} minutes</b>."
                )
                return

        # Random reward selection
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
            reward_text = f"🪵 You found <b>{amount} Wood</b>!"
        elif reward_type == "stone":
            amount = random.randint(10, 35)
            res.stone += amount
            reward_text = f"🪨 You found <b>{amount} Stone</b>!"
        elif reward_type == "food":
            amount = random.randint(10, 35)
            res.food += amount
            reward_text = f"🌾 You found <b>{amount} Food</b>!"
        elif reward_type == "gold":
            amount = random.randint(3, 10)
            res.gold += amount
            reward_text = f"🏅 You found <b>{amount} Gold</b> (Castle Resource)!"
        elif reward_type == "soldiers" and barracks:
            amount = random.randint(50, 200)
            barracks.soldiers += amount
            reward_text = f"⚔️ You found <b>{amount} soldiers</b> joining your army!"
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
            reward_text = f"🛡️ You found an <b>Immunity Card</b>! Use it with /immunity"
        elif reward_type == "coins":
            amount = random.randint(5, 20)
            await add_coins(session, user.id, amount)
            reward_text = f"💰 You found <b>{amount} coins</b> added to your wallet!"
        else:
            amount = random.randint(10, 25)
            res.wood += amount
            reward_text = f"🪵 You found <b>{amount} Wood</b>!"

        if hunt:
            hunt.last_hunt_at = now
        else:
            session.add(TreasureHunt(user_id=user.id, chat_id=chat_id, last_hunt_at=now))

    await update.message.reply_text(
        f"⛏️ <b>Digging Result...</b>\n\n"
        f"{reward_text}\n\n"
        f"⏰ You can dig again in {DIG_COOLDOWN_HOURS} hours."
    )


# ---------------------------------------------------------------------------
# Immunity
# ---------------------------------------------------------------------------

async def cmd_immunity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        imm = await _get_immunity(session, user.id, chat_id)

        # No record or no cards and no active immunity
        if not imm or (imm.cards == 0 and not imm.is_active):
            await update.message.reply_text(
                "🛡️ You don't have any Immunity Cards.\n"
                "Look for them with /dig"
            )
            return

        now = _utcnow()
        if imm.is_active:
            diff = imm.active_until - now
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            # Deactivate — card is consumed (not returned); players cannot abuse
            # activate→deactivate to farm free immunity time
            imm.active_until = None
            msg = (
                f"🛡️ Immunity has been <b>deactivated</b>.\n"
                f"Remaining time was: {h}h {m}m.\n"
                f"Remaining Cards: {imm.cards}"
            )
        else:
            # Activate — consume one card
            imm.active_until = now + IMMUNITY_DURATION
            imm.cards -= 1
            msg = (
                f"🛡️ <b>Immunity Activated!</b>\n"
                f"You are protected for a full 24 hours.\n"
                f"Remaining Cards: {imm.cards}"
            )

    await update.message.reply_text(msg)


async def cmd_my_immunity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        imm = await _get_immunity(session, user.id, chat_id)

    if not imm or (imm.cards == 0 and not imm.is_active):
        await update.message.reply_text(
            "🛡️ No active immunity and no cards collected.\n"
            "Look for cards with /dig"
        )
        return

    lines = [f"🛡️ <b>{user.first_name}'s Immunity</b>\n"]
    if imm.is_active:
        diff = imm.active_until - _utcnow()
        h, rem = divmod(int(diff.total_seconds()), 3600)
        m = rem // 60
        lines.append(f"✅ Immunity <b>Active</b> — expires in {h}h {m}m")
    else:
        lines.append("⭕ Immunity not active")

    lines.append(f"🃏 Available Cards: <b>{imm.cards}</b>")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Duel (Reply only)
# ---------------------------------------------------------------------------

async def cmd_duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    msg     = update.message
    if update.effective_chat.type == "private":
        await msg.reply_text("⚠️ This command only works inside groups.")
        return

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("⚔️ To duel, reply to your opponent's message with /duel")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("⚔️ You cannot duel yourself!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 You cannot duel a bot!")
        return

    # Check duel cooldown (30 minutes between duels with same opponent)
    cooldown_key = f"duel_{user.id}_{target.id}"
    last_duel: Optional[datetime] = context.bot_data.get(cooldown_key)
    if last_duel:
        elapsed = (_utcnow() - last_duel).total_seconds()
        if elapsed < DUEL_COOLDOWN_MIN * 60:
            remaining_m = int((DUEL_COOLDOWN_MIN * 60 - elapsed) / 60)
            await msg.reply_text(
                f"⏳ You dueled {target.first_name} recently.\n"
                f"Wait <b>{remaining_m} minutes</b> before dueling them again."
            )
            return

    async with get_session() as session:
        # Check both players' castles
        my_castle = await _get_castle(session, user.id, chat_id)
        if not my_castle:
            await msg.reply_text("🏚️ Create your castle first with /create_castle")
            return

        their_castle = await _get_castle(session, target.id, chat_id)
        if not their_castle:
            await msg.reply_text(f"🏚️ {target.first_name} doesn't have a castle in this group.")
            return

        # Check target's immunity
        their_imm = await _get_immunity(session, target.id, chat_id)
        if their_imm and their_imm.is_active:
            diff = their_imm.active_until - _utcnow()
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            await msg.reply_text(
                f"🛡️ {target.first_name} is protected by immunity!\n"
                f"Expires in {h}h {m}m — try later."
            )
            return

        my_bar     = await _get_barracks(session, user.id, chat_id)
        their_bar  = await _get_barracks(session, target.id, chat_id)
        my_power   = (my_bar.power_level if my_bar else 0) + my_castle.level * 2
        their_power = (their_bar.power_level if their_bar else 0) + their_castle.level * 2

        # Slight randomness for excitement
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

        # Deduct 10% of loser's soldiers
        soldiers_lost = 0
        if loser_bar and loser_bar.soldiers > 0:
            soldiers_lost = max(10, loser_bar.soldiers // 10)
            loser_bar.soldiers = max(0, loser_bar.soldiers - soldiers_lost)
            loser_bar.power_level = loser_bar.soldiers // SOLDIERS_PER_POWER

        # Winner reward
        await add_coins(session, winner_id, DUEL_WIN_REWARD)

    # Record duel time to prevent spam
    context.bot_data[cooldown_key] = _utcnow()

    emoji = "⚔️" if attacker_won else "🛡️"
    await msg.reply_text(
        f"⚔️ <b>Duel Result!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🗡 {user.first_name}: {my_roll} points\n"
        f"🗡 {target.first_name}: {their_roll} points\n\n"
        f"{emoji} Winner: <b>{winner_name}</b>!\n"
        f"💔 {loser_name} lost {soldiers_lost:,} soldiers\n"
        f"🎁 {winner_name} earned {DUEL_WIN_REWARD} coins 💰"
    )


# ---------------------------------------------------------------------------
# Great Battle
# ---------------------------------------------------------------------------

async def cmd_start_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admins only — /start_battle <registration_minutes>"""
    user    = update.effective_user
    chat    = update.effective_chat
    chat_id = chat.id
    if chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    try:
        mins = int(context.args[0]) if context.args else 10
        if mins < 1 or mins > 60:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("📌 Usage: /start_battle <1-60 minutes>")
        return

    async with get_session() as session:
        existing = await _get_active_battle(session, chat_id)
        if existing:
            await update.message.reply_text("⚔️ A battle is already in progress! Use /end_battle to finish it.")
            return

        battle = GlobalBattle(
            chat_id=chat_id,
            registration_ends_at=_utcnow() + timedelta(minutes=mins),
        )
        session.add(battle)

    await update.message.reply_text(
        f"⚔️ <b>The Great Battle has started!</b>\n\n"
        f"🕐 Registration open for <b>{mins} minutes</b>\n"
        f"Join with /join_battle\n\n"
        f"After registration ends, an admin can announce the winner with /end_battle"
    )


async def cmd_join_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ No active battle right now.")
            return

        if _utcnow() > battle.registration_ends_at:
            await update.message.reply_text(
                "⏰ Registration time has ended!\n"
                "Wait for the next battle."
            )
            return

        r = await session.execute(
            select(BattleParticipant).where(
                BattleParticipant.battle_id == battle.id,
                BattleParticipant.user_id   == user.id,
            )
        )
        if r.scalar_one_or_none():
            await update.message.reply_text("✅ You are already registered for this battle!")
            return

        castle  = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
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
        f"⚔️ <b>You have joined the great battle!</b>\n\n"
        f"Your Castle: Level {castle_level} ({_level_title(castle_level)})\n"
        f"Your Power: {total_power} points (Castle×10 + Army)\n\n"
        f"Use /fighters to view competitors."
    )


async def cmd_fighters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ No active battle right now.")
            return

        r = await session.execute(
            select(BattleParticipant)
            .where(BattleParticipant.battle_id == battle.id)
            .order_by(BattleParticipant.total_power.desc())
        )
        participants = r.scalars().all()

        if not participants:
            await update.message.reply_text("📭 No one has joined yet — use /join_battle")
            return

        now      = _utcnow()
        if now < battle.registration_ends_at:
            diff = battle.registration_ends_at - now
            m = int(diff.total_seconds() / 60)
            time_line = f"⏰ Registration ends in <b>{m} minutes</b>"
        else:
            time_line = "⏰ Registration <b>ended</b> — waiting for winner announcement"

        lines = [f"⚔️ <b>Great Battle — {len(participants)} fighters</b>\n{time_line}\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(participants[:10]):
            tag  = f"@{p.username}" if p.username else p.first_name
            med  = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{med} {tag} — Power: <b>{p.total_power}</b>")

    await update.message.reply_text("\n".join(lines))


async def cmd_end_battle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """For admins — end battle and announce winner."""
    user    = update.effective_user
    chat    = update.effective_chat
    chat_id = chat.id
    if chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ This command is for admins only.")
        return

    async with get_session() as session:
        battle = await _get_active_battle(session, chat_id)
        if not battle:
            await update.message.reply_text("❌ No active battle right now.")
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
            await update.message.reply_text("⚔️ Battle ended — no one participated.")
            return

        winner = participants[0]
        battle.is_active = False
        battle.ended_at  = _utcnow()

        # Record title
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

        # Ranking list
        rank_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(participants[:5]):
            tag = f"@{p.username}" if p.username else p.first_name
            med = medals[i] if i < 3 else f"{i+1}."
            rank_lines.append(f"{med} {tag} — {p.total_power} points")

    winner_tag = f"@{winner.username}" if winner.username else winner.first_name
    await update.message.reply_text(
        f"🏆 <b>The Great Battle has ended!</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"👑 <b>Ruler: {winner_tag}</b>\n"
        f"Winner's Power: {winner.total_power} points\n"
        f"🎁 Reward: {BATTLE_WIN_REWARD} coins 💰\n\n"
        f"<b>Fighters Ranking:</b>\n"
        + "\n".join(rank_lines)
    )


# ---------------------------------------------------------------------------
# Rulers List
# ---------------------------------------------------------------------------

async def cmd_top_rulers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
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
            "📋 No rulers in this group yet.\n"
            "Start a battle with /start_battle to decide the ruler!"
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = ["👑 <b>Rulers List</b>\n"]
    for i, r in enumerate(rulers):
        tag = f"@{r.username}" if r.username else r.first_name
        med = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{med} {tag} — {r.wins} wins")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Alliance and Raids
# ---------------------------------------------------------------------------

async def cmd_alliance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    msg     = update.message
    if update.effective_chat.type == "private":
        await msg.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args:
        await msg.reply_text(
            "📌 Usage: <code>/alliance @username</code>\n"
            "To send an alliance request and perform a joint raid on a target from the rulers list."
        )
        return

    target_username = context.args[0].lstrip("@")

    async with get_session() as session:
        my_castle = await _get_castle(session, user.id, chat_id)
        if not my_castle:
            await msg.reply_text("🏚️ Create your castle first with /create_castle")
            return

        # Search for user via username in Rulers table
        r = await session.execute(
            select(RulerTitle).where(
                RulerTitle.chat_id == chat_id,
                RulerTitle.username == target_username,
            )
        )
        target_ruler = r.scalar_one_or_none()

        if not target_ruler:
            await msg.reply_text(
                f"❌ Could not find @{target_username} in this group or they don't have a castle."
            )
            return

        if target_ruler.user_id == user.id:
            await msg.reply_text("❌ You cannot send an alliance request to yourself.")
            return

        # Check for existing pending request
        r2 = await session.execute(
            select(AllianceRequest).where(
                AllianceRequest.chat_id       == chat_id,
                AllianceRequest.requester_id  == user.id,
                AllianceRequest.target_id     == target_ruler.user_id,
                AllianceRequest.status        == AllianceStatus.PENDING,
            )
        )
        if r2.scalar_one_or_none():
            await msg.reply_text("⏳ Alliance request with this person is already pending.")
            return

        req = AllianceRequest(
            chat_id        = chat_id,
            requester_id   = user.id,
            requester_name = user.first_name,
            target_id      = target_ruler.user_id,
            target_name    = target_ruler.first_name,
        )
        session.add(req)
        req_id = None  # Fetched after flush
        await session.flush()
        req_id = req.id

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"allianceaccept:{req_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"alliancereject:{req_id}"),
    ]])

    target_tag = f"@{target_username}"
    await msg.reply_text(
        f"🤝 <b>Alliance Request</b>\n\n"
        f"{target_tag}, <b>{user.first_name}</b> is asking you to ally for a joint raid.\n\n"
        f"Do you agree?",
        reply_markup=kb,
    )


async def _alliance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    user    = query.from_user
    chat_id = query.message.chat_id
    data    = query.data

    await query.answer()

    try:
        action, req_id_str = data.split(":", 1)
        req_id = int(req_id_str)
    except (ValueError, AttributeError):
        await query.edit_message_text("⚠️ Invalid request data.")
        return

    async with get_session() as session:
        r = await session.execute(
            select(AllianceRequest).where(AllianceRequest.id == req_id)
        )
        req = r.scalar_one_or_none()

        if not req or req.status != AllianceStatus.PENDING:
            await query.edit_message_text("⚠️ This request is no longer valid.")
            return

        if user.id != req.target_id:
            await query.answer("This request is not for you.", show_alert=True)
            return

        if action == "alliancereject":
            req.status = AllianceStatus.REJECTED
            await query.edit_message_text(
                f"❌ {user.first_name} rejected the alliance request from {req.requester_name}."
            )
            return

        req.status = AllianceStatus.ACCEPTED

        # Calculate combined alliance power
        my_castle = await _get_castle(session, req.requester_id, chat_id)
        ally_castle = await _get_castle(session, req.target_id, chat_id)
        my_bar  = await _get_barracks(session, req.requester_id, chat_id)
        ally_bar = await _get_barracks(session, req.target_id, chat_id)

        combined_power = (
            ((my_castle.level  if my_castle  else 1) * 10 + (my_bar.power_level  if my_bar  else 0)) +
            ((ally_castle.level if ally_castle else 1) * 10 + (ally_bar.power_level if ally_bar else 0))
        )

        # Find strongest competitor in Rulers list
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
                # Successful raid
                soldiers_destroyed = 0
                if target_bar and target_bar.soldiers > 0:
                    soldiers_destroyed = max(50, target_bar.soldiers // 5)
                    target_bar.soldiers = max(0, target_bar.soldiers - soldiers_destroyed)
                    target_bar.power_level = target_bar.soldiers // SOLDIERS_PER_POWER

                await add_coins(session, req.requester_id, 30)
                await add_coins(session, req.target_id, 30)

                dmg_line = (
                    f"⚔️ Destroyed from {target_tag}'s army: <b>{soldiers_destroyed:,} soldiers</b>\n"
                    if soldiers_destroyed > 0
                    else f"ℹ️ {target_tag} has no army — the castle was only morally damaged.\n"
                )
                result_text = (
                    f"🎉 <b>Raid Successful!</b>\n"
                    f"Your Power: {raid_roll} points vs {target_roll} points\n"
                    f"{dmg_line}"
                    f"🎁 Each ally earned 30 coins 💰"
                )
            else:
                result_text = (
                    f"💔 <b>Raid Failed!</b>\n"
                    f"Your Power: {raid_roll} points vs {target_roll} points\n"
                    f"{target_tag} defended their castle bravely!"
                )
        else:
            result_text = "ℹ️ No target in the rulers list to raid."

    await query.edit_message_text(
        f"🤝 <b>Alliance Accepted!</b>\n"
        f"{req.requester_name} + {req.target_name}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{result_text}"
    )


async def cmd_exchange_gold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /exchange_gold <amount>
    Convert castle gold to wallet coins — Rate: 1 gold = 100 coins.
    Converted gold is deducted from castle storage and added to general wallet.
    """
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
        return

    if not context.args:
        await update.message.reply_text(
            f"📌 Usage: <code>/exchange_gold &lt;amount&gt;</code>\n"
            f"Example: <code>/exchange_gold 5</code>\n\n"
            f"💱 Exchange Rate: 1 Castle Gold 🏅 = {GOLD_TO_COINS_RATE} coins 💰"
        )
        return

    try:
        amount = int(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be a positive number.")
        return

    async with get_session() as session:
        castle = await _get_castle(session, user.id, chat_id)
        if not castle:
            await update.message.reply_text("🏚️ Create your castle first with /create_castle")
            return

        res = await _get_or_create_resources(session, user.id, chat_id)
        if res.gold < amount:
            await update.message.reply_text(
                f"❌ Not enough gold!\n"
                f"You want to exchange: {amount} 🏅 | You have: {res.gold} 🏅\n\n"
                f"Get more via /dig or /buy_resource gold {amount - res.gold}"
            )
            return

        coins_earned = amount * GOLD_TO_COINS_RATE
        res.gold -= amount
        wallet = await add_coins(session, user.id, coins_earned)
        new_balance = wallet.coins

    await update.message.reply_text(
        f"💱 <b>Conversion Successful!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏅 Gold Converted:  <b>{amount}</b>\n"
        f"💰 Coins Earned: <b>{coins_earned:,}</b>\n\n"
        f"New Balance: <b>{new_balance:,} coins</b>\n"
        f"Remaining Gold: <b>{res.gold} 🏅</b>"
    )


async def cmd_alliance_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("⚠️ This command only works inside groups.")
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
        await update.message.reply_text("📭 No pending alliance requests.")
        return

    lines = [f"🤝 <b>Incoming Alliance Requests ({len(reqs)})</b>\n"]
    for req in reqs:
        lines.append(f"• {req.requester_name} — Use buttons to accept/reject")

    await update.message.reply_text(
        "\n".join(lines) + "\n\n"
        "💡 Use the buttons in the alliance request message to approve or reject."
    )


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    # Castle
    application.add_handler(CommandHandler("create_castle",    cmd_create_castle))
    application.add_handler(CommandHandler("my_castle",        cmd_my_castle))
    application.add_handler(CommandHandler("resource_shop",    cmd_resource_shop))
    application.add_handler(CommandHandler("buy_resource",     cmd_buy_resource))
    application.add_handler(CommandHandler("my_resources",     cmd_my_resources))
    application.add_handler(CommandHandler("upgrade_castle",   cmd_upgrade_castle))
    # Army
    application.add_handler(CommandHandler("create_barracks",  cmd_create_barracks))
    application.add_handler(CommandHandler("buy_army",         cmd_buy_army))
    application.add_handler(CommandHandler("upgrade_army",     cmd_upgrade_army))
    # Treasure and Immunity
    application.add_handler(CommandHandler("dig",              cmd_dig))
    application.add_handler(CommandHandler("immunity",         cmd_immunity))
    application.add_handler(CommandHandler("my_immunity",      cmd_my_immunity))
    # Duels
    application.add_handler(CommandHandler("duel",             cmd_duel))
    application.add_handler(CommandHandler("start_battle",     cmd_start_battle))
    application.add_handler(CommandHandler("join_battle",      cmd_join_battle))
    application.add_handler(CommandHandler("fighters",         cmd_fighters))
    application.add_handler(CommandHandler("end_battle",       cmd_end_battle))
    application.add_handler(CommandHandler("top_rulers",       cmd_top_rulers))
    # Exchange
    application.add_handler(CommandHandler("exchange_gold",     cmd_exchange_gold))
    # Alliance
    application.add_handler(CommandHandler("alliance",          cmd_alliance))
    application.add_handler(CommandHandler("alliance_requests", cmd_alliance_requests))
    # Callbacks
    application.add_handler(CallbackQueryHandler(_alliance_callback, pattern=r"^alliance(accept|reject):\d+$"))

    logger.info("castle_game plugin registered — %d commands.", 22)
