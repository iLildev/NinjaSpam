"""
plugins/truth_dare.py — Truth & Dare commands using the truthordarebot API.

Commands:
  /truth  — Get a random truth question.
  /dare   — Get a random dare challenge.
"""

from __future__ import annotations

import logging

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

API_BASE = "https://api.truthordarebot.xyz/v1"


async def truth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}/truth")
            resp.raise_for_status()
            question = resp.json().get("question", "No question found.")
    except Exception as e:
        logger.warning("truth API error: %s", e)
        question = "Couldn't fetch a question right now. Try again later."
    await update.effective_message.reply_text(question)


async def dare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}/dare")
            resp.raise_for_status()
            question = resp.json().get("question", "No dare found.")
    except Exception as e:
        logger.warning("dare API error: %s", e)
        question = "Couldn't fetch a dare right now. Try again later."
    await update.effective_message.reply_text(question)


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("truth", truth))
    application.add_handler(CommandHandler("dare", dare))
