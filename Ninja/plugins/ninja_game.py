"""
plugins/ninja_game.py — Ninja Game 🥷 (Assassination + Kidnapping)

Commands:
  /my_ninja        — View your profile and level
  /assassinate     — Attempt to assassinate another player (reply to their message)
  /kidnap          — Kidnap a player (reply to their message)
  /pay_ransom      — Pay the ransom to free yourself or another
  /rescue          — Rescue a kidnapped player (reply to their message)
  /escape_kidnap   — Attempt to escape on your own (dangerous!)
  /ninja_rank      — Top 10 ninjas in the group

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ninja Levels (based on XP):
  🥷 Trainee    0   XP
  ⚔️ Apprentice 50   XP
  🗡 Ninja      150  XP
  🌑 Shadow     350  XP
  🔥 Master     700  XP
  💀 Legend     1200 XP

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
XP Points:
  Successful Kill    → +15 XP
  Successful Kidnap  → +25 XP
  Successful Rescue  → +20 XP
  Successful Escape  → +10 XP

Health:
  Each player has ❤️ × 3 (regenerates 1 every 6 hours)
  Death (Health = 0) prevents assassination until regeneration
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
# Game Constants
# ---------------------------------------------------------------------------
MAX_HEALTH          = 3
HEALTH_REGEN_HOURS  = 6       # Hours between each health regeneration

ASSASSINATE_CD_MIN  = 60      # Assassination cooldown (minutes)
KIDNAP_CD_MIN       = 180     # Kidnapping cooldown (minutes)
KIDNAP_DURATION_H   = 4       # Kidnapping duration in hours
RESCUE_CD_MIN       = 90      # Rescue cooldown (minutes)
ESCAPE_CD_MIN       = 120     # Escape cooldown

XP_KILL    = 15
XP_KIDNAP  = 25
XP_RESCUE  = 20
XP_ESCAPE  = 10
XP_SURVIVE = 5   # XP for the victim who survived an assassination

BASE_RANSOM = 40  # Base coins for ransom
MAX_RANSOM  = 120

HEARTS = ["❤️", "🧡", "💛"]     # Health colors


# ---------------------------------------------------------------------------
# Helpers
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
    """Regenerate health if the required time has passed. Returns True if health changed."""
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
    """Battle randomness. Returns (attacker points, defender points)."""
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
    return f"{remaining_m} minutes"


def _add_xp(profile: NinjaProfile, amount: int) -> bool:
    """Adds XP and updates level. Returns True if leveled up."""
    old_level = profile.level
    profile.xp += amount
    new_level  = xp_to_level(profile.xp)
    profile.level = new_level
    return new_level != old_level


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_my_ninja(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("🥷 This game only works inside groups.")
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
            f"\n🔒 <b>Kidnapped!</b> Ransom: {kidnap.ransom_coins} coins\n"
            f"You will be automatically released after {m} minutes if the ransom is not paid."
        )

    level_line = (
        "🏆 Max Level! — No higher level"
        if profile.level == NinjaLevel.LEGEND
        else f"📈 XP for next level: {xp_remaining}"
    )

    await update.message.reply_text(
        f"🥷 <b>Ninja Profile — {user.first_name}</b>\n"
        f"{'━'*26}\n"
        f"🎖 Level: <b>{profile.level.english}</b>\n"
        f"⚡ XP: <b>{profile.xp}</b>   {_xp_bar(profile.xp)}\n"
        f"💗 Health: {_heart_bar(profile.health)}\n\n"
        f"🗡 Successful Assassinations: <b>{profile.kills}</b>\n"
        f"💀 Deaths:          <b>{profile.deaths}</b>\n"
        f"🔗 Kidnappings:    <b>{profile.kidnaps_done}</b>\n"
        f"🦸 Rescues:        <b>{profile.rescues}</b>\n"
        f"🏃 Successful Escapes:   <b>{profile.kidnaps_survived}</b>\n"
        f"❌ Failed Attacks:   <b>{profile.failed_attacks}</b>\n\n"
        f"{level_line}"
        f"{kidnap_line}",
        parse_mode="HTML"
    )


async def cmd_assassinate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to the target's message and send /assassinate"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 This game only works inside groups.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🗡 Reply to the target's message and use /assassinate")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤦 You cannot assassinate yourself!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 Bots do not die!")
        return

    # Check cooldown
    cd_key = f"ninja_atk_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, ASSASSINATE_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ You must wait {wait} before another assassination attempt.")
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

        # Check attacker health
        if attacker.health <= 0:
            hours_needed = HEALTH_REGEN_HOURS
            await msg.reply_text(
                f"💔 You are exhausted and cannot fight!\n"
                f"Your health regenerates every {hours_needed} hours — rest first."
            )
            return

        # Check if attacker is kidnapped
        my_kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if my_kidnap:
            await msg.reply_text(
                "🔒 You are kidnapped and cannot fight!\n"
                f"Pay the ransom ({my_kidnap.ransom_coins} coins) with /pay_ransom or wait for the duration to end."
            )
            return

        # Battle
        a_roll, d_roll = _attack_roll(attacker, defender)
        attacker_won   = a_roll > d_roll

        if attacker_won:
            # Successful assassination
            defender.health = max(0, defender.health - 1)
            attacker.kills += 1
            leveled_up = _add_xp(attacker, XP_KILL)

            if defender.health <= 0:
                defender.deaths += 1
                death_line = f"💀 <b>{target.first_name} has met their end!</b> Health = 0 — waiting for regeneration."
                defender.last_health_regen = _utcnow()
            else:
                death_line = f"❤️ {target.first_name}'s remaining health: {_heart_bar(defender.health)}"

            level_line = f"\n🆙 <b>You have ascended to {attacker.level.english} level!</b>" if leveled_up else ""
            result = (
                f"🗡 <b>Assassination Successful!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} ({a_roll}) struck {target.first_name} ({d_roll})\n\n"
                f"{death_line}\n"
                f"⚡ You gained {XP_KILL} XP — total: {attacker.xp}"
                f"{level_line}"
            )
        else:
            # Failed attack — attacker loses health
            attacker.health = max(0, attacker.health - 1)
            attacker.failed_attacks += 1
            _add_xp(defender, XP_SURVIVE)

            if attacker.health <= 0:
                attacker.deaths += 1
                attacker_line = f"💀 {user.first_name} met their end during the attack!"
            else:
                attacker_line = f"❤️ Your remaining health: {_heart_bar(attacker.health)}"

            result = (
                f"🛡 <b>Attack Failed!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} ({a_roll}) was defeated by {target.first_name} ({d_roll})\n\n"
                f"{attacker_line}\n"
                f"⚡ {target.first_name} earned {XP_SURVIVE} XP for defending"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result, parse_mode="HTML")


async def cmd_kidnap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to the target's message and use /kidnap"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 This game only works inside groups.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🔗 Reply to the target's message and use /kidnap")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤦 You cannot kidnap yourself!")
        return
    if target.is_bot:
        await msg.reply_text("🤖 Bots cannot be kidnapped!")
        return

    # Check cooldown
    cd_key = f"ninja_kd_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, KIDNAP_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ You cannot kidnap again for {wait}.")
        return

    async with get_session() as session:
        kidnapper = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        victim = await _get_or_create_profile(
            session, target.id, chat_id, target.first_name, target.username
        )
        _regen_health(kidnapper)

        # Check that attacker is not kidnapped
        my_kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if my_kidnap:
            await msg.reply_text("🔒 You are already kidnapped! You cannot kidnap anyone now.")
            return

        # Check kidnapper health
        if kidnapper.health <= 0:
            await msg.reply_text("💔 You are exhausted and cannot kidnap! Rest until your health regenerates.")
            return

        # Check if victim is already kidnapped
        existing = await _get_active_kidnap_as_victim(session, target.id, chat_id)
        if existing:
            await msg.reply_text(
                f"🔒 {target.first_name} is already kidnapped!\n"
                f"Kidnapper: {existing.kidnapper_name}"
            )
            return

        # Kidnap randomness
        k_roll = kidnapper.level.power + random.randint(0, 35)
        v_roll = victim.level.power   + random.randint(0, 25)

        if k_roll > v_roll:
            # Successful kidnap
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

            level_line = f"\n🆙 <b>You have ascended to {kidnapper.level.english} level!</b>" if leveled_up else ""
            result = (
                f"🔗 <b>Kidnapping Successful!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} kidnapped {target.first_name}!\n\n"
                f"💰 Ransom: <b>{ransom} coins</b>\n"
                f"⏳ Will be automatically released after {KIDNAP_DURATION_H} hours\n\n"
                f"To release: /pay_ransom\n"
                f"To rescue (by force): Reply to the kidnapped person's message and use /rescue"
                f"{level_line}"
            )
        else:
            # Failed kidnap
            kidnapper.health = max(0, kidnapper.health - 1)
            kidnapper.failed_attacks += 1
            if kidnapper.health <= 0:
                kidnapper.deaths += 1
                fail_line = f"💀 {user.first_name} was caught and lost consciousness during the attempt!"
            else:
                fail_line = f"❤️ Your remaining health: {_heart_bar(kidnapper.health)}"

            result = (
                f"🛡 <b>Kidnapping Attempt Failed!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{target.first_name} noticed and foiled the plan ({k_roll} vs {v_roll})\n\n"
                f"{fail_line}"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result, parse_mode="HTML")


async def cmd_pay_ransom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pay_ransom          — free yourself
    /pay_ransom @username — free someone else
    """
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 This game only works inside groups.")
        return

    async with get_session() as session:
        # Is the payer freeing themselves or another?
        kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)

        if not kidnap:
            # Check if there is a kidnapped person in the group they want to free
            await msg.reply_text(
                "ℹ️ You are not kidnapped.\n"
                "To free a kidnapped person, reply to their message and use /rescue"
            )
            return

        wallet = await deduct_coins(session, user.id, kidnap.ransom_coins)
        if wallet is None:
            w = await get_wallet(session, user.id)
            await msg.reply_text(
                f"💸 Insufficient balance to pay the ransom!\n"
                f"Ransom: {kidnap.ransom_coins} coins | Your balance: {w.coins} coins\n\n"
                f"Other ways: /escape_kidnap (dangerous) or wait {KIDNAP_DURATION_H} hours."
            )
            return

        kidnap.status      = KidnapStatus.RANSOMED
        kidnap.released_at = _utcnow()

        # Update victim profile
        victim_profile = await _get_profile(session, user.id, chat_id)
        if victim_profile:
            victim_profile.is_kidnapped = False

        # Add ransom to kidnapper
        await add_coins(session, kidnap.kidnapper_id, kidnap.ransom_coins)

        remaining = wallet.coins

    await msg.reply_text(
        f"💰 <b>Ransom Paid!</b>\n\n"
        f"Paid {kidnap.ransom_coins} coins to kidnapper {kidnap.kidnapper_name}\n"
        f"🔓 You are free now!\n"
        f"💰 Remaining balance: {remaining} coins",
        parse_mode="HTML"
    )


