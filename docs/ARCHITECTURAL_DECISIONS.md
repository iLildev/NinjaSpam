# ARCHITECTURAL_DECISIONS.md

## Why Was the System Built This Way?

This document records every significant architectural decision made during the design and development of Hozan Bot. Each decision explains the problem being solved, the options considered, what was chosen, and why. Future developers must understand these decisions before proposing changes.

---

## AD-001 — Plugin-Based Architecture

**Date:** Early design phase  
**Status:** Active

**Problem:** A Telegram group management bot has 100+ independent features. Monolithic code makes features difficult to isolate, maintain, or disable without affecting others.

**Options Considered:**
1. Single monolithic `handlers.py` file with all commands
2. Category-based modules (moderation.py, games.py, etc.)
3. File-per-feature plugin system with dynamic loading

**Decision:** Option 3 — file-per-feature plugin system.

**Rationale:**
- Each plugin is fully self-contained: its commands, handlers, and database imports are co-located.
- A broken plugin cannot crash the bot — the loader continues on `ImportError` or `AttributeError`.
- Adding a new feature requires only creating one file and implementing `async def register(application)`.
- Feature removal is as simple as deleting a file. No surgical editing of a monolith required.
- Enables future per-group plugin toggling if the `disable.py` system is extended.

**Consequences:**
- Load order is alphabetical — occasionally forces naming conventions (prefix with `00_` for early loaders).
- `fun_strings.py` (a data module with no `register()`) generates a WARNING at startup on every run. Accepted as known noise.

---

## AD-002 — Single Shared Coin Wallet (`core/game_wallet.py`)

**Date:** During game system design  
**Status:** Active

**Problem:** Multiple games (Ninja, Castle, Farm, Quiz) plus an Economy system all deal with coins. Without a shared API, each game would maintain its own coin table, creating fragmented balances.

**Options Considered:**
1. Per-game coin tables (ninja_coins, castle_coins, etc.)
2. One `Wallet` table accessed directly by each plugin
3. One `Wallet` table with a single access API module

**Decision:** Option 3 — `core/game_wallet.py` as the sole coin mutation API.

**Rationale:**
- Players have one balance visible in `/wallet`, regardless of how they earned coins.
- All coin mutations go through `add_coins()` and `deduct_coins()`, making auditing trivial.
- `deduct_coins()` returns `None` on insufficient funds — a consistent "not enough coins" check across all games.
- Centralised total_earned tracking.

**Consequences:**
- Castle game has an additional internal resource (`CastleResources.gold`) for upgrades. This is NOT coins and must never be confused with `Wallet.coins`. The distinction is documented in `PROJECT_GLOSSARY.md`.
- Developers must import from `core.game_wallet` not directly from `database.game_models`.

---

## AD-003 — Python-Telegram-Bot v20 (Async)

**Date:** Framework selection  
**Status:** Active

**Problem:** Telegram bots require handling many concurrent updates from multiple groups simultaneously. A synchronous bot would block on DB queries.

**Options Considered:**
1. python-telegram-bot v13 (sync)
2. python-telegram-bot v20 (async/await)
3. Aiogram (alternative async framework)
4. Telethon (MTProto, not Bot API)

**Decision:** Option 2 — python-telegram-bot v20.7.

**Rationale:**
- Native `async/await` support allows concurrent handling of updates without threads.
- Built-in JobQueue (APScheduler) eliminates need for a separate task scheduler.
- Familiar API for developers coming from v13.
- Strong community, extensive documentation.
- `ConversationHandler` built-in for multi-step interactions (registration, settings setup).

**Consequences:**
- All plugin functions must be `async def`. No synchronous DB calls.
- SQLAlchemy must use the async engine (`asyncpg` driver). Standard `psycopg2` won't work.

---

## AD-004 — SQLAlchemy 2.0 Async ORM

**Date:** Database layer selection  
**Status:** Active

**Problem:** The bot needs persistent storage for 105 features across potentially thousands of groups and users.

**Options Considered:**
1. SQLite (simple, no server needed)
2. PostgreSQL with synchronous SQLAlchemy
3. PostgreSQL with async SQLAlchemy 2.0 + asyncpg
4. MongoDB
5. Redis (in-memory only)

**Decision:** Option 3 — PostgreSQL + SQLAlchemy 2.0 async + asyncpg.

