"""
database/models.py — Unified SQLAlchemy ORM models.

Design principles:
- Every model inherits from ``Base`` (defined in engine.py).
- Feature toggle columns are co-located on ``ChatFeatureSettings`` so that a
  group administrator can independently enable/disable each subsystem without
  touching other tables.
- Both the traditional regex-based spam filter AND the Bayesian AI spam filter
  are first-class citizens with separate toggle columns — they coexist and are
  never mutually exclusive (fulfilling the Feature-Intersection requirement).
- ``BayesianToken`` stores the per-chat word frequency tables that power the
  naive Bayes classifier; this keeps training data isolated per group.
- All timestamps are stored as UTC datetimes.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.engine import Base


# ---------------------------------------------------------------------------
# Helper: timezone-aware UTC now
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    """
    Represents the authority level of a user within a specific chat.

    These are distinct from Telegram's native admin status — the bot tracks
    its own permission layer on top of Telegram's so that fine-grained
    feature access can be granted per-group without relying solely on
    Telegram's coarse-grained admin flag.
    """
    OWNER = "owner"       # Typically the group creator; unrestricted access
    ADMIN = "admin"       # Bot-granted admin; can manage bot settings
    MODERATOR = "mod"     # Can action users (ban/mute/warn) but not settings
    MEMBER = "member"     # Ordinary member; no elevated privileges
    BANNED = "banned"     # Banned from interacting with the bot in this chat


class CaptchaType(str, enum.Enum):
    """
    The style of CAPTCHA challenge presented to a new member.

    Multiple CAPTCHA types can be configured per group; the active type is
    stored in ``ChatFeatureSettings.captcha_type``.
    """
    BUTTON = "button"           # Single inline button the user must press
    MATH = "math"               # Simple arithmetic question
    TEXT = "text"               # Type a displayed word/phrase


class WarnAction(str, enum.Enum):
    """
    Action taken automatically when a member accumulates enough warnings.
    """
    NOTHING = "nothing"
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"


class SpamAction(str, enum.Enum):
    """
    Action taken when a message is flagged as spam (by either the regex
    filter or the Bayesian classifier).
    """
    DELETE = "delete"           # Remove the message only
    DELETE_WARN = "delete_warn" # Remove the message and issue a warning
    DELETE_MUTE = "delete_mute" # Remove the message and temporarily mute
    DELETE_BAN = "delete_ban"   # Remove the message and ban the sender


# ---------------------------------------------------------------------------
# Model: User
# ---------------------------------------------------------------------------

class User(Base):
    """
    Represents a Telegram user that has been encountered by the bot.

    A single ``User`` row is shared across all groups — per-group membership
    information is stored in ``ChatMember``.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="Telegram user ID (from the API; globally unique).",
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="Telegram @handle without the '@' prefix.  May be null.",
    )
    first_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        comment="User's Telegram first name.",
    )
    last_name: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="User's Telegram last name.  May be null.",
    )
    is_bot: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True when this record represents a bot account.",
    )

    # Global ban flag — separate from per-chat bans tracked in ChatMember.
    # A globally banned user is ignored by the bot in every group.
    is_globally_banned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Global ban set by a bot owner; enforced across all chats.",
    )
    global_ban_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for the global ban, if any.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        comment="UTC timestamp of the first time this user was seen.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        comment="UTC timestamp of the last profile update.",
    )

    # Relationships
    memberships: Mapped[list[ChatMember]] = relationship(
        "ChatMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    warn_entries: Mapped[list[WarnEntry]] = relationship(
        "WarnEntry",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    captcha_pending: Mapped[list[CaptchaPending]] = relationship(
        "CaptchaPending",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


# ---------------------------------------------------------------------------
# Model: Chat
# ---------------------------------------------------------------------------

class Chat(Base):
    """
    Represents a Telegram group or supergroup managed by the bot.

    The bot only persists chat records for groups/supergroups it is a member
    of.  Individual feature settings are stored in the related
    ``ChatFeatureSettings`` row (one-to-one relationship).
    """

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="Telegram chat ID (negative for groups/supergroups).",
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Current Telegram group title.",
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Public @username of the group, if it has one.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="False when the bot has been removed from the group.",
    )

    # The Telegram user ID of the group creator / primary owner.
    # Stored redundantly here for quick owner lookups without joining ChatMember.
    owner_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Telegram ID of the group's primary owner.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Relationships
    feature_settings: Mapped[Optional[ChatFeatureSettings]] = relationship(
        "ChatFeatureSettings",
        back_populates="chat",
        uselist=False,              # One-to-one
        cascade="all, delete-orphan",
    )
    members: Mapped[list[ChatMember]] = relationship(
        "ChatMember",
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    spam_patterns: Mapped[list[SpamPatternEntry]] = relationship(
        "SpamPatternEntry",
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    bayes_tokens: Mapped[list[BayesianToken]] = relationship(
        "BayesianToken",
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    warn_entries: Mapped[list[WarnEntry]] = relationship(
        "WarnEntry",
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    captcha_pending: Mapped[list[CaptchaPending]] = relationship(
        "CaptchaPending",
        back_populates="chat",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Chat id={self.id} title={self.title!r}>"


# ---------------------------------------------------------------------------
# Model: ChatFeatureSettings
# ---------------------------------------------------------------------------

class ChatFeatureSettings(Base):
    """
    One-to-one extension of ``Chat`` that stores all toggleable feature
    flags and configuration values for a group.

    Architectural decision — Coexistence principle:
    Both traditional regex-based antispam (``regex_filter_enabled``) and the
    Bayesian AI antispam (``bayes_filter_enabled``) are stored as independent
    boolean flags.  They can be active simultaneously, with the middleware
    pipeline running Bayesian analysis first (Phase 1) so that high-confidence
    spam is blocked even if no regex pattern matches it.  A group admin can
    toggle each independently via bot commands.
    """

    __tablename__ = "chat_feature_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="Foreign key back to the parent Chat row.",
    )

    # ------------------------------------------------------------------ #
    # Phase 1 — Bayesian AI Spam Filter (from SpamBayes logic)            #
    # ------------------------------------------------------------------ #

    bayes_filter_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "Toggle for the Bayesian AI spam classifier. "
            "When True, every incoming message is scored before handlers run. "
            "Coexists with regex_filter_enabled — both can be active at once."
        ),
    )
    bayes_spam_action: Mapped[SpamAction] = mapped_column(
        Enum(SpamAction),
        nullable=False,
        default=SpamAction.DELETE_WARN,
        comment="Action taken when Bayes classifier flags a message as spam.",
    )
    # Per-chat override for the global BAYES_SPAM_THRESHOLD config value.
    # NULL means 'use the global config default'.
    bayes_spam_threshold_override: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        default=None,
        comment="Per-chat probability threshold override (0.0 – 1.0).  NULL = use global default.",
    )
    # Track corpus size so the classifier can abstain when data is sparse.
    bayes_ham_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of ham (non-spam) messages used to train this chat's classifier.",
    )
    bayes_spam_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of spam messages used to train this chat's classifier.",
    )

    # ------------------------------------------------------------------ #
    # Phase 1 — Traditional Regex / Word-List Spam Filter                 #
    # Coexists with the Bayesian filter above; neither replaces the other. #
    # ------------------------------------------------------------------ #

    regex_filter_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "Toggle for the traditional pattern/word-list spam filter. "
            "Coexists with bayes_filter_enabled — both can be active at once."
        ),
    )
    regex_spam_action: Mapped[SpamAction] = mapped_column(
        Enum(SpamAction),
        nullable=False,
        default=SpamAction.DELETE_WARN,
        comment="Action taken when a regex/word-list pattern matches a message.",
    )

    # ------------------------------------------------------------------ #
    # Phase 2 — CAPTCHA / New Member Verification (from Shieldy)          #
    # ------------------------------------------------------------------ #

    captcha_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Toggle for CAPTCHA challenges issued to new members.",
    )
    captcha_type: Mapped[CaptchaType] = mapped_column(
        Enum(CaptchaType),
        nullable=False,
        default=CaptchaType.BUTTON,
        comment="Style of CAPTCHA challenge to present (button, math, or text).",
    )
    # Per-chat CAPTCHA timeout override; NULL → use CAPTCHA_TIMEOUT_SECONDS.
    captcha_timeout_override: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        comment="Per-chat CAPTCHA timeout in seconds.  NULL = use global config default.",
    )
    captcha_kick_on_timeout: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="If True, new members who fail the CAPTCHA within the timeout are kicked.",
    )
    # Restrict new members to read-only until CAPTCHA is solved.
    captcha_mute_until_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="If True, new members are muted (send_messages=False) until the CAPTCHA is solved.",
    )

    # ------------------------------------------------------------------ #
    # Phase 3 — Permissions / Warning System (from William Butcher)        #
    # ------------------------------------------------------------------ #

    warn_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        comment="Number of warnings before the configured warn_action is triggered.",
    )
    warn_action: Mapped[WarnAction] = mapped_column(
        Enum(WarnAction),
        nullable=False,
        default=WarnAction.BAN,
        comment="Action triggered when a member reaches warn_limit warnings.",
    )
    # Duration in seconds for mute/ban imposed by warn_action (0 = permanent).
    warn_action_duration: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Duration in seconds for the warn action (0 = permanent).",
    )
    # Days after which a warning automatically expires (0 = never expire).
    warn_expiry_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of days after which warnings expire automatically.  0 = never.",
    )

    # CAS (Combot Anti-Spam) protection toggle.
    cas_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Auto-ban users found in the CAS global spam database on join.",
    )

    # ------------------------------------------------------------------ #
    # General Group Settings                                              #
    # ------------------------------------------------------------------ #

    welcome_message_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Toggle for the automated welcome message sent to new members.",
    )
    welcome_message_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Custom welcome message text.  Supports Markdown.  NULL = use built-in default.",
    )
    goodbye_message_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Toggle for the automated goodbye message when a member leaves.",
    )
    goodbye_message_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Custom goodbye message text.  Supports Markdown.  NULL = use built-in default.",
    )
    rules_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Group rules displayed by the /rules command.  Supports Markdown.",
    )
    # If True, the bot deletes its own CAPTCHA challenge messages after
    # the member verifies (or is kicked) to keep the chat clean.
    clean_service_messages: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="If True, the bot removes its own challenge/notification messages after resolution.",
    )
    # Controls whether flood (rapid message) detection is active.
    flood_control_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Toggle for the rate-based flood detection subsystem.",
    )
    flood_messages_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        comment="Maximum messages per flood_interval_seconds before the flood action fires.",
    )
    flood_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        comment="Rolling window in seconds for flood detection.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="feature_settings")

    def __repr__(self) -> str:
        return (
            f"<ChatFeatureSettings chat_id={self.chat_id} "
            f"bayes={self.bayes_filter_enabled} "
            f"regex={self.regex_filter_enabled} "
            f"captcha={self.captcha_enabled}>"
        )