async def cmd_rescue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to the kidnapped person's message and use /rescue to attempt a rescue"""
    user    = update.effective_user
    msg     = update.message
    chat_id = update.effective_chat.id

    if update.effective_chat.type == "private":
        await msg.reply_text("🥷 This game only works inside groups.")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🦸 Reply to the kidnapped person's message and use /rescue")
        return

    target = msg.reply_to_message.from_user
    if target.id == user.id:
        await msg.reply_text("🤔 To rescue yourself use /escape_kidnap")
        return

    cd_key = f"ninja_res_{user.id}_{chat_id}"
    wait   = _cd_remaining_text(context.bot_data, cd_key, RESCUE_CD_MIN)
    if wait:
        await msg.reply_text(f"⏳ You must wait {wait} before another rescue attempt.")
        return

    async with get_session() as session:
        kidnap = await _get_active_kidnap_as_victim(session, target.id, chat_id)
        if not kidnap:
            await msg.reply_text(f"ℹ️ {target.first_name} is not kidnapped.")
            return

        rescuer = await _get_or_create_profile(
            session, user.id, chat_id, user.first_name, user.username
        )
        _regen_health(rescuer)

        if rescuer.health <= 0:
            await msg.reply_text("💔 You are exhausted and cannot rescue! Rest first.")
            return

        # Rescue randomness
        r_roll = rescuer.level.power + random.randint(0, 40)
        g_roll = LEVEL_ORDER.index(NinjaLevel.TRAINEE) * 20 + random.randint(20, 50)

        if r_roll > g_roll:
            # Successful rescue
            kidnap.status      = KidnapStatus.RESCUED
            kidnap.released_at = _utcnow()

            victim_profile = await _get_profile(session, target.id, chat_id)
            if victim_profile:
                victim_profile.is_kidnapped = False

            rescuer.rescues += 1
            leveled_up = _add_xp(rescuer, XP_RESCUE)

            level_line = f"\n🆙 <b>You have ascended to {rescuer.level.english} level!</b>" if leveled_up else ""
            result = (
                f"🦸 <b>Rescue Successful!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🥷 {user.first_name} successfully rescued {target.first_name} from {kidnap.kidnapper_name}!\n\n"
                f"⚡ Gained {XP_RESCUE} XP"
                f"{level_line}"
            )
        else:
            # Failed rescue
            rescuer.health = max(0, rescuer.health - 1)
            if rescuer.health <= 0:
                rescuer.deaths += 1
                fail_line = f"💀 {user.first_name} was defeated during the rescue attempt!"
            else:
                fail_line = f"❤️ Your remaining health: {_heart_bar(rescuer.health)}"

            result = (
                f"🛡 <b>Rescue Failed!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"The guards were too strong for {user.first_name} ({r_roll} vs {g_roll})\n\n"
                f"{fail_line}"
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_text(result, parse_mode="HTML")


async def _rank_text(chat_id: int) -> str:
    async with get_session() as session:
        result = await session.execute(
            select(NinjaProfile)
            .where(NinjaProfile.chat_id == chat_id)
            .order_by(NinjaProfile.xp.desc())
            .limit(10)
        )
        rows = result.scalars().all()

    if not rows:
        return "No ninjas have joined the battle yet in this group."

    lines = ["🥷 <b>Top 10 Ninjas</b>", "━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        name = r.first_name or f"User {r.user_id}"
        lines.append(f"{i}. <b>{name}</b> — {r.level.english} ({r.xp} XP)")

    return "\n".join(lines)


async def cmd_ninja_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("🥷 This command only works inside groups.")
        return
    text = await _rank_text(chat_id)
    await update.message.reply_html(text)


async def cmd_escape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Try to escape kidnapping on your own."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    msg = update.message

    async with get_session() as session:
        kidnap = await _get_active_kidnap_as_victim(session, user.id, chat_id)
        if not kidnap:
            await msg.reply_text("ℹ️ You are not kidnapped.")
            return

        profile = await _get_or_create_profile(session, user.id, chat_id, user.first_name, user.username)
        _regen_health(profile)

        cd_key = f"ninja_esc_{user.id}_{chat_id}"
        wait = _cd_remaining_text(context.bot_data, cd_key, ESCAPE_CD_MIN)
        if wait:
            await msg.reply_text(f"⏳ You are too weak to attempt another escape yet. Wait {wait}.")
            return

        # Escape roll
        roll = random.randint(1, 100)
        success_chance = 25 + (LEVEL_ORDER.index(profile.level) * 5)

        if roll <= success_chance:
            kidnap.status = KidnapStatus.ESCAPED
            kidnap.released_at = _utcnow()
            profile.is_kidnapped = False
            profile.kidnaps_survived += 1
            _add_xp(profile, XP_ESCAPE)
            result = (
                "🏃 <b>Escape Successful!</b>\n\n"
                f"You managed to slip away from {kidnap.kidnapper_name}'s grasp!\n"
                f"⚡ Gained {XP_ESCAPE} XP"
            )
        else:
            profile.health = max(0, profile.health - 1)
            if profile.health <= 0:
                profile.deaths += 1
                fail_line = "💀 You were caught and beaten! Health = 0."
            else:
                fail_line = f"❤️ You were caught and injured! Health: {_heart_bar(profile.health)}"

            result = (
                "🛡 <b>Escape Failed!</b>\n\n"
                f"{fail_line}\n"
                "The kidnapper's grip is too tight."
            )

    context.bot_data[cd_key] = _utcnow()
    await msg.reply_html(result)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler(["my_ninja", "ninja_profile"], cmd_my_ninja))
    application.add_handler(CommandHandler("assassinate", cmd_assassinate))
    application.add_handler(CommandHandler("kidnap", cmd_kidnap))
    application.add_handler(CommandHandler("pay_ransom", cmd_pay_ransom))
    application.add_handler(CommandHandler("rescue", cmd_rescue))
    application.add_handler(CommandHandler("escape_kidnap", cmd_escape))
    application.add_handler(CommandHandler(["ninja_rank", "ninja_top"], cmd_ninja_rank))
    logger.info("Ninja game plugin registered.")
