"""
plugins/rss.py — RSS feed subscription system for groups.

Commands (admin only in groups):
  /addrss <url>      — Subscribe the chat to an RSS feed.
  /removerss <url>   — Unsubscribe from an RSS feed.
  /listrss           — List all active feed subscriptions for this chat.
  /rss <url>         — Preview the latest entry from any RSS URL (no subscription).

Background job:
  Every 5 minutes the bot polls all subscribed feeds and sends new entries
  to the relevant chats (at most 5 new entries per cycle to avoid spam).

Dependencies:
  feedparser — pure-python RSS/Atom parser (add to requirements.txt).
  Install: pip install feedparser
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

MAX_MESSAGE_LENGTH: int = 4096

from core.helpers.chat_status import user_admin
from database.engine import get_session
from database.models_extra import RSSFeed

log = logging.getLogger(__name__)

_MAX_NEW_PER_CYCLE = 5
_POLL_INTERVAL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Feedparser helper (lazy import to keep startup fast if not installed)
# ---------------------------------------------------------------------------

def _parse_feed(url: str):  # type: ignore[return]
    """Parse an RSS/Atom feed URL. Returns a feedparser.FeedParserDict."""
    try:
        import feedparser  # type: ignore[import]
        return feedparser.parse(url)
    except ImportError:
        return None


def _is_valid_feed(url: str) -> bool:
    parsed = _parse_feed(url)
    if parsed is None:
        return False
    return parsed.bozo == 0


def _get_first_entry_link(url: str) -> str:
    parsed = _parse_feed(url)
    if parsed and parsed.entries:
        return parsed.entries[0].get("link", "")
    return ""


# ---------------------------------------------------------------------------
# /addrss
# ---------------------------------------------------------------------------

@user_admin
async def add_rss(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Subscribe the chat to an RSS feed."""
    msg = update.effective_message
    chat_id = update.effective_chat.id

    if not context.args:
        await msg.reply_text("Usage: /addrss <feed_url>")
        return

    url = context.args[0].strip()

    await msg.reply_text("⏳ Validating feed…")

    if not _is_valid_feed(url):
        await msg.reply_text("❌ That doesn't appear to be a valid RSS/Atom feed URL.")
        return

    async with get_session() as session:
        existing = await session.execute(
            select(RSSFeed).where(
                RSSFeed.chat_id == chat_id,
                RSSFeed.feed_link == url,
            )
        )
        if existing.scalar_one_or_none():
            await msg.reply_text("This feed is already subscribed in this chat.")
            return

        last_link = _get_first_entry_link(url)
        session.add(RSSFeed(
            chat_id=chat_id,
            feed_link=url,
            last_entry_link=last_link,
        ))

    await msg.reply_text(
        f"✅ Subscribed to:\n<code>{html.escape(url)}</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /removerss
# ---------------------------------------------------------------------------

@user_admin
async def remove_rss(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Unsubscribe the chat from an RSS feed."""
    msg = update.effective_message
    chat_id = update.effective_chat.id

    if not context.args:
        await msg.reply_text("Usage: /removerss <feed_url>")
        return

    url = context.args[0].strip()

    async with get_session() as session:
        result = await session.execute(
            select(RSSFeed).where(
                RSSFeed.chat_id == chat_id,
                RSSFeed.feed_link == url,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            await msg.reply_text("This feed is not subscribed in this chat.")
            return
        await session.delete(row)

    await msg.reply_text(
        f"✅ Unsubscribed from:\n<code>{html.escape(url)}</code>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /listrss
# ---------------------------------------------------------------------------

async def list_rss(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List all active RSS subscriptions for the current chat."""
    msg = update.effective_message
    chat_id = update.effective_chat.id

    async with get_session() as session:
        result = await session.execute(
            select(RSSFeed).where(RSSFeed.chat_id == chat_id).order_by(RSSFeed.id)
        )
        feeds = result.scalars().all()

    if not feeds:
        await msg.reply_text("This chat has no RSS subscriptions.")
        return

    lines = [f"<b>RSS Subscriptions ({len(feeds)}):</b>\n"]
    for feed in feeds:
        lines.append(f"• <code>{html.escape(feed.feed_link)}</code>")

    await msg.reply_html("\n".join(lines))


# ---------------------------------------------------------------------------
# /rss <url> — preview
# ---------------------------------------------------------------------------

async def preview_rss(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Preview the latest entry from an RSS URL without subscribing."""
    msg = update.effective_message

    if not context.args:
        await msg.reply_text("Usage: /rss <feed_url>")
        return

    url = context.args[0].strip()
    parsed = _parse_feed(url)

    if parsed is None:
        await msg.reply_text("❌ feedparser is not installed on this server.")
        return

    if parsed.bozo != 0:
        await msg.reply_text("❌ That doesn't appear to be a valid RSS/Atom feed URL.")
        return

    feed = parsed.feed
    feed_title = html.escape(feed.get("title", "Unknown"))
    feed_desc = html.escape(re.sub(r"<[^>]+>", "", feed.get("description", "No description")))
    feed_link = html.escape(feed.get("link", url))

    text = (
        f"<b>Feed Title:</b> {feed_title}\n"
        f"<b>Description:</b> <i>{feed_desc[:200]}</i>\n"
        f"<b>Link:</b> {feed_link}"
    )

    if parsed.entries:
        entry = parsed.entries[0]
        entry_title = html.escape(entry.get("title", "Unknown"))
        entry_link = html.escape(entry.get("link", ""))
        entry_desc = re.sub(r"<[^>]+>", "", entry.get("description", entry.get("summary", "")))
        entry_desc = html.escape(entry_desc[:300])

        text += (
            f"\n\n<b>Latest Entry:</b>\n"
            f"<b>Title:</b> {entry_title}\n"
            f"<b>Summary:</b> <i>{entry_desc}</i>\n"
            f"<b>Link:</b> {entry_link}"
        )

    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 100] + "\n…"

    await msg.reply_html(text, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Background polling job
# ---------------------------------------------------------------------------

async def _poll_all_feeds(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch all feeds and send new entries to subscribed chats."""
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        return

    async with get_session() as session:
        result = await session.execute(select(RSSFeed))
        feeds: List[RSSFeed] = list(result.scalars().all())

    for feed_row in feeds:
        try:
            parsed = feedparser.parse(feed_row.feed_link)
            if parsed.bozo != 0 or not parsed.entries:
                continue

            new_entries = []
            for entry in parsed.entries:
                link = entry.get("link", "")
                if link == feed_row.last_entry_link:
                    break
                new_entries.append(entry)

            if not new_entries:
                continue

            # Send at most _MAX_NEW_PER_CYCLE entries (newest first).
            to_send = list(reversed(new_entries[:_MAX_NEW_PER_CYCLE]))
            for entry in to_send:
                title = html.escape(entry.get("title", "New Entry"))
                link = html.escape(entry.get("link", ""))
                text = f"<b>{title}</b>\n\n{link}"
                if len(text) <= MAX_MESSAGE_LENGTH:
                    try:
                        await context.bot.send_message(
                            chat_id=feed_row.chat_id,
                            text=text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                        await asyncio.sleep(0.05)  # respect rate limits
                    except Exception as e:
                        log.debug("RSS send failed for chat %s: %s", feed_row.chat_id, e)

            # Update the last seen entry link.
            async with get_session() as session:
                row = await session.get(RSSFeed, feed_row.id)
                if row:
                    row.last_entry_link = new_entries[0].get("link", row.last_entry_link)
                    row.last_checked = datetime.now(tz=timezone.utc)

            if len(new_entries) > _MAX_NEW_PER_CYCLE:
                excess = len(new_entries) - _MAX_NEW_PER_CYCLE
                try:
                    await context.bot.send_message(
                        chat_id=feed_row.chat_id,
                        text=f"⚠️ <b>{excess}</b> older entries were skipped to prevent spam.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        except Exception as exc:
            log.debug("RSS poll error for feed %s: %s", feed_row.feed_link, exc)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

async def register(application: Application) -> None:
    """Register RSS commands and background polling job."""
    application.add_handler(
        CommandHandler("addrss", add_rss, filters=None)
    )
    application.add_handler(
        CommandHandler("removerss", remove_rss, filters=None)
    )
    application.add_handler(
        CommandHandler("listrss", list_rss)
    )
    application.add_handler(
        CommandHandler("rss", preview_rss)
    )

    # Schedule background poller every 5 minutes.
    if application.job_queue:
        application.job_queue.run_repeating(
            _poll_all_feeds,
            interval=_POLL_INTERVAL_SECONDS,
            first=30,
            name="rss_poller",
        )

    log.info("Plugin loaded: rss")