**Rationale:**
- SQLite has limitations for concurrent async writes.
- Async SQLAlchemy integrates naturally with PTB v20's async architecture.
- PostgreSQL is fully hosted and provisioned by Replit with zero configuration.
- SQLAlchemy ORM provides type-safe model definitions and Alembic migration support.

**Consequences:**
- All DB operations must be `await`ed inside `async with get_session() as session:` blocks.
- FK integrity must be manually ensured before inserts via `ensure_user_and_chat()` (PostgreSQL enforces FK constraints, unlike SQLite).

---

## AD-005 — Dual Database Access Pattern (Technical Debt)

**Date:** Identified during Phase 1 audit  
**Status:** Active (Tech Debt — target resolution: migration to repository pattern)

**Problem:** The codebase has two different ways of accessing the database:
1. Direct ORM in plugins: `session.execute(select(WarnEntry).where(...))`
2. Repository pattern: `await warns_repo.add(chat_id, user_id, reason)`

Both are in active use. The repository pattern is strictly better but only covers bans, warns, settings, and members so far.

**Options Considered:**
1. Accept both patterns as valid and document them equally
2. Adopt repository pattern as the standard and migrate old plugins gradually
3. Rewrite all plugins to use direct ORM for consistency

**Decision:** Option 2 — repository pattern is the canonical standard. Direct ORM in existing plugins is tech debt to be migrated gradually.

**Rationale:**
- Repository functions decouple plugin logic from schema details.
- Schema changes only require updating one repository file, not every plugin that touches the table.
- Repository functions are more testable.
- A full rewrite would be disruptive and risky.

**Consequences:**
- New plugins MUST use repository functions where available.
- Existing plugins using direct ORM are flagged but functional — do not break them while migrating.

---

## AD-006 — Per-Group Configuration in `ChatFeatureSettings`

**Date:** Early architecture decision  
**Status:** Active

**Problem:** Every protection feature needs to be independently toggleable per group. Without centralised configuration, each feature would need its own table.

**Decision:** All per-group boolean toggles are columns in `ChatFeatureSettings`. One row per chat.

**Rationale:**
- All group settings visible in one SELECT query.
- Easy to add new settings via Alembic migration (add column with default).
- `db/repositories/settings.py::get_or_create()` is the single entry point.

**Consequences:**
- `ChatFeatureSettings` is a wide table with many columns. This is expected and acceptable.
- Adding a new toggleable feature requires an Alembic migration. Do not use in-memory sets as a shortcut (see AD-007).

---

## AD-007 — In-Memory Feature Toggle Antipattern (Known Violation)

**Date:** Identified during Phase 1 audit  
**Status:** Active (Tech Debt — must be resolved)

**Problem:** Three plugins use in-memory Python sets/dicts for feature state instead of the database:
- `chatbot.py`: `_CHATBOT_ENABLED: set[int]` — chatbot enabled/disabled per chat
- `clean_blue.py`: `_ENABLED_CHATS: set[int]` — clean blue enabled/disabled per chat
- `global_ignore.py`: in-memory ignore list

**Impact:** These settings are silently lost every time the bot restarts.

**Decision to Fix:** Migrate all three to `ChatFeatureSettings` columns or dedicated tables via Alembic. This is tracked as priority-2 technical debt in `LIVING_PROJECT_LOG.md`.

**Why Not Fixed Yet:** Deferred to avoid scope creep during initial documentation phase. Fix is unambiguous — each needs a DB column and a migration.

---

## AD-008 — `elevated_users.json` for Bot Hierarchy (Known Violation)

**Date:** Identified during Phase 1 audit  
**Status:** Active (Tech Debt — must be resolved)

**Problem:** The bot's permission hierarchy (Dragon/Demon/Tiger/Wolf) is stored in `elevated_users.json` via `disasters.py`. Everything else uses PostgreSQL.

**Impact:**
- JSON file is not ACID-compliant. Concurrent writes risk corruption.
- File is not backed up with the database.
- Inconsistency: one "database" is SQL, one is a file.
- If the bot is deployed to multiple instances, they won't share the same hierarchy.

**Decision to Fix:** Migrate to a `ElevatedUser` table in PostgreSQL. This is tracked as priority-1 technical debt in `LIVING_PROJECT_LOG.md`.

