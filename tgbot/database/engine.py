"""
database/engine.py — Async SQLAlchemy engine and session factory.

Provides:
- ``Base``                 : Declarative base shared by all ORM models.
- ``async_session_factory``: The raw async session factory (for advanced use).
- ``get_session``          : Async context-manager that yields a scoped session
                             and automatically commits or rolls back on exit.
- ``init_db``              : One-time coroutine that creates all tables on
                             first startup (idempotent).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Shared declarative base for every ORM model in the project.

    All model classes in ``database/models.py`` must inherit from this class
    so that ``init_db`` can discover and create their tables automatically.
    """


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------

def _build_engine() -> AsyncEngine:
    """
    Construct and return the async SQLAlchemy engine from config.DATABASE_URL.

    Engine-level tuning notes:
    - ``pool_pre_ping=True`` keeps the connection pool healthy by validating
      connections before use (important for long-running bots).
    - ``echo=False`` suppresses SQL statement logging in production; flip this
      to ``True`` for debugging query issues.
    - For PostgreSQL (asyncpg) the pool settings can be tuned via
      ``connect_args`` if needed.
    """
    engine_kwargs: dict = {
        "echo": False,
        "pool_pre_ping": True,
    }

    # SQLite requires a special flag to allow cross-thread usage by asyncio.
    if config.DATABASE_URL.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    return create_async_engine(config.DATABASE_URL, **engine_kwargs)


# Module-level singleton engine — created once at import time.
_engine: AsyncEngine = _build_engine()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

#: Async session factory bound to the module-level engine.
#: Use ``get_session()`` in application code rather than this directly.
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Prevents lazy-load errors after commit in async code
    autoflush=True,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a database session with automatic
    commit/rollback semantics.

    Usage::

        async with get_session() as session:
            result = await session.execute(select(User).where(User.id == uid))
            user = result.scalar_one_or_none()

    On normal exit the session is committed.  On any exception the transaction
    is rolled back and the exception is re-raised so the caller can handle it.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """
    Create all ORM-declared tables that do not yet exist in the database.

    This is idempotent: calling it more than once is safe.  It does NOT run
    schema migrations — use Alembic for that in production when you need to
    alter existing tables.

    Must be awaited once during bot startup, before any handler processes a
    message, to ensure the schema is present.
    """
    # Import both model modules so all Table objects are registered on
    # Base.metadata before create_all is called.  The imports themselves
    # are the side effect required — the names are intentionally unused.
    import database.models  # noqa: F401
    import database.models_extra  # noqa: F401
    import database.game_models  # noqa: F401
    import database.farm_models  # noqa: F401
    import database.payment_models  # noqa: F401

    async with _engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    logger.info("Database schema initialised (tables created if absent).")