# ---------------------------------------------------------------------------
# Model: ChatMember
# ---------------------------------------------------------------------------

class ChatMember(Base):
    """
    Junction table tracking a User's membership and bot-assigned role in a Chat.

    Telegram admin status is checked live via the API; this table stores only
    the bot's own permission layer (e.g., who the group owner made a bot-mod).
    """

    __tablename__ = "chat_members"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_chat_member"),
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
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        nullable=False,
        default=UserRole.MEMBER,
        comment="Bot-assigned role for this user in this chat.",
    )
    # When this member's mute expires (None = not muted / permanent mute).
    muted_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC datetime when the mute expires.  NULL = not muted or permanent.",
    )
    # When this member's ban expires (None = not banned / permanent ban).
    banned_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC datetime when the ban expires.  NULL = not banned or permanent.",
    )
    # Total warnings accumulated in this chat.
    warn_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Running total of active warnings the user has in this chat.",
    )
    # Whitelist: if True, spam filters skip this user in this chat entirely.
    is_whitelisted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, all automated filters (spam, flood, CAPTCHA) are bypassed for this user.",
    )
    joined_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when this user joined the group.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="members")
    user: Mapped[User] = relationship("User", back_populates="memberships")

    def __repr__(self) -> str:
        return f"<ChatMember chat={self.chat_id} user={self.user_id} role={self.role}>"


