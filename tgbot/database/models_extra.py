"""
database/models_extra.py — Extended ORM models for Task 2 plugins.

All models here supplement the Task 1 schema in models.py.  They are
auto-discovered by init_db() because engine.py imports this module
before calling create_all().

Covers:
- Notes (name/value/type/buttons)
- Custom keyword→reply Filters + buttons
- Warn keyword→auto-warn Filters
- Per-chat Content Locks and Message Restrictions
- Global Ban list + per-chat gban toggle
- Disabled command registry
- Log channel configuration
- Welcome/goodbye media settings + buttons
- Word blacklist
- Anti-link settings (URL/invite blocking per chat)
- Anti-raid settings (mass-join detection and auto-lockdown)
- Approved users (filter bypass whitelist)
- Federation system (cross-group ban sharing)
- Report settings (per-chat report toggle)
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NoteType(int, enum.Enum):
    """
    Message content type for notes, custom filters, and welcome messages.

    Stored as Integer in the database so the value is driver-agnostic
    (avoids Postgres-only ENUM dependencies and simplifies SQLite dev).
    """
    TEXT = 1            # Plain text with optional Markdown
    BUTTON_TEXT = 2     # Text + InlineKeyboard buttons
    STICKER = 3         # Telegram sticker (file_id)
    DOCUMENT = 4        # Any document / file
    PHOTO = 5           # Photo (stores highest-quality file_id)
    AUDIO = 6           # Audio track
    VOICE = 7           # Voice message
    VIDEO = 8           # Video file


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class Note(Base):
    """
    A named snippet (text, media, or forwarded message) saved per group.

    Retrieved by /get <name> or the #<name> shortcut.
    If ``is_reply`` is True, ``value`` stores the original message_id to be
    forwarded; otherwise ``value`` holds the note text.
    """

    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint("chat_id", "name", name="uq_note_chat_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Lowercase note name used as the retrieval key.",
    )
    value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="Note text or original message_id (when is_reply=True).",
    )
    file_id: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Telegram file_id for non-text note types.",
    )
    is_reply: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, value is a message_id to forward rather than text.",
    )
    has_buttons: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True when this note has associated inline keyboard buttons.",
    )
    msg_type: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=NoteType.TEXT,
        comment="NoteType integer value determining how the note is sent.",
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram user_id of the admin who created this note.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    buttons: Mapped[list[NoteButton]] = relationship(
        "NoteButton",
        back_populates="note",
        cascade="all, delete-orphan",
        order_by="NoteButton.id",
    )

    def __repr__(self) -> str:
        return f"<Note chat={self.chat_id} name={self.name!r}>"


class NoteButton(Base):
    """Inline keyboard button attached to a Note."""

    __tablename__ = "note_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    button_name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    same_line: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, this button is placed on the same row as the previous one.",
    )

    note: Mapped[Note] = relationship("Note", back_populates="buttons")

    def __repr__(self) -> str:
        return f"<NoteButton note={self.note_id} name={self.button_name!r}>"


# ---------------------------------------------------------------------------
# Custom Filters
# ---------------------------------------------------------------------------

class CustomFilter(Base):
    """
    A keyword → reply mapping stored per chat.

    When any non-admin message contains the keyword (word-boundary match),
    the bot replies with the configured content.
    """

    __tablename__ = "custom_filters"
    __table_args__ = (
        UniqueConstraint("chat_id", "keyword", name="uq_filter_chat_keyword"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Trigger keyword stored in lowercase.",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="Reply text or file_id (for media filters).",
    )
    file_id: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Telegram file_id for non-text filter responses.",
    )
    has_buttons: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    msg_type: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=NoteType.TEXT,
        comment="NoteType integer value for the reply content type.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    buttons: Mapped[list[FilterButton]] = relationship(
        "FilterButton",
        back_populates="custom_filter",
        cascade="all, delete-orphan",
        order_by="FilterButton.id",
    )

    def __repr__(self) -> str:
        return f"<CustomFilter chat={self.chat_id} keyword={self.keyword!r}>"


class FilterButton(Base):
    """Inline keyboard button attached to a CustomFilter reply."""

    __tablename__ = "filter_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filter_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("custom_filters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    button_name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    same_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    custom_filter: Mapped[CustomFilter] = relationship(
        "CustomFilter", back_populates="buttons"
    )


# ---------------------------------------------------------------------------
# Warn Filters (keyword → auto-warn)
# ---------------------------------------------------------------------------

class WarnFilter(Base):
    """
    A keyword that automatically issues a warning when detected in a message.

    Loaded into an in-memory dict on startup for fast O(1) chat lookup.
    The dict is invalidated and reloaded whenever a filter is added/removed.
    """

    __tablename__ = "warn_filters"
    __table_args__ = (
        UniqueConstraint("chat_id", "keyword", name="uq_warnfilter_chat_keyword"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Trigger keyword (lowercase, matched with word boundaries).",
    )
    reply_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="Optional custom message included with the automated warning.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<WarnFilter chat={self.chat_id} keyword={self.keyword!r}>"


# ---------------------------------------------------------------------------
# Content Locks (per-chat lock state for 14 message types)
# ---------------------------------------------------------------------------

class LockSettings(Base):
    """
    Per-chat boolean lock state for each of the 14 lockable message types.

    When a type is locked (True), the bot deletes matching messages sent by
    non-admins.  Defaults to all unlocked (False).
    """

    __tablename__ = "lock_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
        comment="One-to-one with Chat.",
    )
    sticker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    document: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    videonote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    photo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    url: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forward: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    game: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    location: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    poll: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<LockSettings chat={self.chat_id}>"


class RestrictionSettings(Base):
    """
    Per-chat boolean restriction state for the four broad restriction categories.

    When a restriction is active (True), non-admins cannot send that class of
    content.  Defaults to all unrestricted (False).
    """

    __tablename__ = "restriction_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    messages: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="All text messages (includes commands).",
    )
    media: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Audio, documents, photos, videos, videonotes, voice.",
    )
    other: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Games, stickers, GIFs.",
    )
    preview: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Web page link previews.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<RestrictionSettings chat={self.chat_id}>"


# ---------------------------------------------------------------------------
# Global Bans
# ---------------------------------------------------------------------------

class GlobalBannedUser(Base):
    """
    Users banned globally by the bot owner or sudo users.

    On detection (message or join), the user is kicked from every group
    the bot manages that has gban enforcement enabled.
    """

    __tablename__ = "global_banned_users"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="Telegram user_id of the globally banned user.",
    )
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
        comment="First name or username at the time of the ban.",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for the global ban.",
    )
    banned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<GlobalBannedUser user_id={self.user_id}>"


class ChatGbanToggle(Base):
    """
    Per-chat opt-in/opt-out of global ban enforcement.

    Defaults to enabled (gban_enabled=True).  Group admins can disable gban
    enforcement for their specific group via /gbanstat off.
    """

    __tablename__ = "chat_gban_toggles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    gban_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<ChatGbanToggle chat={self.chat_id} enabled={self.gban_enabled}>"


# ---------------------------------------------------------------------------
# Disabled Commands
# ---------------------------------------------------------------------------

class DisabledCommand(Base):
    """
    Commands disabled for regular users in a specific chat.

    Admin-flagged commands (admin_ok=True) still work for admins even when
    present in this table.
    """

    __tablename__ = "disabled_commands"
    __table_args__ = (
        UniqueConstraint("chat_id", "command", name="uq_disabled_chat_command"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Command name without the leading '/' character.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<DisabledCommand chat={self.chat_id} cmd={self.command!r}>"


# ---------------------------------------------------------------------------
# Log Channel Settings
# ---------------------------------------------------------------------------

class LogChannelSettings(Base):
    """
    Maps a Telegram group to its designated log channel.

    The @loggable decorator reads this table and forwards action strings to
    the configured channel after each moderation event.
    """

    __tablename__ = "log_channel_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Group chat_id that owns this log channel configuration.",
    )
    log_channel_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Telegram channel chat_id where log messages are sent.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<LogChannelSettings chat={self.chat_id} log={self.log_channel_id}>"


# ---------------------------------------------------------------------------
# Welcome / Goodbye Settings (media-aware extension)
# ---------------------------------------------------------------------------

class WelcomeSettings(Base):
    """
    Extended welcome/goodbye configuration supporting media content types.

    Supplements ``ChatFeatureSettings`` which only stores plain text.
    When ``msg_type`` is not NoteType.TEXT, ``file_id`` is used instead of
    ``message_text`` when sending the welcome/goodbye.
    """

    __tablename__ = "welcome_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Welcome configuration
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    welcome_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Welcome message text with optional {variable} placeholders.",
    )
    welcome_file_id: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Telegram file_id for media-type welcome messages.",
    )
    welcome_msg_type: Mapped[int] = mapped_column(
        Integer, nullable=False, default=NoteType.TEXT
    )
    clean_welcome: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, previous welcome message is deleted on each new join.",
    )
    last_welcome_msg_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="message_id of the last sent welcome message (for clean_welcome).",
    )

    # Goodbye configuration
    goodbye_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    goodbye_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goodbye_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    goodbye_msg_type: Mapped[int] = mapped_column(
        Integer, nullable=False, default=NoteType.TEXT
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    welcome_buttons: Mapped[list[WelcomeButton]] = relationship(
        "WelcomeButton",
        back_populates="settings",
        cascade="all, delete-orphan",
        primaryjoin="and_(WelcomeSettings.chat_id == WelcomeButton.chat_id)",
    )

    def __repr__(self) -> str:
        return f"<WelcomeSettings chat={self.chat_id}>"


class WelcomeButton(Base):
    """Inline keyboard button for welcome or goodbye messages."""

    __tablename__ = "welcome_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("welcome_settings.chat_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    msg_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Either 'welcome' or 'goodbye' — identifies which message this button belongs to.",
    )
    button_name: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    same_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    settings: Mapped[WelcomeSettings] = relationship(
        "WelcomeSettings",
        back_populates="welcome_buttons",
        foreign_keys=[chat_id],
    )


# ---------------------------------------------------------------------------
# Word Blacklist
# ---------------------------------------------------------------------------

class BlacklistEntry(Base):
    """
    A single blacklisted word or phrase for a specific chat.

    Any non-admin message containing the trigger (word-boundary match,
    case-insensitive) is automatically deleted.
    """

    __tablename__ = "blacklist_entries"
    __table_args__ = (
        UniqueConstraint("chat_id", "trigger", name="uq_blacklist_chat_trigger"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Blacklisted word or phrase (stored lowercase).",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<BlacklistEntry chat={self.chat_id} trigger={self.trigger!r}>"


# ---------------------------------------------------------------------------
# Anti-Link Settings
# ---------------------------------------------------------------------------

class AntiLinkMode(str, enum.Enum):
    """
    Scope of URL blocking enforced by the anti-links plugin.

    OFF      — feature disabled; no URLs are blocked.
    INVITE   — block Telegram invite links only (t.me/joinchat/..., t.me/+...).
    ALL      — block every hyperlink found in the message text or caption.
    """
    OFF = "off"
    INVITE = "invite"
    ALL = "all"


class AntiLinkAction(str, enum.Enum):
    """Action taken when a prohibited link is detected."""
    DELETE = "delete"           # Delete the message silently.
    DELETE_WARN = "delete_warn" # Delete and issue a warning.
    DELETE_MUTE = "delete_mute" # Delete and mute the sender.
    DELETE_BAN = "delete_ban"   # Delete and ban the sender.


class AntiLinkSettings(Base):
    """
    Per-chat configuration for the anti-links enforcement plugin.

    Administrators can choose to block all URLs, only Telegram invite links,
    or disable the feature entirely.  Each chat independently selects the
    enforcement action (delete-only, delete+warn, delete+mute, delete+ban).
    """

    __tablename__ = "antilink_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Group chat_id this configuration applies to.",
    )
    mode: Mapped[AntiLinkMode] = mapped_column(
        String(16),
        nullable=False,
        default=AntiLinkMode.OFF,
        comment="URL blocking scope: off | invite | all.",
    )
    action: Mapped[AntiLinkAction] = mapped_column(
        String(24),
        nullable=False,
        default=AntiLinkAction.DELETE,
        comment="Enforcement action when a prohibited link is detected.",
    )
    # When True, admins are also subject to link filtering (unusual but supported).
    apply_to_admins: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, admin messages are also scanned. Defaults to False.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiLinkSettings chat={self.chat_id} mode={self.mode}>"


# ---------------------------------------------------------------------------
# Anti-Raid Settings
# ---------------------------------------------------------------------------

class AntiRaidSettings(Base):
    """
    Per-chat configuration for the anti-raid mass-join detection plugin.

    A "raid" is detected when more than ``join_threshold`` new members join
    within ``time_window_seconds``.  On detection the bot can:
    - Enable CAPTCHA for all new joiners for the lockdown period.
    - Kick all members who joined during the raid window.
    - Send an alert to the log channel.

    Settings are stored in the database so they survive bot restarts.
    The in-memory join counter is intentionally ephemeral — it resets on
    restart, which is acceptable for a real-time detection mechanism.
    """

    __tablename__ = "antiraid_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Master toggle for anti-raid protection.",
    )
    # Number of joins within time_window_seconds that triggers a raid alert.
    join_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10,
        comment="Minimum simultaneous joins within the window to trigger raid mode.",
    )
    time_window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60,
        comment="Sliding window in seconds for counting rapid joins.",
    )
    # Duration of the automatic lockdown once a raid is detected.
    lockdown_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300,
        comment="How long (seconds) the lockdown remains active after a raid is detected.",
    )
    # Action applied to each raider during lockdown.
    kick_raiders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="If True, members who joined during the raid window are kicked automatically.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiRaidSettings chat={self.chat_id} enabled={self.enabled}>"


# ---------------------------------------------------------------------------
# Approved Users (filter bypass whitelist)
# ---------------------------------------------------------------------------

class ApprovedUser(Base):
    """
    A user explicitly approved by an admin to bypass all automated filters
    in a specific chat.

    Approved users are exempt from: Bayesian spam filter, regex/word filters,
    anti-flood, anti-links, blacklist, and CAPTCHA.  They are NOT exempt from
    manual moderation commands (/ban, /warn, etc.).

    The (chat_id, user_id) pair is unique — a user can be approved in some
    groups but not others.
    """

    __tablename__ = "approved_users"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_approved_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Telegram ID of the admin who approved this user.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ApprovedUser chat={self.chat_id} user={self.user_id}>"


# ---------------------------------------------------------------------------
# Federation System (cross-group ban sharing)
# ---------------------------------------------------------------------------

class Federation(Base):
    """
    A named federation that groups multiple Telegram chats together so that
    a single /fban command propagates across all member chats.

    Each federation is identified by a UUID stored as a string for
    readability in commands.  The creator is the initial federation admin.
    """

    __tablename__ = "federations"

    fed_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        comment="UUID string identifying this federation (e.g. '550e8400-e29b-...').",
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False,
        comment="Human-readable federation name set by the creator.",
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="Telegram user_id of the federation creator/owner.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    admins: Mapped[list["FedAdmin"]] = relationship(
        "FedAdmin", back_populates="federation", cascade="all, delete-orphan"
    )
    bans: Mapped[list["FedBan"]] = relationship(
        "FedBan", back_populates="federation", cascade="all, delete-orphan"
    )
    chats: Mapped[list["ChatFed"]] = relationship(
        "ChatFed", back_populates="federation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Federation id={self.fed_id!r} name={self.name!r}>"


class FedAdmin(Base):
    """
    A user who has been granted federation admin rights by the federation owner.

    Federation admins can use /fban and /funban within the federation but
    cannot delete the federation or add/remove other admins.
    """

    __tablename__ = "fed_admins"
    __table_args__ = (
        UniqueConstraint("fed_id", "user_id", name="uq_fed_admin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fed_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("federations.fed_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    added_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    federation: Mapped[Federation] = relationship("Federation", back_populates="admins")

    def __repr__(self) -> str:
        return f"<FedAdmin fed={self.fed_id!r} user={self.user_id}>"


class FedBan(Base):
    """
    A single federation-level ban entry.

    When an /fban is issued, one FedBan row is created and the offending user
    is subsequently banned from every chat that has joined the federation.
    Existing bans are propagated to new chats that join after the ban was issued.
    """

    __tablename__ = "fed_bans"
    __table_args__ = (
        UniqueConstraint("fed_id", "user_id", name="uq_fed_ban"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fed_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("federations.fed_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    banned_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Telegram ID of the federation admin who issued this ban.",
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    federation: Mapped[Federation] = relationship("Federation", back_populates="bans")

    def __repr__(self) -> str:
        return f"<FedBan fed={self.fed_id!r} user={self.user_id}>"


class ChatFed(Base):
    """
    Maps a Telegram chat to the federation it has joined.

    A chat can belong to at most one federation at a time.  When a chat joins
    a federation, all existing FedBan entries for that federation are
    immediately enforced in the new chat.
    """

    __tablename__ = "chat_feds"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    fed_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("federations.fed_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    federation: Mapped[Federation] = relationship("Federation", back_populates="chats")

    def __repr__(self) -> str:
        return f"<ChatFed chat={self.chat_id} fed={self.fed_id!r}>"


# ---------------------------------------------------------------------------
# Report Settings
# ---------------------------------------------------------------------------

class ReportSettings(Base):
    """
    Per-chat configuration for the /report user command.

    When enabled, any member can reply to a message with /report to notify
    all current group administrators via a private mention message.
    """

    __tablename__ = "report_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="If True, members can use /report to alert admins.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ReportSettings chat={self.chat_id} enabled={self.enabled}>"


# ---------------------------------------------------------------------------
# User Timezone (per-user, privacy-respecting)
# ---------------------------------------------------------------------------

class UserTimezone(Base):
    """
    Stores the user's self-reported timezone.

    The user sets this by:
    a) Typing a city name  → bot resolves it to an IANA timezone string.
    b) Sharing a location  → bot uses timezonefinder offline to determine
       the IANA timezone, then stores only the city/zone label — the raw
       GPS coordinates are NEVER persisted.

    The timezone_name field holds a valid pytz/IANA string (e.g. 'Asia/Aden').
    The city_label field holds the human-readable name shown back to the user.
    """

    __tablename__ = "user_timezones"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Telegram user_id this timezone belongs to.",
    )
    timezone_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="IANA timezone string, e.g. 'Asia/Aden', 'Europe/London'.",
    )
    city_label: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Human-readable city/region label shown to the user.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UserTimezone user={self.user_id} tz={self.timezone_name!r}>"


# ---------------------------------------------------------------------------
# Night Mode Settings (per-group)
# ---------------------------------------------------------------------------

class NightModeAction(str, enum.Enum):
    """
    How the bot enforces night mode restrictions.

    MUTE_NEW    — Restrict only new members who join during night hours.
    RESTRICT    — Mute ALL non-admin members (restrict send_messages = False).
    LOCK        — Disable messaging for the whole chat via set_chat_permissions.
    """
    MUTE_NEW = "mute_new"
    RESTRICT = "restrict"
    LOCK = "lock"


class NightModeSettings(Base):
    """
    Per-group night mode configuration.

    Night mode automatically restricts a group between start_time and end_time
    in the group's configured timezone.  A job scheduler (APScheduler via PTB's
    JobQueue) checks the time every minute and toggles restrictions accordingly.

    Times are stored as (hour, minute) pairs and interpreted in timezone_name.
    When timezone_name is None the bot falls back to UTC.
    """

    __tablename__ = "nightmode_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Master toggle — True means night mode is active.",
    )
    # Start of night restriction window (local time in timezone_name).
    start_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=23,
        comment="Hour (0-23) at which night restrictions begin.",
    )
    start_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Minute (0-59) at which night restrictions begin.",
    )
    # End of night restriction window (local time in timezone_name).
    end_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6,
        comment="Hour (0-23) at which night restrictions are lifted.",
    )
    end_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Minute (0-59) at which night restrictions are lifted.",
    )
    timezone_name: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        comment="IANA timezone for interpreting start/end times. NULL = UTC.",
    )
    city_label: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="Human-readable city label for the configured timezone.",
    )
    action: Mapped[NightModeAction] = mapped_column(
        String(16),
        nullable=False,
        default=NightModeAction.LOCK,
        comment="Restriction type applied during night hours.",
    )
    # Track whether we're currently in a restricted state to avoid redundant API calls.
    currently_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True when night restrictions are currently applied to this chat.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<NightModeSettings chat={self.chat_id} "
            f"enabled={self.enabled} "
            f"tz={self.timezone_name!r}>"
        )


# ---------------------------------------------------------------------------
# Activity Statistics
# ---------------------------------------------------------------------------

class UserDailyStat(Base):
    """
    Daily message count per user per chat.

    Incremented by the stats plugin on every non-command message.
    One row per (chat_id, user_id, date) — upserted atomically.
    Used to generate leaderboards and daily activity charts.
    """

    __tablename__ = "user_daily_stats"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", "stat_date", name="uq_user_daily_stat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    stat_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="UTC date (time portion is always 00:00:00) of this stat bucket.",
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Total messages sent by this user in this chat on this date.",
    )
    # Cache the display name at record-write time to avoid extra API lookups.
    display_name: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Cached full name/username at the time of last update.",
    )

    def __repr__(self) -> str:
        return (
            f"<UserDailyStat chat={self.chat_id} user={self.user_id} "
            f"date={self.stat_date.date()} count={self.message_count}>"
        )


class ChatHourlyStat(Base):
    """
    Accumulated message count per hour-of-day per chat.

    Each of the 24 rows (one per hour) accumulates across all time so the
    peak-hour graph shows the all-time busiest hours for a group.
    Rows are created on first message in that hour slot and incremented
    thereafter — never reset, so the trend is meaningful over time.
    """

    __tablename__ = "chat_hourly_stats"
    __table_args__ = (
        UniqueConstraint("chat_id", "hour", name="uq_chat_hourly_stat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Hour of day (0–23, UTC) this slot represents.",
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Total messages ever sent in this chat during this UTC hour.",
    )

    def __repr__(self) -> str:
        return f"<ChatHourlyStat chat={self.chat_id} hour={self.hour:02d}:xx count={self.message_count}>"


# ---------------------------------------------------------------------------
# AFK System
# ---------------------------------------------------------------------------

class UserAFK(Base):
    """
    Stores AFK status for users across all groups.

    One row per user — the status is global (not per-chat) so that if a
    user is mentioned in any group they're in, the bot can notify the
    mentioner regardless of which chat the mention happens in.

    Row is deleted when the user sends any message (auto-clear) or explicitly
    runs /afk off.
    """

    __tablename__ = "user_afk"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Telegram user ID — primary key (one AFK record per user).",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        comment="Optional reason the user provided when going AFK.",
    )
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="UTC timestamp when the user set their AFK status.",
    )

    def __repr__(self) -> str:
        return f"<UserAFK user={self.user_id} since={self.since} reason={self.reason!r}>"


# ---------------------------------------------------------------------------
# Clean Service Settings
# ---------------------------------------------------------------------------

class CleanServiceSettings(Base):
    """
    Per-chat configuration for automatic deletion of Telegram service messages.

    Each boolean toggle controls a specific service message type.
    The master 'enabled' flag gates all other toggles — when False, nothing
    is ever deleted regardless of the individual toggles.
    """

    __tablename__ = "clean_service_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The group this configuration belongs to.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Master toggle — enables all configured service message cleanup.",
    )
    clean_joins: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Delete 'X joined the group' service messages.",
    )
    clean_leaves: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Delete 'X left the group' and 'X was removed' service messages.",
    )
    clean_pins: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Delete 'Message was pinned' service notifications.",
    )
    clean_chatname: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Delete 'Group name/photo changed' service messages.",
    )

    def __repr__(self) -> str:
        return (
            f"<CleanServiceSettings chat={self.chat_id} "
            f"enabled={self.enabled} joins={self.clean_joins} leaves={self.clean_leaves}>"
        )


# ---------------------------------------------------------------------------
# Temporary Promotions
# ---------------------------------------------------------------------------

class TempPromotion(Base):
    """
    Tracks temporary admin promotions that must be automatically reversed.

    When /tpromote is used, the bot promotes the user and stores a record here.
    A PTB JobQueue job is scheduled to demote the user at expires_at.
    On bot restart, the slowmode plugin re-schedules all pending demotions.
    """

    __tablename__ = "temp_promotions"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_temp_promotion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    promoted_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Telegram ID of the admin who issued the /tpromote command.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="UTC datetime when this temporary promotion expires.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<TempPromotion chat={self.chat_id} user={self.user_id} "
            f"expires={self.expires_at}>"
        )


# ---------------------------------------------------------------------------
# Raid Fingerprints
# ---------------------------------------------------------------------------

class RaidFingerprint(Base):
    """
    Stores behavioral fingerprints of detected raids for future prevention.

    When a raid is detected in a group, the bot analyzes the raiding accounts
    and saves distinguishing patterns here.  Future joins are checked against
    stored fingerprints to catch recurring raid campaigns before they escalate.

    Pattern fields are all optional — only those with sufficient signal are set.
    """

    __tablename__ = "raid_fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Chat where this raid fingerprint was collected.",
    )
    # Account characteristics observed during the raid.
    avg_account_age_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Average age of raider accounts in days at time of raid.",
    )
    # Regex pattern that matched most raider usernames/names (auto-generated).
    username_pattern: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True,
        comment="Regex pattern matching raider display names, if discernible.",
    )
    # How many accounts joined in the detected window.
    raider_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Number of accounts that joined during the raid event.",
    )
    # Average milliseconds between successive joins (tight = bot-driven).
    avg_join_interval_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Average time between successive joins during the raid (ms).",
    )
    # Tracking
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="UTC timestamp of the first detected raid matching this fingerprint.",
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="UTC timestamp of the most recent raid matching this fingerprint.",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="How many times a raid matching this fingerprint has been detected.",
    )

    def __repr__(self) -> str:
        return (
            f"<RaidFingerprint id={self.id} chat={self.chat_id} "
            f"raiders={self.raider_count} hits={self.hit_count}>"
        )


# ---------------------------------------------------------------------------
# i18n — Chat Language Preference
# ---------------------------------------------------------------------------

class ChatLanguage(Base):
    """Per-chat language preference for bot responses (i18n)."""

    __tablename__ = "chat_languages"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lang_code: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="en",
        comment="BCP-47 language code (en, ar, fa, tr, ru, id).",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ChatLanguage chat={self.chat_id} lang={self.lang_code}>"


# ---------------------------------------------------------------------------
# Warn Reasons (predefined per-chat warn reasons)
# ---------------------------------------------------------------------------

class WarnReason(Base):
    """Admin-defined warn reason presets for a chat."""

    __tablename__ = "warn_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Predefined warn reason text shown as an inline button.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<WarnReason chat={self.chat_id} reason={self.reason!r}>"


# ---------------------------------------------------------------------------
# SpamWatch Settings (per-chat toggle)
# ---------------------------------------------------------------------------

class SpamWatchSettings(Base):
    """Per-chat SpamWatch integration toggle."""

    __tablename__ = "spamwatch_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<SpamWatchSettings chat={self.chat_id} enabled={self.enabled}>"


# ---------------------------------------------------------------------------
# Anti-Astroturfing Settings (per-chat)
# ---------------------------------------------------------------------------

class AstroSettings(Base):
    """Per-chat anti-astroturfing (coordinated spam detection) configuration."""

    __tablename__ = "astro_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30,
        comment="Sliding window duration in seconds for similarity detection.",
    )
    min_users: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
        comment="Minimum distinct users sending the same message to trigger action.",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mute",
        comment="Action on detection: warn | mute | ban.",
    )

    def __repr__(self) -> str:
        return f"<AstroSettings chat={self.chat_id} enabled={self.enabled}>"


# ---------------------------------------------------------------------------
# Adaptive CAPTCHA Settings (per-chat)
# ---------------------------------------------------------------------------

class AdaptiveCaptchaSettings(Base):
    """Enables risk-based adaptive CAPTCHA for a chat."""

    __tablename__ = "adaptive_captcha_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    adaptive_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="When True, CAPTCHA difficulty is scaled by risk score.",
    )

    def __repr__(self) -> str:
        return f"<AdaptiveCaptchaSettings chat={self.chat_id} adaptive={self.adaptive_mode}>"


# ---------------------------------------------------------------------------
# User Risk Score (persistent across sessions)
# ---------------------------------------------------------------------------

class UserRiskScore(Base):
    """Persistent per-user risk score updated by the adaptive CAPTCHA system."""

    __tablename__ = "user_risk_scores"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="Telegram user_id.",
    )
    risk_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Exponential moving average risk score (0–100).",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UserRiskScore user={self.user_id} score={self.risk_score}>"


# ---------------------------------------------------------------------------
# Channel Forward Protection (per-chat)
# ---------------------------------------------------------------------------

class ChannelProtectSettings(Base):
    """Per-chat toggle for blocking forwards from non-whitelisted channels."""

    __tablename__ = "channel_protect_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<ChannelProtectSettings chat={self.chat_id} enabled={self.enabled}>"


class ChannelWhitelist(Base):
    """Channels whose forwards are allowed through channel protect."""

    __tablename__ = "channel_whitelist"
    __table_args__ = (
        UniqueConstraint("chat_id", "channel_id", name="uq_channel_whitelist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="Telegram channel_id that is allowed to be forwarded from.",
    )
    channel_title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="",
        comment="Human-readable channel title cached at whitelist time.",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ChannelWhitelist chat={self.chat_id} channel={self.channel_id}>"


# ---------------------------------------------------------------------------
# Scheduled Messages (daily announcements)
# ---------------------------------------------------------------------------

class ScheduledMessage(Base):
    """A recurring daily message scheduled for a group (HH:MM UTC)."""

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    time_utc: Mapped[str] = mapped_column(
        String(5), nullable=False,
        comment="Send time in HH:MM format, UTC.",
    )
    message_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="HTML-formatted message text to send.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ScheduledMessage chat={self.chat_id} time={self.time_utc}>"


# ---------------------------------------------------------------------------
# Ban Appeals
# ---------------------------------------------------------------------------

class BanAppeal(Base):
    """
    A ban appeal submitted by a banned user.

    Lifecycle: pending → approved | rejected.
    """

    __tablename__ = "ban_appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="Telegram user_id of the appealing user.",
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    appeal_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="The user's appeal message.",
    )
    ban_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="The original ban reason (from BanRecord or manual input).",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending",
        comment="pending | approved | rejected",
    )
    reviewed_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Telegram user_id of the admin who reviewed this appeal.",
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<BanAppeal id={self.id} user={self.user_id} status={self.status}>"


# ---------------------------------------------------------------------------
# Ban Record (tracks bans for appeal system)
# ---------------------------------------------------------------------------

class BanRecord(Base):
    """
    Persistent record of every ban issued by the bot.

    Written by the bans plugin on /ban and /tempban; used by the appeals
    system to verify that a user is actually banned and to surface the
    original ban reason.
    """

    __tablename__ = "ban_records"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_ban_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="Telegram user_id of the banned user.",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Reason provided by the admin when banning.",
    )
    banned_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Telegram user_id of the admin who issued the ban.",
    )
    unbanned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True when the ban has been lifted.",
    )
    banned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    unbanned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<BanRecord chat={self.chat_id} user={self.user_id} unbanned={self.unbanned}>"


# ---------------------------------------------------------------------------
# User Self-Info (/setme / /me)
# ---------------------------------------------------------------------------

class UserInfoData(Base):
    """A user's self-set info blurb (via /setme)."""

    __tablename__ = "user_info_data"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True,
        comment="Telegram user_id.",
    )
    info_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="User-written about-me text.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UserInfoData user={self.user_id}>"


