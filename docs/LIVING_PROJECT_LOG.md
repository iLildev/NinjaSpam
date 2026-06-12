# LIVING_PROJECT_LOG.md

## Where Were We, Where Are We Now, and Where Are We Going?

This is the permanent memory of the project. It must never be edited to look tidy or complete. It reflects the real state of the project at every point in time.

**RULES:**
- Never delete completed tasks
- Never erase history
- Never replace old milestones
- Convert ⬜ to ✅ when work is completed
- Keep a permanent historical record
- Update this file after every significant change

---

## Current Project Status (as of June 12, 2026)

The bot is fully operational on Replit. 105 of 106 plugins load successfully at runtime (the 1 "missing" load is `fun_strings.py`, which is a data module by design — not a plugin). The bot connects to the Replit-provisioned PostgreSQL database via asyncpg. All core systems are functional: moderation, protection pipeline, games, economy, federation, and CAPTCHA.

The documentation system has just been created (PHASE 1 audit + PHASE 2 documentation). No code has been modified during the documentation phase.

**Infrastructure state:**
- Runtime: Python 3.11, python-telegram-bot v20.7 async
- Database: Replit PostgreSQL (asyncpg + SQLAlchemy 2.0)
- Secrets configured: `BOT_TOKEN` ✅, `OWNER_IDS` ✅, `DATABASE_URL` ✅ (auto from Replit)
- Secrets NOT configured: `SPAMWATCH_TOKEN` (SpamWatch integration silently inactive)
- Plugins: 105/106 loaded (fun_strings.py expected skip)
- Alembic migrations: applied

**Known active bugs/degraded features:**
1. `chatbot.py` — state lost on restart (in-memory)
2. `clean_blue.py` — state lost on restart (in-memory)
3. `global_ignore.py` — state lost on restart (in-memory)
4. `disasters.py` — bot hierarchy stored in JSON, not database
5. `spamwatch.py` — silently inactive (no token)

---

## Long-Term Vision

Ninja Bot should become the definitive open-source Arabic-language Telegram group management bot. It should be:
- **Fully self-documenting** — any new developer or AI agent can understand the entire system from `docs/` alone, without requiring access to the original author's history or conversations.
- **100% database-backed** — no persistent state stored in in-memory structures or flat files. Full survivability across restarts and deployments.
- **Architecturally consistent** — all plugins follow the same patterns: repository layer, ChatFeatureSettings for toggles, game_wallet.py for coins, HTML parse mode.
- **Community-tested** — features validated by real Arabic-speaking Telegram communities, with user feedback integrated into the roadmap.
- **Extensible by anyone** — a developer unfamiliar with the project can add a new feature by reading only `DEVELOPER_GUIDE.md` and following its templates.

---

## Active Objectives

1. ✅ PHASE 1: Full repository audit — completed June 2026
2. ✅ PHASE 2: Documentation generation (10 docs files) — completed June 2026
3. ⬜ PHASE 3: Documentation validation against live codebase
4. ⬜ PHASE 4: Documentation quality review (score each doc ≥ 9/10)
5. ⬜ PHASE 5: Full project evaluation report (UX, architecture, debt, orphans)
6. ⬜ PHASE 6: Restructuring plan
7. ⬜ PHASE 7: Future Agent Governance setup

---

## Roadmap

### Infrastructure & Reliability
- ✅ Migrate bot to Replit
- ✅ Fix BOT_TOKEN trailing whitespace bug (`config.py::_require()`)
- ✅ Confirm 105/106 plugins load successfully
- ⬜ **[TD-001]** Migrate `chatbot.py` in-memory state to `ChatFeatureSettings.chatbot_enabled`
- ⬜ **[TD-001]** Migrate `clean_blue.py` in-memory state to `ChatFeatureSettings.clean_blue_enabled`
- ⬜ **[TD-001]** Migrate `global_ignore.py` in-memory state to a dedicated DB table
- ⬜ **[TD-002]** Migrate bot hierarchy (`disasters.py`) from `elevated_users.json` to PostgreSQL `ElevatedUser` table
- ⬜ **[TD-005]** Fix hardcoded log file path in `dev_cmds.py`
- ⬜ **[TD-006]** Add startup warning when `SPAMWATCH_TOKEN` is not set