**Why Not Fixed Yet:** The JSON approach is functional for single-instance deployments and was inherited from an earlier design. Full migration requires careful handling of existing data.

---

## AD-009 — Economy as a Sub-Package (`economy/`)

**Date:** Economy system design  
**Status:** Active

**Problem:** The economy system (salary, stealing, investment, heist, loans, jail) has 24+ commands, complex internal state, and its own DB models. It is too large for a single plugin file.

**Decision:** `economy/` is a Python sub-package with its own `models.py`, `helpers.py`, `plugin.py`, `loans.py`, and `heist.py`. The entry point `plugins/economy.py` is a thin shim that imports `economy.plugin.register`.

**Rationale:**
- Sub-package allows private helpers and models without polluting the top-level namespace.
- Same pattern as `quiz/` package (questions.py + plugin.py).
- Clean separation of concerns within the economy system.

**Consequences:**
- `plugins/economy.py` is a pure shim — it contains no logic. This looks confusing but is intentional.
- Quiz follows the same pattern via `plugins/quiz.py` → `quiz/plugin.py`.

---

## AD-010 — Bayesian Spam Classifier Per Group, Not Global

**Date:** Spam filter design  
**Status:** Active

**Problem:** Spam patterns differ significantly between communities and languages. A global classifier trained on mixed data would perform poorly for any individual community. A spam message for one community may be perfectly normal in another.

**Decision:** Each group trains its own per-chat Bayes classifier using `BayesianToken` rows scoped to `chat_id`.

**Rationale:**
- Community-specific spam patterns are learned automatically.
- A spam campaign in Group A doesn't affect Group B's classification.
- Admins control what their classifier learns via `/train`.

**Consequences:**
- New groups start with no trained data. Bayes abstains (returns `None`) until `BAYES_MIN_CORPUS_SIZE` tokens are collected. The regex filter must handle spam during this bootstrap period.
- Memory TTLCache per chat (256 chats, 5-minute TTL) reduces per-message DB round-trips.

---

## AD-011 — HTML Parse Mode as Global Default

**Date:** Message format standardisation  
**Status:** Active

**Problem:** python-telegram-bot supports HTML, Markdown, and MarkdownV2. Using different modes across plugins creates inconsistency and confusion.

**Decision:** HTML is the global default parse mode configured in the PTB Application builder.

**Rationale:**
- HTML is easier to reason about than MarkdownV2's strict escaping requirements.
- `<b>`, `<i>`, `<code>`, `<a href>` cover all formatting needs.
- No risk of accidental character escaping breaking messages.
- Some special characters in user content (brackets, underscores) break Markdown but not HTML.

**Consequences:**
- All plugin messages must use HTML tags, not Markdown syntax.
- User-supplied content placed in messages must be HTML-escaped using `html.escape()`.

---

## AD-012 — Three-Phase Spam Middleware Pipeline

**Date:** Spam architecture design  
**Status:** Active

**Problem:** Multiple independent spam protection mechanisms need to run sequentially on every message without coupling their implementations.

**Decision:** PTB handler groups are used to enforce order:
- Group 1: Bayesian filter
- Group 2: Regex filter
- Groups 3+: Individual attribute filters

`ApplicationHandlerStop` is raised to halt the pipeline when a message is actioned.

**Rationale:**
- PTB handler groups are built-in — no custom middleware framework needed.
- Each filter registers itself independently; no filter knows about others.
- Adding a new filter is simply adding a new `MessageHandler` in a new group.

**Consequences:**
- Developers must choose the correct group number when adding new message handlers. Too low = runs before spam filters. Too high = runs after all spam is already handled.

---

## AD-013 — Alembic for All Schema Changes

**Date:** Database management decision  
**Status:** Active

**Problem:** With 100+ features using 25+ tables across 5+ model files, ad-hoc schema changes are impossible to track and reproduce.

**Decision:** All schema changes must go through Alembic migrations. `alembic upgrade head` is called automatically in `main.py → init_db()`.

**Rationale:**
- Complete, reproducible migration history.
- Auto-generates migrations from model diffs via `alembic revision --autogenerate`.
- Replit production database can be upgraded to match development schema at any time.

**Consequences:**
- Every new column or table requires a migration file checked in alongside the model change.
- Do not use `Base.metadata.create_all()` for schema management in production.