# ---------------------------------------------------------------------------
# User Bio (/setbio / /bio — written by admins about a user)
# ---------------------------------------------------------------------------

class UserBioData(Base):
    """Bio for a user, written by admins (via /setbio @reply)."""

    __tablename__ = "user_bio_data"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True,
        comment="Telegram user_id of the subject.",
    )
    bio_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Admin-written bio text.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UserBioData user={self.user_id}>"


# ---------------------------------------------------------------------------
# RSS Feed Subscriptions
# ---------------------------------------------------------------------------

class RSSFeed(Base):
    """An RSS/Atom feed subscription for a chat."""

    __tablename__ = "rss_feeds"
    __table_args__ = (
        UniqueConstraint("chat_id", "feed_link", name="uq_rss_chat_feed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feed_link: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Full URL of the RSS/Atom feed.",
    )
    last_entry_link: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Link of the most recently sent entry (used for dedup).",
    )
    last_checked: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Timestamp of the last successful poll.",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<RSSFeed chat={self.chat_id} url={self.feed_link[:40]!r}>"


# ---------------------------------------------------------------------------
# Channel Subscription Gate (channel_sub.py)
# ---------------------------------------------------------------------------

class ChannelSubSettings(Base):
    """Per-chat configuration for the channel subscription gate."""

    __tablename__ = "channel_sub_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="Telegram ID of the required subscription channel.",
    )
    channel_username: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="@username of the channel (without @), if public.",
    )
    channel_title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="",
        comment="Human-readable channel title at time of configuration.",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ChannelSubSettings chat={self.chat_id} channel={self.channel_id}>"


