# ARCHITECTURE.md

## How Does This Project Work?

---

## High-Level Overview

```
Telegram API
     │
     ▼
python-telegram-bot v20.7 (async polling)
     │
     ▼
┌────────────────────────────────────────────────────────┐
│                    main.py (entry point)                │
│  1. configure_logging()                                 │
│  2. init_db()          ──► PostgreSQL (asyncpg)         │
│  3. build_application()                                 │
│  4. register_error_handler()                            │
│  5. load_all_plugins()  ──► plugins/*.py (105 plugins)  │
│  6. start_polling()                                     │
└────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
Ninja/
├── main.py                    # Entry point — startup sequence
├── config.py                  # All environment variables, centralised config
├── alembic.ini                # Alembic migration config
├── alembic/                   # Database migration scripts
│   └── env.py
├── requirements.txt           # Python dependencies
│
├── core/                      # Shared infrastructure (not plugins)
│   ├── bot.py                 # PTB Application factory
│   ├── plugin_loader.py       # Dynamic plugin discovery & registration
│   ├── error_handler.py       # Global PTB error handler
│   ├── game_wallet.py         # Unified coin wallet API for all games
│   ├── rate_limiter.py        # Telegram API rate-limit throttle
│   ├── spam_bayes.py          # Naive Bayes spam classifier engine
│   ├── log_channel.py         # @loggable decorator for audit logging
│   ├── i18n.py                # Internationalisation (t() function)
│   └── helpers/
│       ├── chat_status.py     # Decorators: @user_admin, @bot_admin, etc.
│       ├── extraction.py      # extract_user_and_text() from messages
│       ├── fuzzy.py           # Levenshtein distance for command suggestions
│       ├── string_handling.py # button_markdown_parser(), template escaping
│       └── city_timezones.py  # City → timezone mapping for /timezone
│
├── database/                  # SQLAlchemy ORM models
│   ├── engine.py              # async engine, Base, get_session() context manager
│   ├── models.py              # Core models: User, Chat, ChatFeatureSettings,
│   │                          #   WarnEntry, CaptchaPending, SpamPatternEntry,
│   │                          #   BayesianToken, ChatMember
│   ├── models_extra.py        # Extended models: WelcomeSettings, CustomFilter,
│   │                          #   Note, Federation, FedBan, BanRecord, LockSettings,
│   │                          #   AntiLinkSettings, WarnFilter, CleanServiceSettings, etc.
│   ├── ninja_models.py        # NinjaProfile, KidnapRecord
│   ├── game_models.py         # Wallet, Castle, CastleResources, Barracks,
│   │                          #   ImmunityCard, TreasureHunt, AllianceRequest, RulerTitle
│   ├── farm_models.py         # Farm, FarmPlot, FarmInventory
│   ├── payment_models.py      # UserProfile, PaymentAccount (fictional payment system)
│   └── connect_models.py      # UserConnection (PM-to-group bridge)
│
├── db/                        # Repository pattern (clean data access layer)
│   └── repositories/
│       ├── base.py            # ensure_user_and_chat() helper
│       ├── bans.py            # record_ban(), record_unban(), get_ban()
│       ├── warns.py           # add(), count(), list_entries(), clear_all()
│       ├── settings.py        # get(), get_or_create(), update()
│       └── members.py         # get_or_create(), get(), set_warn_count()
│
├── economy/                   # Economy sub-package (24 commands)
│   ├── plugin.py              # Command handlers: /richlist, /balance, /steal, etc.
│   ├── models.py              # BankAccount, EconomyStats, JailRecord, LoanRecord,
│   │                          #   HeistSession, HeistParticipant
│   ├── helpers.py             # fmt_coins(), jail helpers, loan helpers
│   ├── loans.py               # /loan, /repay, /myloan, /bail, /bailout
│   └── heist.py               # /rob, /joinrob (group heist mechanic)
│
├── quiz/                      # Quiz sub-package
│   ├── plugin.py              # Quiz command handlers + answer listener
│   └── questions.py           # Question bank (general, anime, cars)
│
├── locales/                   # Internationalisation strings
│   └── strings.py             # All translatable strings keyed by ID
│
├── plugins/                   # 106 plugin files (105 load successfully)
│   ├── [protection]           # captcha, bayes_filter, antiflood, antilinks...
│   ├── [moderation]           # bans, muting, warns, admin, locks, purge...
│   ├── [games]                # ninja_game, castle_game, farm_game, quiz...
│   ├── [economy]              # economy (shim), wallet
│   ├── [management]           # federation, global_bans, settings_panel, backup...
│   ├── [welcome/info]         # welcome, rules, notes, filters, onboarding
│   ├── [utility]              # ping_cmd, math_cmd, currency, translator...
│   └── [fun]                  # fun, fonts, tagall, sticker_tools, couples...
│
└── elevated_users.json        # Bot owner hierarchy (dragons/demons/tigers/wolves)
                               # ⚠️ Should be migrated to database — see LIVING_PROJECT_LOG.md
```

