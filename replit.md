# Hozan Bot

A global Telegram group management and entertainment bot. Supports group protection, moderation, games, and a virtual economy — all in English.

## Run the Bot

```
cd Ninja && pip install -r requirements.txt -q && python main.py
```

## Requirements

- `BOT_TOKEN` — Bot token from @BotFather
- `DATABASE_URL` — PostgreSQL connection string (auto-provided by Replit)
- `OWNER_IDS` — Owner's Telegram user ID

## Project Structure

```
Ninja/
├── main.py               # Entry point
├── config.py             # Configuration from environment variables
├── plugins/              # All plugins (105 loaded)
├── database/             # SQLAlchemy models and DB setup
├── core/                 # Plugin loader, error handler, shared utilities
└── locales/              # Translation files
```

## Key Plugin Categories

- **Protection**: antispam, captcha, antiraid, cas_check, global_bans
- **Moderation**: bans, muting, warns, locks, admin, federation
- **Entertainment**: ninja_game, farm_game, castle_game, wallet (virtual coins)
- **Utilities**: notes, filters, rules, rss, scheduler

## Stack

- Python 3.11
- python-telegram-bot v20.7 (async)
- SQLAlchemy 2.0 (async) + asyncpg
- PostgreSQL

## User preferences

_To be filled as needed._