# ---------------------------------------------------------------------------
# Anti-NSFW Filter (anti_nsfw.py)
# ---------------------------------------------------------------------------

class AntiNSFWSettings(Base):
    """Per-chat configuration for the NSFW content filter."""

    __tablename__ = "anti_nsfw_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="delete",
        comment="Action on detection: delete | mute | ban",
    )
    scan_captions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Scan media captions for NSFW keywords.",
    )
    scan_stickers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Scan sticker set names for NSFW indicators.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiNSFWSettings chat={self.chat_id} enabled={self.enabled}>"


class AntiNSFWKeyword(Base):
    """Per-chat custom NSFW caption keyword."""

    __tablename__ = "anti_nsfw_keywords"
    __table_args__ = (
        UniqueConstraint("chat_id", "keyword", name="uq_nsfw_chat_keyword"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiNSFWKeyword chat={self.chat_id} kw={self.keyword!r}>"


# ---------------------------------------------------------------------------
# Anti-Forward Filter (anti_forward.py)
# ---------------------------------------------------------------------------

class AntiForwardSettings(Base):
    """Per-chat toggle for the anti-forward filter."""

    __tablename__ = "anti_forward_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiForwardSettings chat={self.chat_id} enabled={self.enabled}>"


class AntiForwardWhitelist(Base):
    """Chats whose forwards are allowed even when the filter is on."""

    __tablename__ = "anti_forward_whitelist"
    __table_args__ = (
        UniqueConstraint("chat_id", "source_chat_id", name="uq_afwd_chat_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_chat_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="Telegram ID of the whitelisted source chat.",
    )
    source_title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AntiForwardWhitelist chat={self.chat_id} src={self.source_chat_id}>"


# ---------------------------------------------------------------------------
# Community Report-to-Delete voting (report_vote.py)
# ---------------------------------------------------------------------------

class ReportVoteSettings(Base):
    """Per-chat configuration for community report voting."""

    __tablename__ = "report_vote_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5,
        comment="Number of votes required to delete the message.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ReportVoteSettings chat={self.chat_id} threshold={self.threshold}>"


class ReportVoteRecord(Base):
    """Single user vote on a specific message for deletion."""

    __tablename__ = "report_vote_records"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", "voter_id", name="uq_rvote_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    voter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ReportVoteRecord chat={self.chat_id} msg={self.message_id} voter={self.voter_id}>"


# ---------------------------------------------------------------------------
# Community Vote-to-Mute (vote_mute.py)
# ---------------------------------------------------------------------------

class VoteMuteSettings(Base):
    """Per-chat configuration for the vote-to-mute system."""

    __tablename__ = "vote_mute_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5,
        comment="Yes votes needed to mute / No votes needed to cancel.",
    )
    default_duration: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600,
        comment="Default mute duration in seconds (1 hour).",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<VoteMuteSettings chat={self.chat_id} threshold={self.threshold}>"


class VoteMuteSession(Base):
    """An active (or recently completed) vote-to-mute session."""

    __tablename__ = "vote_mute_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    target_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    initiator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    yes_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    yes_voter_ids: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Comma-separated list of user IDs who voted Yes.",
    )
    no_voter_ids: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Comma-separated list of user IDs who voted No.",
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600,
        comment="Mute duration in seconds if vote passes.",
    )
    panel_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="Message ID of the vote panel in the group.",
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Whether this vote session is still in progress.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<VoteMuteSession id={self.id} chat={self.chat_id} target={self.target_user_id} active={self.active}>"


