# DESIGN_PRINCIPLES.md

## What Fundamental Beliefs Guide This Project?

These principles are timeless. They apply regardless of which feature is being built, which plugin is being modified, or which developer is doing the work. When two approaches conflict, these principles break the tie.

---

## Principle 1 — Resilience Over Completeness

**The bot must keep running even when parts of it fail.**

The plugin loader deliberately continues loading remaining plugins when one raises an exception. A single broken plugin must never prevent the other 104 plugins from serving users. This principle extends to all subsystems: a missing DB row should produce a safe default, not a crash.

*Applied in:* `core/plugin_loader.py`, `core/bot.py`, every plugin's `register()` function.

---

## Principle 2 — Coexistence Over Replacement

**Protection layers complement each other; they do not replace each other.**

The Bayesian spam filter and the regex/word-list filter are independent toggles. Enabling one does not disable the other. A group admin may run both simultaneously for maximum coverage, or either individually. This same principle applies to CAPTCHA + CAS + Bayes: all three can be active at once and each adds a distinct layer.

*Applied in:* `ChatFeatureSettings.bayes_filter_enabled` + `regex_filter_enabled`, `captcha_enabled` + `cas_enabled`.

---

## Principle 3 — Per-Group Configuration Over Global Defaults

**Every feature that can vary per community must be configurable per group.**

Spam threshold, warn limit, warn action, CAPTCHA type, CAPTCHA timeout, nightmode schedule — all stored in `ChatFeatureSettings` with a per-group row. Global defaults in `config.py` exist as fallbacks only. No group should be forced to accept another group's settings.

*Applied in:* `database/models.py` (`ChatFeatureSettings`), `db/repositories/settings.py`.

---

## Principle 4 — One Logical Home Per Feature

**Each feature lives in exactly one place. There are no duplicate entry points.**

A command should be discoverable from one canonical location. If it is in the `settings_panel`, it should not also require a separate command that does the same thing. If an economy feature is in `economy/plugin.py`, there should not be a parallel implementation in `plugins/economy.py`.

*Current violations of this principle are documented in `LIVING_PROJECT_LOG.md` as technical debt.*

---

## Principle 5 — Shared Economy, Independent Games

**All games share a single coin wallet. No game has its own currency silo.**

`core/game_wallet.py` is the single source of truth for coin balances. Ninja game XP, Castle gold, Farm sales, Quiz rewards, and Economy commands all ultimately read and write the same `Wallet` table. This ensures players have one balance they care about across all activities.

*Exception:* `CastleResources.gold` is an internal Castle resource (wood/stone/food/gold) used for upgrades. It is explicitly NOT the same as `Wallet.coins`. This distinction must be preserved.

---

## Principle 6 — Database Over Memory for State

**Persistent state lives in the database. In-memory caches are for performance only.**

Enabled/disabled feature states must survive a bot restart. Using in-memory sets (e.g., `_CHATBOT_ENABLED`, `_ENABLED_CHATS` in `clean_blue.py`) for toggle state is an antipattern that violates this principle. Such state must be migrated to `ChatFeatureSettings` or a dedicated DB table.

*Current violations are listed in `LIVING_PROJECT_LOG.md`.*

---

## Principle 7 — English as the Global Interface Language

**All user-facing messages, game content, and error strings are in English.**

Hozan is a global bot with a worldwide user base. All game text, economy messages, error responses, and interactive menus must be written in English. This ensures consistency and accessibility across all communities.

*Applies to:* All plugins serving regular members and administrators alike.

---

## Principle 8 — Explicit Failure Over Silent Fallback

**The bot should report what went wrong rather than silently doing nothing.**

If a ban fails, say why. If a CAPTCHA can't be sent, log it. If a DB query fails, surface the error in the log channel rather than swallowing it. Silent failures create bugs that are impossible to diagnose.

*Applied in:* `core/error_handler.py`, all `try/except BadRequest` blocks in moderation plugins.

---

## Principle 9 — Repository Pattern for Data Access

**Plugins should not write raw SQL. They should use the `db/repositories/` layer.**

The `db/repositories/` package (bans, warns, settings, members) provides clean async interfaces that decouple plugin logic from database schema details. Plugins that bypass this layer and write direct `session.execute(select(...))` calls are harder to test and maintain.

*Target state:* All new plugins use repository functions. Existing plugins should be migrated as they are touched.

---

## Principle 10 — Documentation Is Part of the Product

**No significant feature is complete until it is documented.**

The `docs/` directory is not optional reading. Every architectural decision, every feature, every UX rule must be recorded. Future developers (human or AI) must be able to understand the entire system from `docs/` alone, without access to the original author's memory or conversation history.
