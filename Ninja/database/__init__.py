"""
database/__init__.py — Public re-exports for the database package.

Import from here rather than from the sub-modules directly so that internal
refactors do not break import paths across the codebase.
"""

from database.engine import (
    Base,
    async_session_factory,
    get_session,
    init_db,
)

# Task 1 — base models
from database.models import (
    BayesianToken,
    CaptchaPending,
    CaptchaType,
    Chat,
    ChatFeatureSettings,
    ChatMember,
    SpamAction,
    SpamPatternEntry,
    User,
    UserRole,
    WarnAction,
    WarnEntry,
)

# Task 2 — extended models for plugins
from database.models_extra import (
    BlacklistEntry,
    ChatGbanToggle,
    CustomFilter,
    DisabledCommand,
    FilterButton,
    GlobalBannedUser,
    LockSettings,
    LogChannelSettings,
    Note,
    NoteButton,
    NoteType,
    RestrictionSettings,
    WarnFilter,
    WelcomeButton,
    WelcomeSettings,
)

__all__: list[str] = [
    # Engine / session utilities
    "Base",
    "async_session_factory",
    "get_session",
    "init_db",
    # Enumerations
    "CaptchaType",
    "NoteType",
    "SpamAction",
    "UserRole",
    "WarnAction",
    # Base models
    "BayesianToken",
    "CaptchaPending",
    "Chat",
    "ChatFeatureSettings",
    "ChatMember",
    "SpamPatternEntry",
    "User",
    "WarnEntry",
    # Extra models
    "BlacklistEntry",
    "ChatGbanToggle",
    "CustomFilter",
    "DisabledCommand",
    "FilterButton",
    "GlobalBannedUser",
    "LockSettings",
    "LogChannelSettings",
    "Note",
    "NoteButton",
    "RestrictionSettings",
    "WarnFilter",
    "WelcomeButton",
    "WelcomeSettings",
]