### Architecture & Code Quality
- ✅ Create `db/repositories/` layer (bans, warns, settings, members)
- ⬜ **[TD-003]** Expand repository layer to cover Ninja game models
- ⬜ **[TD-003]** Expand repository layer to cover Castle game models
- ⬜ **[TD-003]** Expand repository layer to cover Farm game models
- ⬜ **[TD-003]** Expand repository layer to cover Federation models
- ⬜ **[TD-003]** Expand repository layer to cover Welcome models
- ⬜ **[TD-004]** Consolidate dual warn tracking (`WarnEntry` vs `ChatMember.warn_count`)
- ⬜ Move `fun_strings.py` to `core/fun_data.py` to eliminate startup WARNING

### Documentation
- ✅ `PROJECT_VISION.md` — created
- ✅ `DESIGN_PRINCIPLES.md` — created
- ✅ `ARCHITECTURE.md` — created
- ✅ `BOT_UX_RULES.md` — created
- ✅ `MENU_TREE.md` — created
- ✅ `FEATURES.md` — created
- ✅ `PROJECT_GLOSSARY.md` — created
- ✅ `DEVELOPER_GUIDE.md` — created
- ✅ `ARCHITECTURAL_DECISIONS.md` — created
- ✅ `DECISION_FRAMEWORK.md` — created
- ✅ `LIVING_PROJECT_LOG.md` — created
- ⬜ PHASE 3: Validate documentation against codebase
- ⬜ PHASE 4: Quality review and scoring
- ⬜ PHASE 5: Full evaluation report

### Features & UX
- ⬜ **[TD-007]** Verify Privileged Group Intents for `couples.py`
- ⬜ **[TD-008]** Re-upload GIF/sticker file IDs in `fun_strings.py` using active bot token
- ⬜ Add SpamWatch token to Replit secrets (activates SpamWatch global ban database)
- ⬜ Standardise all user-facing English text to Arabic (chatbot.py, settings_panel English labels)
- ⬜ Deprecate standalone commands that duplicate `/settings` panel functionality
- ⬜ Resolve dual CAPTCHA overlap (standard vs adaptive — clarify which takes precedence on join)

---

## Completed Milestones

| Date | Milestone |
|------|-----------|
| June 2026 | Bot migrated to Replit. `BOT_TOKEN` secret configured. Trailing whitespace bug discovered and fixed in `config.py`. |
| June 2026 | Confirmed 105/106 plugins load successfully. `fun_strings.py` skip is expected and documented. |
| June 2026 | PHASE 1 audit completed: all 106 plugin files, 5 model files, economy sub-package, quiz sub-package, core modules, and repository layer fully analysed. |
| June 2026 | PHASE 2 documentation completed: 10 files created in `/docs/` totalling 3,640+ lines of project-specific documentation. |

---

## Assumptions

These are the foundational assumptions under which the project operates. Unrecorded assumptions are future bugs.

1. **Single-instance deployment**: The bot runs as a single process. There is no horizontal scaling, load balancing, or multi-instance coordination. Systems that use in-memory state (once fixed) can use process-local caches safely.

2. **Telegram Bot API v6+**: Features like reactions, topics, and permission models depend on API versions. The bot is tested against the Telegram Bot API version supported by python-telegram-bot v20.7.

3. **Replit PostgreSQL is durable**: The Replit-provisioned PostgreSQL instance is treated as production-grade durable storage. If Replit changes their database offering, a migration plan is required.

4. **Arabic is the dominant language**: Over 90% of users are Arabic-speaking. English-only content in user-facing messages is a bug, not an acceptable default.

5. **Groups are supergroups**: All group-specific features assume Telegram supergroups (not legacy basic groups). Features like topics, admin permissions, and member counts behave differently in basic groups.

6. **Bot is admin in managed groups**: The bot assumes it has at least "Delete Messages", "Ban Users", and "Restrict Members" permissions in groups it manages. Features silently degrade when permissions are missing.

