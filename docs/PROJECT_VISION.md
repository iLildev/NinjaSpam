# PROJECT_VISION.md

## Why Does This Project Exist?

Ninja Bot exists to give Arabic-speaking Telegram group administrators a single, self-contained moderation and entertainment platform that eliminates the need to run multiple specialized bots in parallel. Most Arabic communities rely on combinations of Rose Bot (management), Shieldy (CAPTCHA), CAS (spam), and separate game bots. Ninja consolidates all of these roles into one bot that the community controls end-to-end.

---

## Project Purpose

Ninja Bot is a Telegram group management bot built for Arabic-speaking communities. It provides:

1. **Layered spam and intrusion protection** — CAS integration, Bayesian AI spam classification, regex/word-list filters, CAPTCHA verification, flood control, homoglyph detection, phishing detection, duplicate message detection, and more — all independently configurable per group.
2. **Full moderation toolchain** — bans, mutes, warns, locks, purges, federations, global bans — with audit logging to a designated log channel.
3. **Entertainment and engagement systems** — a virtual economy (coins, bank, heist, loans, jail), a Ninja game (assassinations, kidnap, XP), a Castle Kingdom strategy game, a Farm simulation, and a Quiz game — all sharing a single coin wallet.
4. **Group utility** — welcome/goodbye messages, notes, keyword filters, rules, RSS feeds, scheduled announcements, AFK tracking, timezone tools, and more.

---

## Target Users

| User Type | Who They Are |
|-----------|-------------|
| **Group Owner** | The Telegram group creator who deploys the bot and owns its configuration |
| **Group Admin / Moderator** | Trusted members who use moderation commands daily |
| **Regular Group Member** | End users who interact with games, economy, and entertainment features |
| **Bot Owner (Developer)** | The operator of the bot instance — has access to sudo/owner commands |

Primary audience: **Arabic-speaking** Telegram communities (the bot interface, game system, and economy are Arabic-first).

---

## Core Philosophy

> **One bot, one community, zero compromises.**

- Every feature should work independently — enabling CAPTCHA should not require enabling Bayes filter.
- Protection layers should coexist and reinforce each other, never replace each other.
- The entertainment system should feel like a natural extension of the community, not an external import.
- Group administrators own their data and their configuration. Nothing is hardcoded that should be configurable.
- The bot must remain operational even if individual plugins fail — resilience by design.

---

## Product Goals

1. **Zero external dependencies for core protection** — CAS check uses a public API; all other protection (Bayes, regex, flood, CAPTCHA) runs locally.
2. **Per-group configuration** — every protection feature is independently toggleable per group via `ChatFeatureSettings`.
3. **Unified economy** — all games share a single `Wallet` model via `core/game_wallet.py` so coins earned in one game are spendable in another.
4. **Auditability** — moderation actions (ban, mute, warn, kick) are logged to an admin-configured log channel.
5. **Survivability** — the plugin loader continues on individual plugin failures; a broken plugin never brings down the bot.
6. **Extensibility** — adding a new feature requires only creating a new `plugins/feature.py` file with an `async def register(application)` function.

---

## Long-Term Mission

To be the definitive open-source Telegram management bot for Arabic communities — fully self-hosted, modular, documented, and maintainable by any future developer or AI agent without requiring any prior knowledge of the project's history.

---

## Success Criteria

- A new developer can clone the repository and understand the full system within 30 minutes.
- Any protection feature can be enabled or disabled per group without restarting the bot.
- The bot loads successfully with only `BOT_TOKEN`, `OWNER_IDS`, and a PostgreSQL connection.
- All game economy values remain consistent — coins earned in Quiz appear in `/wallet` balance.
- No single plugin failure crashes the bot or blocks other plugins from loading.
- The documentation system is always current with the codebase.
