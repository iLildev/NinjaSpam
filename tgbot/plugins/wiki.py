"""
plugins/wiki.py — Wikipedia and Urban Dictionary quick-lookup commands.

Commands:
  /wiki <query>   — Fetch the first paragraph of the Wikipedia article
                    that best matches the query.
  /ud <query>     — Fetch the top Urban Dictionary definition for a term.

Both commands work in groups and PM.  Results are truncated to Telegram's
4096-char limit with a link to the full page appended.

No external API keys required:
  • Wikipedia: public REST API (https://en.wikipedia.org/api/rest_v1/)
  • Urban Dictionary: public JSON API (https://api.urbandictionary.com/v0/define)
"""

from __future__ import annotations

import html
import logging
from typing import Optional
from urllib.parse import quote

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

_WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
_UD_API_URL = "https://api.urbandictionary.com/v0/define?term={}"
_CHAR_LIMIT = 1000  # Truncate long summaries/definitions at this length


async def _fetch_json(url: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    """Fetch JSON from a URL using python-telegram-bot's bundled httpx session."""
    import json
    try:
        resp = await context.bot.request.get(url)  # type: ignore[attr-defined]
        return json.loads(resp.body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_json: %s — %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# /wiki
# ---------------------------------------------------------------------------

async def wiki(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Fetch a Wikipedia summary for the given query."""
    msg = update.effective_message
    if not msg:
        return

    if not context.args:
        await msg.reply_text("Usage: /wiki <search term>")
        return

    query = " ".join(context.args).strip()
    encoded = quote(query.replace(" ", "_"))
    url = _WIKI_SUMMARY_URL.format(encoded)

    data = await _fetch_json(url, context)

    if not data or data.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
        await msg.reply_text(
            f"❌ No Wikipedia article found for: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    title = data.get("title", query)
    extract = data.get("extract", "No summary available.")
    page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

    if len(extract) > _CHAR_LIMIT:
        extract = extract[:_CHAR_LIMIT].rsplit(" ", 1)[0] + "…"

    text = (
        f"📖 <b>{html.escape(title)}</b>\n\n"
        f"{html.escape(extract)}"
    )
    if page_url:
        text += f'\n\n<a href="{page_url}">Read more on Wikipedia</a>'

    await msg.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# /ud
# ---------------------------------------------------------------------------

async def ud(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Fetch the top Urban Dictionary definition for a term."""
    msg = update.effective_message
    if not msg:
        return

    if not context.args:
        await msg.reply_text("Usage: /ud <term>")
        return

    query = " ".join(context.args).strip()
    url = _UD_API_URL.format(quote(query))

    data = await _fetch_json(url, context)

    if not data:
        await msg.reply_text("⚠️ Could not reach Urban Dictionary right now.")
        return

    entries = data.get("list", [])
    if not entries:
        await msg.reply_text(
            f"❌ No Urban Dictionary definition found for: <b>{html.escape(query)}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    entry = entries[0]
    word = entry.get("word", query)
    definition = entry.get("definition", "").replace("[", "").replace("]", "")
    example = entry.get("example", "").replace("[", "").replace("]", "")
    permalink = entry.get("permalink", "")

    if len(definition) > _CHAR_LIMIT:
        definition = definition[:_CHAR_LIMIT].rsplit(" ", 1)[0] + "…"

    text = (
        f"📚 <b>{html.escape(word)}</b> (Urban Dictionary)\n\n"
        f"{html.escape(definition)}"
    )
    if example:
        if len(example) > 400:
            example = example[:400].rsplit(" ", 1)[0] + "…"
        text += f"\n\n<i>Example: {html.escape(example)}</i>"
    if permalink:
        text += f'\n\n<a href="{permalink}">Full entry</a>'

    await msg.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:  # noqa: D401
    application.add_handler(CommandHandler("wiki", wiki))
    application.add_handler(CommandHandler("ud",   ud))
    logger.info("wiki plugin registered.")
