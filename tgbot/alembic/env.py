"""
alembic/env.py — Async-aware Alembic environment for the Ninja Bot.

Supports both online (live DB) and offline (SQL script) migration modes.
Reads DATABASE_URL directly from the environment so secrets never touch
alembic.ini or version control.

Usage (from tgbot/ directory):
  alembic upgrade head        # apply all pending migrations
  alembic revision --autogenerate -m "describe change"  # generate new migration
  alembic downgrade -1        # roll back one migration
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Make tgbot/ importable so we can access database.* packages.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.engine import Base  # noqa: E402 — must come after sys.path fix
import database.models          # noqa: F401 — registers Core models on Base.metadata
import database.models_extra    # noqa: F401 — registers Extended models on Base.metadata

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use all tables registered on the shared Base.
target_metadata = Base.metadata


def _get_url() -> str:
    """
    Resolve the database URL.

    Priority:
      1. ALEMBIC_DATABASE_URL environment variable (override for CI/scripts)
      2. DATABASE_URL environment variable (Replit-provisioned PostgreSQL)
      3. alembic.ini's sqlalchemy.url value

    Automatically converts postgresql:// → postgresql+asyncpg:// and strips
    SSL params that asyncpg does not accept (same logic as config.py).
    """
    raw = (
        os.environ.get("ALEMBIC_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url", "")
    )
    # asyncpg driver
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    # strip SSL params asyncpg rejects
    for param in ("?sslmode=require", "&sslmode=require", "sslmode=require"):
        raw = raw.replace(param, "")
    return raw


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout, no live connection needed.
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to the live DB and run migrations.
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine, get a sync connection, run migrations."""
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