# ---------------------------------------------------------------------------
# Account Age Gate — per-chat settings
# ---------------------------------------------------------------------------

class AccountAgeSettings(Base):
    """Minimum Telegram account age required to join this chat."""

    __tablename__ = "account_age_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Whether the account-age gate is active.",
    )
    min_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30,
        comment="Minimum account age in days (estimated from user ID).",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="kick",
        comment="Action when age < min_days: 'kick' or 'restrict'.",
    )

    def __repr__(self) -> str:
        return f"<AccountAgeSettings chat={self.chat_id} enabled={self.enabled} min={self.min_days}d>"


# ---------------------------------------------------------------------------
# Anti-Nuke — per-chat settings
# ---------------------------------------------------------------------------

class AntiNukeSettings(Base):
    """Anti-nuke protection: detect and revert rapid admin promotions."""

    __tablename__ = "anti_nuke_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Whether anti-nuke monitoring is active.",
    )
    threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
        comment="Number of admin promotions within window_seconds to trigger alert.",
    )
    window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60,
        comment="Sliding time window in seconds for counting promotions.",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="alert",
        comment="Response action: 'alert' (notify only) or 'demote' (revert rights).",
    )

    def __repr__(self) -> str:
        return (
            f"<AntiNukeSettings chat={self.chat_id} enabled={self.enabled} "
            f"threshold={self.threshold}/{self.window_seconds}s>"
        )