7. **`OWNER_IDS` is a trusted set**: Owner-level commands (eval, exec, sh) are unrestricted. The owner is assumed to be a trusted developer. Compromise of an owner account is not in the threat model.

8. **CAS API availability**: The CAS check on join assumes `api.cas.chat` is available. There is no fallback when CAS is unavailable — new member joins proceed unchecked.

9. **FallenRobot chatbot API availability**: The chatbot feature assumes `fallenxbot.vercel.app` is available. The bot silently swallows failures — users get no response but no error either.

10. **PTB JobQueue is reliable**: CAPTCHA expiry, nightmode scheduling, RSS polling, and quiz timeouts all depend on PTB's embedded APScheduler. If jobs are lost (e.g., OOM restart), pending CAPTCHA challenges will hang forever (no record of the timer).

---

## Current Project Risks

### Technical Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| In-memory feature state lost on restart (`chatbot`, `clean_blue`, `global_ignore`) | Medium | Certain (every restart) | TD-001: Migrate to DB |
| Bot hierarchy JSON corruption (`elevated_users.json`) | High | Low | TD-002: Migrate to DB |
| SpamWatch silently inactive | Medium | Already occurring | Add SPAMWATCH_TOKEN or add startup warning |
| CAPTCHA job orphaned on unexpected restart | Medium | Low-Medium | Add orphan cleanup on startup |
| `fun_strings.py` GIF file IDs expiry | Low | Low-Medium | Re-upload with active token |
| CAS API downtime allows spam bots on join | Medium | Low | Implement timeout-based fallback |
| FallenRobot API downtime disables chatbot entirely | Low | Medium | Already has silent fail — acceptable |
| Alembic migration not run after code deploy | High | Low | `main.py` calls `alembic upgrade head` on startup |
| Dual warn tracking drift (`WarnEntry` vs `ChatMember.warn_count`) | Medium | Medium | TD-004: Remove duplicate |
| `couples.py` fails without privileged intents | Medium | High | TD-007: Document required bot settings |

### Architectural Risks

| Risk | Severity | Description |
|------|----------|-------------|
| Direct ORM usage bypasses repository layer | Low | Ongoing tech debt — no data loss, just harder maintenance |
| `ChatFeatureSettings` width | Low | Wide table acceptable — no performance concern at current scale |
| No horizontal scaling support | Medium | Single-process assumption — acceptable for current deployment |
| 105 plugins loaded sequentially at startup | Low | Start time ~5-10 seconds — acceptable |

### UX Risks

| Risk | Description |
|------|-------------|
| English-only messages in some plugins | Users may not understand captcha text words (WELCOME, CONFIRM, etc.) — currently English |
| Dual CAPTCHA plugins active simultaneously | Adaptive captcha and standard captcha both handle new member joins — unclear precedence |
| Command duplication | `/warnlimit` and settings panel both control warn limit — users may not know which is canonical |
| Chatbot state loss on restart | Users who enabled chatbot will find it disabled after every restart until TD-001 is resolved |

### Maintenance Risks

| Risk | Description |
|------|-------------|
| No tests | Zero automated tests. All validation is manual. A broken plugin is only discovered when it's triggered in production. |
| 105 plugins to maintain | Large surface area. A change to `ChatFeatureSettings` requires reviewing all 105 plugins. |
| `fun_strings.py` hardcoded Telegram file IDs | If bot token changes, all GIF/sticker IDs must be re-uploaded. |

---

## Lessons Learned

1. **Trailing whitespace in Replit secrets is silent and deadly.** The `BOT_TOKEN` issue caused the bot to fail with a cryptic `InvalidToken` error. Solution: always `.strip()` all environment variable values in `config.py`. Any secret value coming from a user interface should be treated as potentially whitespace-contaminated.

2. **`fun_strings.py` is correctly placed but generates confusing noise.** A data module in `plugins/` that isn't itself a plugin generates a WARNING on every startup. This is harmless but confusing. Data modules that serve only one plugin should either be in `core/` or share a `_` prefix to make their non-plugin nature explicit.

