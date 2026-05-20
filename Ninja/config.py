"""
config.py — Centralised runtime configuration for the bot.

All sensitive values are sourced exclusively from the '.env' file via
python-dotenv so that the repository itself remains credential-free.
Import this module anywhere in the codebase; never read os.environ directly.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import FrozenSet

from dotenv import load_dotenv

# Locate the '.env' file relative to this config module so the bot can be
# launched from any working directory.
_ENV_PATH: Path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require(key: str) -> str:
    """Return the value of a required environment variable or abort startup."""
    value: str | None = os.getenv(key)
    if not value:
        print(
            f"[config] FATAL: Required environment variable '{key}' is not set. "
            f"Copy '.env.example' to '.env' and fill in your credentials.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _optional(key: str, default: str = "") -> str:
    """Return the value of an optional environment variable or its default."""
    return os.getenv(key, default)


def _parse_int_list(raw: str) -> FrozenSet[int]:
    """Parse a comma-separated string of integers into a frozen set."""
    result: set[int] = set()
    for part in raw.split(","):
        stripped = part.strip()
        if stripped.isdigit():
            result.add(int(stripped))
    return frozenset(result)


# ---------------------------------------------------------------------------
# Core Telegram Settings
# ---------------------------------------------------------------------------

#: The bot token issued by @BotFather.  Required at startup.
BOT_TOKEN: str = _require("BOT_TOKEN")

#: Frozen set of owner Telegram user IDs.  Commands restricted to owners
#: check membership against this set.
OWNER_IDS: FrozenSet[int] = _parse_int_list(_require("OWNER_IDS"))


# ---------------------------------------------------------------------------
# Database Settings
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    """
    Construct the async SQLAlchemy database URL.

    Priority:
    1. DATABASE_URL env var — if present, rewrite postgresql:// → postgresql+asyncpg://
       so the Replit-provided URL works with SQLAlchemy's async engine.
    2. Individual PG* env vars (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD).
    3. SQLite fallback for local development.
    """
    raw: str | None = os.getenv("DATABASE_URL")
    if raw:
        if raw.startswith("postgresql://") or raw.startswith("postgres://"):
            raw = "postgresql+asyncpg://" + raw.split("://", 1)[1]
            # Strip any ?ssl=... query params that asyncpg doesn't accept this way.
            if "?" in raw:
                raw = raw.split("?")[0]
        return raw

    pg_host = os.getenv("PGHOST")
    pg_port = os.getenv("PGPORT", "5432")
    pg_db = os.getenv("PGDATABASE")
    pg_user = os.getenv("PGUSER")
    pg_pass = os.getenv("PGPASSWORD", "")
    if pg_host and pg_db and pg_user:
        return f"postgresql+asyncpg://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    return "sqlite+aiosqlite:///./tgbot.db"


#: Full async SQLAlchemy connection string.
DATABASE_URL: str = _optional("DATABASE_URL", default="") or _build_database_url()
# Normalise any postgresql:// URL that slipped through via .env.
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL.split("://", 1)[1]
    if "?" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.split("?")[0]


# ---------------------------------------------------------------------------
# Logging Settings
# ---------------------------------------------------------------------------

#: Python logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
LOG_LEVEL: str = _optional("LOG_LEVEL", default="INFO").upper()

#: Optional file path for persistent log output.  Empty string → stdout only.
LOG_FILE: str = _optional("LOG_FILE", default="")


# ---------------------------------------------------------------------------
# Bayesian Spam Filter Settings
# ---------------------------------------------------------------------------

#: Minimum number of training samples required before the Bayes classifier
#: starts emitting predictions.  Below this the filter abstains.
BAYES_MIN_CORPUS_SIZE: int = int(_optional("BAYES_MIN_CORPUS_SIZE", default="200"))

#: Probability threshold above which a message is classified as spam.
#: Must be in the range (0.0, 1.0).
BAYES_SPAM_THRESHOLD: float = float(_optional("BAYES_SPAM_THRESHOLD", default="0.90"))


# ---------------------------------------------------------------------------
# CAPTCHA Settings
# ---------------------------------------------------------------------------

#: Seconds a newly joined member has to complete the CAPTCHA before being
#: removed from the group.
CAPTCHA_TIMEOUT_SECONDS: int = int(_optional("CAPTCHA_TIMEOUT_SECONDS", default="120"))


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------

#: Maximum messages per minute a single user may trigger on rate-limited
#: command handlers before being silently ignored.
RATE_LIMIT_MESSAGES_PER_MINUTE: int = int(
    _optional("RATE_LIMIT_MESSAGES_PER_MINUTE", default="30")
)


# ---------------------------------------------------------------------------
# Derived / computed constants
# ---------------------------------------------------------------------------

#: Absolute path to the project root (the directory containing this file).
PROJECT_ROOT: Path = Path(__file__).parent

#: Directory where plugins are discovered and loaded from.
PLUGINS_DIR: Path = PROJECT_ROOT / "plugins"

#: Directory for log files (created on demand by the logging initialiser).
LOGS_DIR: Path = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Extended access control (used by global_bans and welcome plugins)
# ---------------------------------------------------------------------------

#: Telegram user_id of the primary bot owner (first entry in OWNER_IDS).
#: Plugins that need a single owner reference use this.
OWNER_ID: int = next(iter(OWNER_IDS), 0)

#: Sudo users — can perform owner-restricted actions (e.g. /gban).
SUDO_USERS: FrozenSet[int] = _parse_int_list(_optional("SUDO_USERS", ""))

#: Support users — read-only privileged access (e.g. view gban list).
SUPPORT_USERS: FrozenSet[int] = _parse_int_list(_optional("SUPPORT_USERS", ""))

#: When True, globally banned users are automatically kicked from any group
#: they send a message in, not only on join events.
STRICT_GBAN: bool = _optional("STRICT_GBAN", "False").lower() in ("true", "1", "yes")


class _Settings:
    """
    Attribute-style access shim so plugins can do::

        from config import settings as cfg
        if user_id == cfg.OWNER_ID:
            ...

    This avoids sprinkling ``config.OWNER_ID`` everywhere and makes it easy
    to swap the source of settings later (e.g. environment → database).
    """

    BOT_TOKEN: str = BOT_TOKEN
    OWNER_ID: int = OWNER_ID
    OWNER_IDS: FrozenSet[int] = OWNER_IDS
    SUDO_USERS: FrozenSet[int] = SUDO_USERS
    SUPPORT_USERS: FrozenSet[int] = SUPPORT_USERS
    STRICT_GBAN: bool = STRICT_GBAN
    DATABASE_URL: str = DATABASE_URL
    BAYES_MIN_CORPUS_SIZE: int = BAYES_MIN_CORPUS_SIZE
    BAYES_SPAM_THRESHOLD: float = BAYES_SPAM_THRESHOLD
    CAPTCHA_TIMEOUT_SECONDS: int = CAPTCHA_TIMEOUT_SECONDS


#: Singleton settings object for attribute-style access across the codebase.
settings: _Settings = _Settings()


def configure_logging() -> logging.Logger:
    """
    Configure the root logger and return the application-level logger.

    Sets up:
    - A stream handler (always active) for console output.
    - A rotating file handler (when LOG_FILE is non-empty).

    Returns the 'tgbot' logger that all modules should use via
    ``logging.getLogger(__name__)``.
    """
    from logging.handlers import RotatingFileHandler

    numeric_level: int = getattr(logging, LOG_LEVEL, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Stream handler — always present
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Rotating file handler — only when a log file path is configured
    if LOG_FILE:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Silence overly verbose third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    return logging.getLogger("tgbot")
