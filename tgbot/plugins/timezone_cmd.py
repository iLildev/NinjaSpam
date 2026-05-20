"""
plugins/timezone_cmd.py — Per-user timezone configuration.

Allows any Telegram user to store their timezone privately.  The timezone is
used by other features (night mode, scheduled messages, etc.) to display times
in the user's local zone.

Privacy policy:
  - When a location is shared: ONLY the resolved timezone city-label is stored.
    The raw GPS coordinates (latitude/longitude) are read in memory to determine
    the timezone and are immediately discarded — never written to the database.
  - When a city name is typed: only the city label and IANA timezone string are
    stored.

Commands (work in both private and group chats):
  /settimezone <city>   — Set timezone by city name (e.g. /settimezone Aden).
  /settimezone          — Show instructions + ask to share location.
  /mytimezone           — Show currently configured timezone and local time.
  /cleartimezone        — Remove stored timezone data.

Location handler:
  Sharing a location (via Telegram's attach menu) in a private chat
  automatically detects and sets the timezone.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pytz
from sqlalchemy import select
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.helpers.city_timezones import all_suggestions, resolve_city, resolve_timezone_name
from database.engine import get_session
from database.models import User
from database.models_extra import UserTimezone

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _ensure_user(session, user_id: int, first_name: str = "") -> None:
    if not await session.get(User, user_id):
        session.add(User(id=user_id, first_name=first_name))
        await session.flush()


async def _get_user_tz(user_id: int) -> Optional[UserTimezone]:
    async with get_session() as session:
        return await session.get(UserTimezone, user_id)


async def _save_timezone(
    user_id: int,
    first_name: str,
    timezone_name: str,
    city_label: str,
) -> None:
    async with get_session() as session:
        await _ensure_user(session, user_id, first_name)
        tz_row = await session.get(UserTimezone, user_id)
        if tz_row is None:
            session.add(UserTimezone(
                user_id=user_id,
                timezone_name=timezone_name,
                city_label=city_label,
            ))
        else:
            tz_row.timezone_name = timezone_name
            tz_row.city_label = city_label


def _format_local_time(timezone_name: str) -> str:
    """Return current local time in the given IANA timezone as a formatted string."""
    try:
        tz = pytz.timezone(timezone_name)
        now = datetime.now(tz)
        return now.strftime("%H:%M:%S (%A, %d %b %Y)")
    except pytz.UnknownTimeZoneError:
        return "Unknown"


# ---------------------------------------------------------------------------
# /settimezone
# ---------------------------------------------------------------------------

async def settimezone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Set the user's timezone by city name or IANA string.

    Usage:
        /settimezone Aden
        /settimezone Asia/Riyadh
        /settimezone London
        /settimezone (no args)  → shows help + location-share button in private chat
    """
    message = update.effective_message
    user = update.effective_user
    args = context.args or []

    if not args:
        # No argument — show instructions.
        help_text = (
            "<b>🌐 Set Your Timezone</b>\n\n"
            "You can set your timezone in two ways:\n\n"
            "1️⃣ <b>Type your city name:</b>\n"
            "   <code>/settimezone Aden</code>\n"
            "   <code>/settimezone Riyadh</code>\n"
            "   <code>/settimezone London</code>\n\n"
            "2️⃣ <b>Share your location</b> (private chat only):\n"
            "   The bot detects your timezone automatically.\n"
            "   <i>Only the timezone label is saved — GPS coordinates are never stored.</i>\n\n"
            "3️⃣ <b>Use an IANA timezone string:</b>\n"
            "   <code>/settimezone Asia/Aden</code>\n"
            "   <code>/settimezone Europe/London</code>"
        )

        # In private chat, offer a location-share keyboard.
        if update.effective_chat.type == "private":
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("📍 Share my location", request_location=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            )
            await message.reply_html(help_text, reply_markup=keyboard)
        else:
            await message.reply_html(help_text)
        return

    query = " ".join(args).strip()

    # Try IANA string first (contains "/" like "Asia/Aden").
    if "/" in query:
        match = resolve_timezone_name(query)
        if match:
            await _save_timezone(
                user.id, user.first_name or "", match.timezone, match.city_label
            )
            local_time = _format_local_time(match.timezone)
            await message.reply_html(
                f"✅ Timezone set to <b>{match.city_label}</b> "
                f"(<code>{match.timezone}</code>)\n"
                f"🕐 Your local time: <b>{local_time}</b>"
            )
            return
        else:
            await message.reply_text(f"'{query}' is not a valid IANA timezone string.")
            return

    # Try city name lookup.
    match = resolve_city(query)
    if match:
        await _save_timezone(
            user.id, user.first_name or "", match.timezone, match.city_label
        )
        local_time = _format_local_time(match.timezone)
        country_line = f" ({match.country})" if match.country else ""
        await message.reply_html(
            f"✅ Timezone set to <b>{match.city_label}</b>{country_line}\n"
            f"📍 Timezone: <code>{match.timezone}</code>\n"
            f"🕐 Your local time: <b>{local_time}</b>"
        )
        return

    # No exact match — show suggestions.
    suggestions = all_suggestions(query, limit=5)
    if suggestions:
        lines = [f"❓ City not found for <b>{query}</b>. Did you mean:\n"]
        for s in suggestions:
            lines.append(
                f"  • /settimezone {s.city_label.replace(' ', '_')} "
                f"— {s.city_label} ({s.country})"
            )
        await message.reply_html("\n".join(lines))
    else:
        await message.reply_html(
            f"❓ City <b>{query}</b> not found.\n\n"
            f"Try the full city name, or use the IANA format:\n"
            f"<code>/settimezone Asia/Aden</code>"
        )


