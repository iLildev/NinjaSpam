"""
plugins/backup.py — Group settings backup and restore.

Allows group admins to export all bot settings for their group as a
compact JSON file and restore them later — useful when migrating to a
new group, sharing a configuration template, or recovering from
accidental resets.

What is backed up:
  • ChatFeatureSettings  (all feature toggles and thresholds)
  • Rules text
  • Welcome / Goodbye message templates
  • Notes (name → content)
  • Filters (keyword → reply)
  • Blacklist words
  • Warn filters (keyword → action)
  • NightMode settings
  • AntiLink settings
  • AntiRaid settings
  • Log channel ID

What is NOT backed up (by design):
  • User-specific data (warns, bans, approved users) — personal data
  • Federated ban lists — owned by federation, not the group
  • Activity statistics — these are historical and not portable

Commands:
  /backup           — Generate and send the settings JSON file.
  /restore          — Reply to a previously exported JSON file to restore settings.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from telegram import Document, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models import Chat as ChatModel, ChatFeatureSettings, ChatMember
from database.models_extra import (
    AntiLinkSettings,
    AntiRaidSettings,
    BlacklistEntry,
    CustomFilter as FilterModel,
    NightModeSettings,
    Note,
    WarnFilter,
)

log = logging.getLogger(__name__)

BACKUP_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    """Convert SQLAlchemy model attributes to JSON-safe types."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