# ---------------------------------------------------------------------------
# Language Filter — per-chat settings
# ---------------------------------------------------------------------------

class LangFilterSettings(Base):
    """Restrict group messages to one or more Unicode scripts."""

    __tablename__ = "lang_filter_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Whether the language filter is active.",
    )
    allowed_scripts: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="Comma-separated allowed scripts: arabic, latin, cjk.",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="delete",
        comment="Action on violation: 'delete', 'warn', or 'mute'.",
    )

    def __repr__(self) -> str:
        return (
            f"<LangFilterSettings chat={self.chat_id} enabled={self.enabled} "
            f"scripts={self.allowed_scripts!r}>"
        )


# ---------------------------------------------------------------------------
# Phishing Detection — per-chat settings
# ---------------------------------------------------------------------------

class PhishingSettings(Base):
    """Phishing and scam URL detection settings per chat."""

    __tablename__ = "phishing_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Phishing detection is ON by default for new chats.",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="delete",
        comment="Action on detection: 'delete', 'warn', or 'ban'.",
    )
    scan_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Cumulative count of URLs scanned in this chat.",
    )

    def __repr__(self) -> str:
        return f"<PhishingSettings chat={self.chat_id} enabled={self.enabled} action={self.action}>"


# ---------------------------------------------------------------------------
# Gender System — ميزة تحديد الجنس
# ---------------------------------------------------------------------------