3. **In-memory state for feature toggles is an antipattern that accumulates silently.** Three plugins (`chatbot.py`, `clean_blue.py`, `global_ignore.py`) were written with in-memory sets for "enabled chats". This pattern looks expedient but creates a hidden bug that only surfaces on restart. The fix is always the same: `ChatFeatureSettings` column + Alembic migration. Never allow this pattern in new code.

4. **The plugin loader's resilience is a double-edged sword.** It prevents one broken plugin from crashing the bot, but it also means a failed plugin load is easy to miss. Monitoring the startup logs is important — a WARNING about a plugin load failure is not the same as it being intentionally skipped.

5. **CAPTCHA text challenge uses English words in an Arabic-first bot.** The text CAPTCHA word list (`_TEXT_WORDS` in `captcha.py`) contains English words like "WELCOME", "CONFIRM", "VERIFIED". This works mechanically but is culturally inconsistent in an Arabic-primary bot. Future improvements should add Arabic word options.

6. **Alembic `upgrade head` at startup is the right pattern for Replit.** Since Replit doesn't have a separate migration step, calling `alembic upgrade head` in `main.py::init_db()` ensures schema is always current on startup. This is safe because Alembic migrations are idempotent — running an already-applied migration is a no-op.

7. **Documentation written after a full audit is more accurate than documentation written during development.** Writing `FEATURES.md` and `ARCHITECTURE.md` from a complete codebase reading reveals things that incremental documentation misses: orphaned code, hidden dependencies, and undocumented interaction between systems.

---

## Strategic Opportunities

### OPP-001 — Migrate to Full Settings Panel UI (High Priority)
**Description:** Several features still require memorising specific commands rather than being accessible via the `/settings` inline panel. A complete settings panel that covers all protection features would eliminate the need to remember commands.  
**Expected benefit:** Reduced admin friction, more features used correctly.  
**Expected impact:** High — affects all group administrators.  
**Complexity:** Medium  
**Priority:** High  
**Dependencies:** No new features required — refactor of existing commands.  
**Status:** Not started

---

### OPP-002 — Arabic CAPTCHA Text Words
**Description:** The text CAPTCHA mode uses English words (`WELCOME`, `CONFIRM`, etc.). Adding Arabic word options would make the CAPTCHA more natural for Arabic-speaking users.  
**Expected benefit:** Better user experience for Arabic users unfamiliar with English.  
**Expected impact:** Low-Medium  
**Complexity:** Low (add Arabic strings to `_TEXT_WORDS` list)  
**Priority:** Medium  
**Dependencies:** None  
**Status:** Not started

---

### OPP-003 — SpamWatch Integration Activation
**Description:** `spamwatch.py` is fully implemented but silently inactive because `SPAMWATCH_TOKEN` is not configured. Obtaining a SpamWatch API token would add a third global spam database (in addition to CAS and gban).  
**Expected benefit:** Broader coverage of known spammers.  
**Expected impact:** Medium  
**Complexity:** Very Low (add one secret)  
**Priority:** Medium  
**Dependencies:** Requires SpamWatch API token from https://spamwat.ch  
**Status:** Blocked (waiting for token)

---

### OPP-004 — Automated Test Suite
**Description:** The bot currently has no automated tests. Adding unit tests for core modules (`spam_bayes.py`, `game_wallet.py`, `rate_limiter.py`) and integration tests for key plugin flows would catch regressions before they reach production.  
**Expected benefit:** Confidence in changes, faster development.  
**Expected impact:** High long-term  
**Complexity:** High  
**Priority:** Medium  
**Dependencies:** Requires test database setup, PTB test framework  
**Status:** Not started

---

### OPP-005 — Leaderboard Unification
**Description:** There are multiple separate leaderboards: `/ninjarank`, `/top_rulers`, `/richlist`, `/thieftop`, `/top`, `/topusers`. A unified leaderboard hub command (`/leaderboards`) with category navigation would improve discoverability.  
**Expected benefit:** Better feature discoverability, cleaner UX.  
**Expected impact:** Low-Medium  
**Complexity:** Low  
**Priority:** Low  
**Dependencies:** None  
**Status:** Not started

