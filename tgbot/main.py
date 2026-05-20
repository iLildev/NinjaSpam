"""
main.py — Bot entry point.

Startup sequence:
  1. Configure logging (config.py).
  2. Initialise the database and create all tables (database/engine.py).
  3. Build the PTB Application (core/bot.py).
  4. Register the global error handler (core/error_handler.py).
  5. Load all plugins from /plugins (core/plugin_loader.py).
  6. Start polling.

All startup stages are fail-fast except the plugin loader, which continues
on individual plugin errors to avoid a single bad plugin bringing down the bot.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import nest_asyncio
nest_asyncio.apply()

import config
from database.engine import init_db


async def main() -> None:
    """Async entry point for the bot process."""

    # ------------------------------------------------------------------ #
    # Step 1 — Configure logging                                          #
    # ------------------------------------------------------------------ #
    logger: logging.Logger = config.configure_logging()
    logger.info("Starting Telegram Group Management Bot …")
    logger.debug("DATABASE_URL=%s", config.DATABASE_URL)

    # ------------------------------------------------------------------ #
    # Step 2 — Initialise the database schema                             #
    # ------------------------------------------------------------------ #
    logger.info("Initialising database …")
    try:
        await init_db()
    except Exception as exc:
        logger.critical(
            "Failed to initialise the database: %s — check DATABASE_URL.",
            exc,
            exc_info=True,
        )
        sys.exit(1)
    logger.info("Database ready.")

    # ------------------------------------------------------------------ #
    # Step 3 — Build the PTB Application                                  #
    # ------------------------------------------------------------------ #
    from core.bot import build_application
    application = await build_application()

    # ------------------------------------------------------------------ #
    # Step 4 — Register global error handler                              #
    # ------------------------------------------------------------------ #
    from core.error_handler import register_error_handler
    register_error_handler(application)

    # ------------------------------------------------------------------ #
    # Step 5 — Load all plugins                                           #
    # ------------------------------------------------------------------ #
    from core.plugin_loader import load_all_plugins
    loaded: int = await load_all_plugins(application)
    if loaded == 0:
        logger.warning(
            "No plugins were loaded.  The bot will run but handle no commands."
        )

    # ------------------------------------------------------------------ #
    # Step 6 — Start polling using async context manager                  #
    # ------------------------------------------------------------------ #
    logger.info(
        "Bot is running with %d plugin(s).  Press Ctrl+C to stop.", loaded
    )
    async with application:
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=[
                "message",
                "edited_message",
                "callback_query",
                "chat_member",
                "my_chat_member",
            ],
        )
        logger.info("Polling started — bot is live.")
        # Keep running until interrupted
        await asyncio.Event().wait()
        await application.updater.stop()
        await application.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("tgbot").info(
            "Bot stopped by operator (KeyboardInterrupt)."
        )
