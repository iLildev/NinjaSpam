"""
core/bot.py — PTB Application factory.

Provides ``build_application()`` which constructs, configures, and returns a
fully initialised ``telegram.ext.Application`` ready for handler registration
and polling.

Token is read from ``config.BOT_TOKEN``.  Request timeouts and retry behaviour
are configured here to keep the bot resilient under slow network conditions.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, ApplicationBuilder, Defaults
from telegram.constants import ParseMode

import config

logger = logging.getLogger(__name__)


async def build_application() -> Application:
    """
    Construct and return the PTB Application.

    Configured defaults:
    - ``parse_mode=ParseMode.HTML`` so handlers can return HTML strings without
      specifying parse_mode on every send call.
    - ``protect_content=False`` (default) — forwarding is allowed.

    Returns:
        A fully initialised ``Application`` instance, not yet running.
    """
    defaults = Defaults(
        parse_mode=ParseMode.HTML,
        disable_notification=False,
        allow_sending_without_reply=True,
        disable_web_page_preview=True,
    )

    application: Application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .defaults(defaults)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    logger.info(
        "PTB Application built for bot token …%s",
        config.BOT_TOKEN[-6:],
    )
    return application