---

### OPP-006 — Fun Strings Re-upload
**Description:** `fun_strings.py` contains Telegram file IDs (GIFs, stickers) uploaded with a previous bot token. These IDs should be re-uploaded using the current bot token to ensure they remain accessible long-term.  
**Expected benefit:** Prevents silent failure of `/slap`, `/pat`, and other fun commands.  
**Expected impact:** Low  
**Complexity:** Low (run each file_id through the bot once to re-upload)  
**Priority:** Low  
**Dependencies:** Active bot token required  
**Status:** Not started

---

## Abandoned Ideas

*No ideas have been formally rejected yet. This section will be populated as evaluation proceeds through PHASE 5 and PHASE 6.*

---

## Advice For Future Developers

**Before writing a single line of code, read these four documents in order:**
1. `docs/PROJECT_VISION.md` — understand WHY this project exists
2. `docs/DESIGN_PRINCIPLES.md` — understand the non-negotiable rules
3. `docs/ARCHITECTURE.md` — understand HOW the system works
4. `docs/DEVELOPER_GUIDE.md` — understand the coding patterns

**The three most important technical rules:**
1. Never use in-memory sets/dicts for per-group feature state. Always use `ChatFeatureSettings` + Alembic migration.
2. Never modify `Wallet.coins` directly. Always use `core/game_wallet.py::add_coins()` and `deduct_coins()`.
3. Always call `ensure_user_and_chat()` from `db/repositories/base.py` before inserting any row that has `chat_id` or `user_id` foreign keys. PostgreSQL enforces FK constraints — this will cause an IntegrityError without it.

**The most common mistakes to avoid:**
- Reading `fun_strings.py` WARNING at startup and thinking something is broken — it's a data module, not a plugin.
- Writing bot responses with Markdown syntax instead of HTML tags — the bot uses `ParseMode.HTML` globally.
- Creating a new standalone command for something that should be a `/settings` panel toggle.
- Using `disasters.py` elevated user functions without understanding they currently read from JSON, not the database. This is known tech debt (TD-002).

**When you're unsure about a design decision:**
Read `docs/DECISION_FRAMEWORK.md`. It has specific frameworks for every major decision category: plugin vs existing code, in-memory vs database, settings panel vs command, repository vs direct ORM.

**Document before you implement:**
Write your `ARCHITECTURAL_DECISIONS.md` entry and update `LIVING_PROJECT_LOG.md` before writing code. The decision record is part of the product, not an afterthought.

---

## Recommended Next Actions

In recommended priority order:

1. **[IMMEDIATE]** Review all 10 documentation files (PHASE 3 + PHASE 4 from governance prompt) — validate accuracy against codebase, score each doc.

2. **[HIGH]** Fix TD-001: Migrate `chatbot.py` and `clean_blue.py` in-memory state to `ChatFeatureSettings` DB columns. This is the highest-impact low-complexity fix available.

3. **[HIGH]** Fix TD-002: Migrate bot hierarchy (`elevated_users.json`) to PostgreSQL `ElevatedUser` table. This is a data integrity risk.

4. **[HIGH]** Add startup validation: if `SPAMWATCH_TOKEN` is not set, log a WARNING to the console/log channel so the feature degradation is visible.

5. **[MEDIUM]** Obtain and configure `SPAMWATCH_TOKEN` to activate SpamWatch integration (OPP-003).

6. **[MEDIUM]** Fix TD-007: Verify and document Privileged Member Intents requirement for `couples.py`.

7. **[MEDIUM]** Fix TD-004: Remove `ChatMember.warn_count` duplicate and consolidate warn tracking to `WarnEntry` only.

8. **[MEDIUM]** Move `fun_strings.py` to `core/fun_data.py` and update `fun.py` import, eliminating the startup WARNING.

9. **[LOW]** Add Arabic words to CAPTCHA text challenge word list (OPP-002).

10. **[LOW]** Re-upload `fun_strings.py` GIF/sticker file IDs with active bot token (OPP-006).

11. **[LOW]** Begin gradual repository layer expansion (TD-003) — start with federation models.