class GenderType(str, enum.Enum):
    """نوع الجنس المحدد من قبل المستخدم."""
    MALE   = "male"    # ولد
    FEMALE = "female"  # بنت


class UserGender(Base):
    """
    تخزين جنس المستخدم — مشترك عبر جميع المجموعات.

    يحدد العضو جنسه بنفسه عبر الأوامر النصية ويمكنه حذفه في أي وقت.
    """

    __tablename__ = "user_genders"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Telegram user ID.",
    )
    gender: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="'male' or 'female'.",
    )
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        comment="UTC timestamp when the user last set their gender.",
    )

    def __repr__(self) -> str:
        return f"<UserGender user={self.user_id} gender={self.gender!r}>"


class GenderKeyword(Base):
    """
    كلمات مفتاحية تُفعِّل ردوداً مخصصة حسب جنس المرسل — خاصة بكل مجموعة.

    gender_type = 'male'   → كلمات عيال (تُفعَّل حين يرسلها ولد)
    gender_type = 'female' → كلمات بنات (تُفعَّل حين يرسلها بنت)
    """

    __tablename__ = "gender_keywords"
    __table_args__ = (
        UniqueConstraint("chat_id", "keyword", "gender_type", name="uq_gender_keyword"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="The group this keyword belongs to.",
    )
    keyword: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="The trigger keyword (case-insensitive match).",
    )
    gender_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="'male' or 'female' — which gender this keyword targets.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    def __repr__(self) -> str:
        return (
            f"<GenderKeyword chat={self.chat_id} keyword={self.keyword!r} "
            f"gender={self.gender_type}>"
        )


class GenderResponse(Base):
    """
    ردود مخصصة حسب الجنس لكل مجموعة.

    عندما تُفعَّل كلمة مفتاحية من جنس معين،
    يختار البوت رداً عشوائياً من قائمة ردود ذلك الجنس.
    """

    __tablename__ = "gender_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="The group this response belongs to.",
    )
    gender_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="'male' or 'female'.",
    )
    response_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The reply text sent when this response is picked.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    def __repr__(self) -> str:
        return (
            f"<GenderResponse chat={self.chat_id} gender={self.gender_type} "
            f"text={self.response_text[:30]!r}>"
        )