# ---------------------------------------------------------------------------
# /mytimezone
# ---------------------------------------------------------------------------

async def mytimezone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show the user's stored timezone and current local time.

    Usage:
        /mytimezone
    """
    message = update.effective_message
    user = update.effective_user

    tz_row = await _get_user_tz(user.id)
    if tz_row is None:
        await message.reply_html(
            "You haven't set a timezone yet.\n"
            "Use <code>/settimezone &lt;city&gt;</code> or share your location."
        )
        return

    local_time = _format_local_time(tz_row.timezone_name)
    await message.reply_html(
        f"<b>Your Timezone</b>\n\n"
        f"📍 City: <b>{tz_row.city_label}</b>\n"
        f"🌐 Timezone: <code>{tz_row.timezone_name}</code>\n"
        f"🕐 Local time: <b>{local_time}</b>"
    )


# ---------------------------------------------------------------------------
# /cleartimezone
# ---------------------------------------------------------------------------

async def cleartimezone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove stored timezone data for the calling user."""
    message = update.effective_message
    user = update.effective_user

    async with get_session() as session:
        tz_row = await session.get(UserTimezone, user.id)
        if tz_row is None:
            await message.reply_text("No timezone data found for your account.")
            return
        await session.delete(tz_row)

    await message.reply_html(
        "✅ Your timezone data has been removed.\n"
        "Use /settimezone to set it again."
    )


# ---------------------------------------------------------------------------
# Location handler (private chat)
# ---------------------------------------------------------------------------

async def location_timezone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle location messages shared by the user in a private chat.

    Workflow:
    1. Extract lat/lng from the Telegram location object.
    2. Use timezonefinder (offline) to determine the IANA timezone.
    3. Store timezone_name + derived city label — GPS coords discarded immediately.
    4. Confirm to the user with local time.
    """
    message = update.effective_message
    user = update.effective_user

    if not message.location:
        return

    lat: float = message.location.latitude
    lng: float = message.location.longitude

    # --- Timezone resolution (offline, no external API) ---
    try:
        from timezonefinder import TimezoneFinder
        tf = TimezoneFinder()
        tz_name: Optional[str] = tf.timezone_at(lat=lat, lng=lng)
    except ImportError:
        await message.reply_text(
            "Location detection unavailable. Please type your city name:\n"
            "/settimezone Aden"
        )
        return

    # Immediately discard coordinates — we only work with the timezone string.
    del lat, lng

    if not tz_name:
        await message.reply_html(
            "⚠️ Couldn't determine timezone from this location.\n"
            "Please type your city name: <code>/settimezone Aden</code>"
        )
        return

    # Derive a friendly city label.
    match = resolve_timezone_name(tz_name)
    city_label = match.city_label if match else tz_name.split("/")[-1].replace("_", " ")
    country = match.country if match else ""

    await _save_timezone(user.id, user.first_name or "", tz_name, city_label)

    local_time = _format_local_time(tz_name)
    country_line = f" ({country})" if country else ""

    # Dismiss the location-share keyboard.
    await message.reply_html(
        f"✅ Timezone detected and saved!\n\n"
        f"📍 City: <b>{city_label}</b>{country_line}\n"
        f"🌐 Timezone: <code>{tz_name}</code>\n"
        f"🕐 Your local time: <b>{local_time}</b>\n\n"
        f"<i>Your GPS coordinates were not stored.</i>",
        reply_markup=ReplyKeyboardRemove(),
    )
    log.info(
        "User %s set timezone via location: %s (%s)", user.id, tz_name, city_label
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register timezone commands and the location handler."""
    application.add_handler(CommandHandler("settimezone", settimezone))
    application.add_handler(CommandHandler("mytimezone", mytimezone))
    application.add_handler(CommandHandler("cleartimezone", cleartimezone))

    # Location messages — only in private chat (privacy: we don't track location in groups).
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.LOCATION,
            location_timezone,
        )
    )
    log.info("Plugin loaded: timezone_cmd")