---

## Core Systems in Detail

### 1. Plugin Loader (`core/plugin_loader.py`)

- Scans `plugins/*.py` alphabetically, skipping `__init__.py` and files starting with `_`.
- Imports each via `importlib.util.spec_from_file_location`.
- Calls `await module.register(application)` if the function exists and is a coroutine.
- Continues on failure — a broken plugin is logged as WARNING, not an error.
- Load order is alphabetical. Plugins that depend on another plugin being loaded first must be prefixed (e.g., `00_base.py`).

**Plugin Contract:**
```python
async def register(application: Application) -> None:
    application.add_handler(CommandHandler("mycommand", my_handler))
```

---

### 2. Database Layer

**Two parallel access patterns exist (this is technical debt):**

| Pattern | Location | Used By |
|---------|----------|---------|
| Direct ORM (`session.execute(select(...))`) | In plugin files directly | Most older plugins |
| Repository functions | `db/repositories/` | bans.py, warns.py, newer plugins |

**Session management:** `database/engine.py` exposes `get_session()` as an async context manager:
```python
async with get_session() as session:
    result = await session.execute(...)
```

**Model files:**
- `models.py` — core moderation/protection tables
- `models_extra.py` — extended per-feature tables
- `ninja_models.py`, `game_models.py`, `farm_models.py` — game tables
- `payment_models.py` — fictional payment UI tables
- `economy/models.py` — economy system tables
- `connect_models.py` — PM connection table

---

### 3. Spam Protection Pipeline

Messages pass through three independent, coexisting layers:

```
Incoming Message
      │
      ▼
[Layer 1] Bayesian AI Filter (bayes_filter.py)
  - Per-chat trained naive Bayes classifier
  - Uses BayesianToken table for token frequencies
  - Abstains if corpus < BAYES_MIN_CORPUS_SIZE (default 200)
  - Action: delete / delete+warn / delete+mute / delete+ban
      │
      ▼
[Layer 2] Regex/Word-List Filter (antispam_panel.py + SpamPatternEntry table)
  - Per-chat configured word/regex patterns
  - Independent toggle from Layer 1
      │
      ▼
[Layer 3] Message Attribute Filters (independent plugins)
  - antiflood.py          — rate limit per user
  - antilinks.py          — URL/invite link blocking
  - anti_duplicate.py     — duplicate message detection
  - anti_forward.py       — forwarded message blocking
  - anti_nsfw.py          — NSFW content detection
  - lang_filter.py        — language-based filtering
  - spacing_check.py      — abnormal spacing evasion
  - homoglyph.py          — Cyrillic/Latin mixed script
  - reaction_spam.py      — emoji reaction flooding
  - phishing.py           — scam URL detection
  - name_ban.py           — banned username patterns
```

---

### 4. Game Economy Architecture