# ---------------------------------------------------------------------------
# Model: WarnEntry
# ---------------------------------------------------------------------------

class WarnEntry(Base):
    """
    Individual warning issued to a user in a specific chat.

    Each row represents one warning event; the ``ChatMember.warn_count``
    column is a denormalised counter kept in sync for fast threshold checks
    without requiring a COUNT() query.
    """

    __tablename__ = "warn_entries"

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
    # The moderator/admin who issued the warning.
    issued_by_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram ID of the moderator who issued this warning.  NULL = bot-automated.",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable reason for the warning.",
    )
    # Which filter triggered the warning (for automated warnings).
    triggered_by: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Identifier of the subsystem that triggered this warning (e.g., 'bayes', 'regex', 'flood').",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    # UTC datetime when this warning expires and should be ignored.
    # NULL means the warning never expires (or per-chat expiry_days controls it).
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="UTC datetime when this warning expires.  NULL = never expires.",
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="warn_entries")
    user: Mapped[User] = relationship("User", back_populates="warn_entries")

    def __repr__(self) -> str:
        return f"<WarnEntry id={self.id} chat={self.chat_id} user={self.user_id}>"


# ---------------------------------------------------------------------------
# Model: CaptchaPending
# ---------------------------------------------------------------------------

class CaptchaPending(Base):
    """
    Tracks a new member who has been issued a CAPTCHA challenge and has not
    yet completed or failed it.

    The CAPTCHA middleware creates one row per (chat_id, user_id) pair when a
    new member joins.  The row is removed on success (member verified), on
    failure (member kicked/banned), or when the background expiry task fires.
    """

    __tablename__ = "captcha_pending"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_captcha_pending"),
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
    # Telegram message ID of the challenge message so it can be deleted later.
    challenge_message_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Message ID of the inline-button / challenge message sent to the chat.",
    )
    captcha_type: Mapped[CaptchaType] = mapped_column(
        Enum(CaptchaType),
        nullable=False,
        default=CaptchaType.BUTTON,
    )
    # Expected answer for math/text CAPTCHAs; None for button type.
    expected_answer: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="Correct answer for math or text CAPTCHAs.  NULL for button-type challenges.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="UTC datetime after which the pending entry is considered expired.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="captcha_pending")
    user: Mapped[User] = relationship("User", back_populates="captcha_pending")

    def __repr__(self) -> str:
        return f"<CaptchaPending chat={self.chat_id} user={self.user_id} expires={self.expires_at}>"