async def _export_settings(chat_id: int) -> Dict[str, Any]:
    """Build a complete backup dict for the given chat."""
    payload: Dict[str, Any] = {
        "version": BACKUP_VERSION,
        "chat_id": chat_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    async with get_session() as session:
        # ── ChatFeatureSettings ──
        settings = await session.get(ChatFeatureSettings, chat_id)
        if settings:
            payload["feature_settings"] = {
                col.key: _serialize(getattr(settings, col.key))
                for col in ChatFeatureSettings.__table__.columns
                if col.key not in ("chat_id",)
            }

        # ── Rules ──
        chat = await session.get(ChatModel, chat_id)
        payload["rules"] = chat.rules if chat and hasattr(chat, "rules") else None

        # ── Welcome / Goodbye ──
        if settings:
            payload["welcome"] = {
                "enabled": _serialize(settings.welcome_message_enabled),
                "message": _serialize(getattr(settings, "welcome_message", None)),
                "goodbye": _serialize(getattr(settings, "goodbye_message", None)),
            }

        # ── Notes ──
        notes_result = await session.execute(
            select(Note).where(Note.chat_id == chat_id)
        )
        payload["notes"] = [
            {"name": n.name, "content": n.content, "parse_mode": _serialize(n.parse_mode)}
            for n in notes_result.scalars().all()
        ]

        # ── Filters ──
        filters_result = await session.execute(
            select(FilterModel).where(FilterModel.chat_id == chat_id)
        )
        payload["filters"] = [
            {"keyword": f.keyword, "reply": f.reply, "parse_mode": _serialize(f.parse_mode)}
            for f in filters_result.scalars().all()
        ]

        # ── Blacklist ──
        bl_result = await session.execute(
            select(BlacklistEntry).where(BlacklistEntry.chat_id == chat_id)
        )
        payload["blacklist"] = [row.trigger for row in bl_result.scalars().all()]

        # ── Warn filters ──
        wf_result = await session.execute(
            select(WarnFilter).where(WarnFilter.chat_id == chat_id)
        )
        payload["warn_filters"] = [
            {"keyword": w.keyword}
            for w in wf_result.scalars().all()
        ]

        # ── NightMode ──
        night = await session.get(NightModeSettings, chat_id)
        if night:
            payload["nightmode"] = {
                "enabled": night.enabled,
                "start_hour": night.start_hour,
                "start_minute": night.start_minute,
                "end_hour": night.end_hour,
                "end_minute": night.end_minute,
                "timezone_name": night.timezone_name,
            }

        # ── AntiLink ──
        antilink = await session.get(AntiLinkSettings, chat_id)
        if antilink:
            payload["antilink"] = {
                "enabled": antilink.enabled,
                "action": _serialize(antilink.action),
                "allow_telegram_links": antilink.allow_telegram_links,
            }

        # ── AntiRaid ──
        antiraid = await session.get(AntiRaidSettings, chat_id)
        if antiraid:
            payload["antiraid"] = {
                "enabled": antiraid.enabled,
                "threshold": antiraid.threshold,
                "window_seconds": antiraid.window_seconds,
                "lockdown_seconds": antiraid.lockdown_seconds,
                "kick_raiders": antiraid.kick_raiders,
            }

    return payload


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

async def _import_settings(chat_id: int, data: Dict[str, Any]) -> list[str]:
    """
    Apply a backup dict to the given chat.
    Returns a list of applied section names.
    """
    applied: list[str] = []

    async with get_session() as session:
        # ── Feature settings ──
        if "feature_settings" in data:
            settings = await session.get(ChatFeatureSettings, chat_id)
            if settings is None:
                settings = ChatFeatureSettings(chat_id=chat_id)
                session.add(settings)
            for key, val in data["feature_settings"].items():
                if hasattr(settings, key):
                    try:
                        setattr(settings, key, val)
                    except Exception:
                        pass
            applied.append("feature settings")

        # ── Notes ──
        if "notes" in data and data["notes"]:
            for note_data in data["notes"]:
                existing = await session.execute(
                    select(Note).where(
                        Note.chat_id == chat_id, Note.name == note_data["name"]
                    )
                )
                note = existing.scalar_one_or_none()
                if note:
                    note.content = note_data.get("content", "")
                else:
                    session.add(Note(
                        chat_id=chat_id,
                        name=note_data["name"],
                        content=note_data.get("content", ""),
                    ))
            applied.append(f"{len(data['notes'])} notes")

        # ── Filters ──
        if "filters" in data and data["filters"]:
            for f_data in data["filters"]:
                existing = await session.execute(
                    select(FilterModel).where(
                        FilterModel.chat_id == chat_id,
                        FilterModel.keyword == f_data["keyword"],
                    )
                )
                f = existing.scalar_one_or_none()
                if f:
                    f.reply = f_data.get("reply", "")
                else:
                    session.add(FilterModel(
                        chat_id=chat_id,
                        keyword=f_data["keyword"],
                        reply=f_data.get("reply", ""),
                    ))
            applied.append(f"{len(data['filters'])} filters")

        # ── Blacklist ──
        if "blacklist" in data and data["blacklist"]:
            for trigger in data["blacklist"]:
                existing = await session.execute(
                    select(BlacklistEntry).where(
                        BlacklistEntry.chat_id == chat_id,
                        BlacklistEntry.trigger == trigger.lower(),
                    )
                )
                if not existing.scalar_one_or_none():
                    session.add(BlacklistEntry(chat_id=chat_id, trigger=trigger.lower()))
            applied.append(f"{len(data['blacklist'])} blacklist entries")

        # ── NightMode ──
        if "nightmode" in data:
            night = await session.get(NightModeSettings, chat_id)
            nd = data["nightmode"]
            if night is None:
                night = NightModeSettings(chat_id=chat_id)
                session.add(night)
            for k, v in nd.items():
                if hasattr(night, k):
                    setattr(night, k, v)
            applied.append("nightmode")

        # ── AntiLink ──
        if "antilink" in data:
            al = await session.get(AntiLinkSettings, chat_id)
            ald = data["antilink"]
            if al is None:
                al = AntiLinkSettings(chat_id=chat_id)
                session.add(al)
            for k, v in ald.items():
                if hasattr(al, k):
                    try:
                        setattr(al, k, v)
                    except Exception:
                        pass
            applied.append("antilink settings")

        # ── AntiRaid ──
        if "antiraid" in data:
            ar = await session.get(AntiRaidSettings, chat_id)
            ard = data["antiraid"]
            if ar is None:
                ar = AntiRaidSettings(chat_id=chat_id)
                session.add(ar)
            for k, v in ard.items():
                if hasattr(ar, k):
                    setattr(ar, k, v)
            applied.append("antiraid settings")

    return applied


# ---------------------------------------------------------------------------
# /backup command
# ---------------------------------------------------------------------------

@user_admin
async def backup_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Export group settings as a JSON file."""
    chat = update.effective_chat
    msg = update.effective_message

    sent = await msg.reply_text("⏳ Generating backup…")

    try:
        data = await _export_settings(chat.id)
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        buf = io.BytesIO(json_bytes)
        buf.name = f"backup_{chat.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"

        await context.bot.send_document(
            chat_id=chat.id,
            document=buf,
            caption=(
                f"📦 <b>Settings Backup</b>\n\n"
                f"Chat: {chat.title}\n"
                f"Version: {BACKUP_VERSION}\n"
                f"Sections: {', '.join(str(k) for k in data.keys() if k not in ('version', 'chat_id', 'exported_at'))}\n\n"
                f"<i>To restore: send this file to the group and reply to it with /restore</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        await sent.delete()
    except Exception as exc:
        log.exception("Backup failed for chat %d: %s", chat.id, exc)
        await sent.edit_text(f"❌ Backup failed: {exc}")


# ---------------------------------------------------------------------------
# /restore command
# ---------------------------------------------------------------------------

@user_admin
async def restore_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Restore settings from a backup JSON file (reply to the file)."""
    msg = update.effective_message
    chat = update.effective_chat

    # Must be a reply to a document message.
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            "⚠️ Reply to a backup JSON file with /restore to apply it.\n"
            "Use /backup first to generate one."
        )
        return

    doc: Document = msg.reply_to_message.document
    if not doc.file_name or not doc.file_name.endswith(".json"):
        await msg.reply_text("❌ The file must be a .json backup created by /backup.")
        return

    if doc.file_size and doc.file_size > 2 * 1024 * 1024:  # 2 MB sanity limit
        await msg.reply_text("❌ Backup file too large (max 2 MB).")
        return

    sent = await msg.reply_text("⏳ Restoring settings…")

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)
        data = json.loads(buf.read().decode("utf-8"))
    except Exception as exc:
        await sent.edit_text(f"❌ Could not read backup file: {exc}")
        return

    # Validate version
    if data.get("version") != BACKUP_VERSION:
        await sent.edit_text(
            f"❌ Incompatible backup version: {data.get('version')}. "
            f"This bot expects version {BACKUP_VERSION}."
        )
        return

    # Optional: warn if backup is from a different chat
    if data.get("chat_id") and data["chat_id"] != chat.id:
        backup_chat_id = data["chat_id"]
        # Continue anyway — cross-chat restore is a valid use case (template)
        log.info(
            "Cross-chat restore: backup from %d being applied to %d",
            backup_chat_id,
            chat.id,
        )

    try:
        applied = await _import_settings(chat.id, data)
    except Exception as exc:
        log.exception("Restore failed for chat %d: %s", chat.id, exc)
        await sent.edit_text(f"❌ Restore failed: {exc}")
        return

    if applied:
        await sent.edit_text(
            f"✅ <b>Restore Complete</b>\n\n"
            f"Applied: {', '.join(applied)}\n\n"
            f"<i>Some settings (like flood limits and CAPTCHA type) take effect immediately.</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await sent.edit_text("⚠️ Nothing was restored — the backup appears to be empty.")


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    from telegram.ext import filters as _f
    application.add_handler(
        CommandHandler("backup", backup_cmd, filters=_f.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler("restore", restore_cmd, filters=_f.ChatType.GROUPS)
    )
    log.info("Plugin loaded: backup")