```
                    ┌──────────────┐
                    │  Wallet DB   │  (game_wallets table)
                    │  .coins      │  ← Single source of truth
                    └──────┬───────┘
                           │ read/write via core/game_wallet.py
          ┌────────────────┼────────────────────────────┐
          │                │                            │
    wallet.py         farm_game.py                ninja_game.py
    (/wallet,         (sell harvest)              (kill reward XP)
    /daily)                │
                      castle_game.py              quiz/plugin.py
                      (resource shop,             (correct answer
                      army purchase)               reward)
                           │
                      economy/plugin.py
                      (salary, steal, invest,
                       heist, loans, jail)
```

**Important distinction:** `CastleResources.gold` is an in-game Castle resource (alongside wood, stone, food). It is NOT the same as `Wallet.coins`. Castle gold is exchangeable to Wallet coins via `/exchange_gold`.

---

### 5. Rate Limiter (`core/rate_limiter.py`)

Handles Telegram API limits during bulk operations (federation bans, global bans):
- Global: 20 messages/second (safely below Telegram's 30/s cap)
- Per-chat: 1 message/second
- Auto-retry on `RetryAfter` and `TimedOut` errors
- `mass_operation()` for batch processing with pacing

---

### 6. Bayesian Classifier (`core/spam_bayes.py`)

- Token storage: raw `(ham_count, spam_count)` per token per chat in `bayesian_tokens` table
- In-memory TTLCache (5 min, 256 chats) reduces DB round-trips
- Laplace add-1 smoothing prevents zero-probability tokens
- Log-sum-exp arithmetic prevents float underflow
- Training via `/train` command in `bayes_filter.py`
- Abstains (returns `None`) when corpus < `BAYES_MIN_CORPUS_SIZE`

---

### 7. Scheduler / Job Queue

PTB's built-in `JobQueue` (APScheduler) is used for:
- CAPTCHA expiry jobs (per user per chat)
- Nightmode on/off schedule (nightmode.py)
- RSS feed polling (rss.py)
- Heist execution after 60-second recruitment window (economy/heist.py)
- Quiz timeout jobs (quiz/plugin.py)

---

## Configuration (`config.py`)

All configuration is sourced from environment variables. The `.env` file is optional — in Replit, secrets are injected directly into the environment.

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `OWNER_IDS` | ✅ | Comma-separated Telegram user IDs with owner access |
| `DATABASE_URL` | Auto | PostgreSQL connection string (Replit provides this) |
| `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` | Auto | Replit PostgreSQL credentials (alternative to DATABASE_URL) |
| `LOG_LEVEL` | No | DEBUG/INFO/WARNING (default: INFO) |
| `BAYES_MIN_CORPUS_SIZE` | No | Minimum training samples before Bayes activates (default: 200) |
| `BAYES_SPAM_THRESHOLD` | No | Spam probability threshold 0–1 (default: 0.90) |
| `CAPTCHA_TIMEOUT_SECONDS` | No | Default CAPTCHA window (default: 120) |
| `SUDO_USERS` | No | Comma-separated IDs with sudo access |
| `SUPPORT_USERS` | No | Comma-separated IDs with support access |
| `STRICT_GBAN` | No | Enforce gbans on every message, not just joins (default: False) |

---

## Data Flow: Moderation Action Example (Warn)

```
Admin sends /warn @user reason
      │
      ▼
warns.py::warn()
  ├─ extract_user_and_text() → user_id, reason
  ├─ is_user_ban_protected() → skip admins
  ├─ warns_repo.add(chat_id, user_id, reason)
  │     └─ db/repositories/warns.py
  │           └─ WarnEntry INSERT into PostgreSQL
  ├─ settings_repo.get(chat_id) → warn_action, warn_limit
  ├─ if count >= limit → execute WarnAction (ban/kick/mute)
  ├─ reply with progress bar [▓▓░░░░░░] 2/3
  └─ @loggable → send audit message to log_channel
```