# ---------------------------------------------------------------------------
# Model: SpamPatternEntry
# ---------------------------------------------------------------------------

class SpamPatternEntry(Base):
    """
    A single traditional spam filter rule belonging to a specific chat.

    Stored separately from ``ChatFeatureSettings`` so that a chat can have an
    arbitrary number of patterns without imposing a column-per-pattern design.

    The regex/word-list filter iterates all active patterns for a chat and
    returns a match if any pattern matches the incoming message text.
    """

    __tablename__ = "spam_pattern_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The pattern itself — can be a plain word, phrase, or a regex expression.
    pattern: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Word, phrase, or regular expression to match against message text.",
    )
    # If True, the pattern field is treated as a compiled regex.
    is_regex: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If True, pattern is compiled as a case-insensitive regex. If False, treated as a plain substring.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Inactive patterns are retained in the DB but skipped during evaluation.",
    )
    # Moderator who added this pattern.
    added_by_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Telegram ID of the moderator who added this pattern.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="spam_patterns")

    def __repr__(self) -> str:
        return f"<SpamPatternEntry id={self.id} chat={self.chat_id} regex={self.is_regex}>"


# ---------------------------------------------------------------------------
# Model: BayesianToken
# ---------------------------------------------------------------------------

class BayesianToken(Base):
    """
    Stores per-chat word frequency counts for the naive Bayes spam classifier.

    Architectural decision — Bayesian Model Storage:
    Rather than persisting a serialised classifier object (which is fragile
    across library versions), we store raw token counts in the database.  The
    classifier is reconstructed in memory at runtime from these counts.  This
    makes the training data inspectable, portable, and easy to reset.

    Each row represents one token (word) in one chat's training corpus.
    ``ham_count`` and ``spam_count`` are incremented as administrators flag
    messages as ham or spam via the /train command.
    """

    __tablename__ = "bayesian_tokens"
    __table_args__ = (
        UniqueConstraint("chat_id", "token", name="uq_bayes_chat_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The normalised token (lowercased, stripped of punctuation at ingestion).
    token: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Normalised word token (lowercase, no leading/trailing punctuation).",
    )
    # Number of times this token appeared in messages labelled as ham (not spam).
    ham_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Occurrences of this token in ham-labelled training messages.",
    )
    # Number of times this token appeared in messages labelled as spam.
    spam_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Occurrences of this token in spam-labelled training messages.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Relationships
    chat: Mapped[Chat] = relationship("Chat", back_populates="bayes_tokens")

    def __repr__(self) -> str:
        return (
            f"<BayesianToken chat={self.chat_id} token={self.token!r} "
            f"ham={self.ham_count} spam={self.spam_count}>"
        )
