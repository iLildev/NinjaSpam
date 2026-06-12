# DECISION_FRAMEWORK.md

## How Should Future Decisions Be Made?

This document provides reusable frameworks for making consistent decisions when building or modifying Ninja Bot. Use it as a checklist before committing to any approach.

---

## Framework 1 — Should This Be a Plugin or Part of an Existing Plugin?

Ask these questions in order:

1. **Is this feature logically independent?** Can it be enabled/disabled without affecting any other feature?
   - YES → It should be a new plugin file.
   - NO → It belongs in an existing plugin.

2. **Does this feature have its own set of commands?** (More than 1-2 commands)
   - YES → New plugin file.
   - NO → Consider adding to the most logically related existing plugin.

3. **Is this feature large enough to warrant its own DB models?**
   - YES → New plugin + new model file.
   - NO → Reuse existing models if appropriate.

4. **Is this a sub-feature of an existing large system?** (e.g., a new Economy mechanic)
   - YES → Add to the existing sub-package (`economy/`, `quiz/`).
   - NO → New top-level plugin.

**Decision: When in doubt, make it a new plugin. The cost of an extra file is lower than the cost of entangled logic.**

---

## Framework 2 — Should This State Be In-Memory or In the Database?

| Question | In-Memory | Database |
|----------|-----------|----------|
| Must survive bot restart? | ❌ | ✅ |
| Per-group configuration? | ❌ | ✅ |
| Temporary (session-scoped)? | ✅ | ❌ |
| Large volume (thousands of items)? | ❌ (RAM) | ✅ |
| Performance cache (duplicate of DB)? | ✅ | N/A |
| Shared across multiple bot instances? | ❌ | ✅ |

**Rule:** If the answer to any of "must survive restart", "per-group configuration", or "shared across instances" is YES, use the database. In-memory is only acceptable for:
- TTL caches of recently-fetched DB data
- True session-scoped transient state (e.g., ConversationHandler state)
- Per-startup constant data (never changes after load)

**Never use in-memory sets to track "enabled/disabled per group" — this is the canonical antipattern (see AD-007 in ARCHITECTURAL_DECISIONS.md).**

---

## Framework 3 — Where Does New Per-Group Configuration Live?

```
Is this a boolean on/off toggle?
  └─ YES → Add column to ChatFeatureSettings (+ Alembic migration)
  └─ NO
      Is this a single value (number, enum, string)?
        └─ YES → Add column to ChatFeatureSettings (+ Alembic migration)
        └─ NO
            Is this a list/collection (e.g., a ban list, pattern list)?
              └─ YES → Create a new dedicated table with chat_id FK
              └─ NO → Reconsider the data model
```

**Never create a new settings table for a simple boolean or scalar value. ChatFeatureSettings is specifically designed to hold all per-group settings.**

---

## Framework 4 — Should This Use the Repository Layer or Direct ORM?

| Situation | Use Repository | Use Direct ORM |
|-----------|---------------|---------------|
| Reading/writing Bans, Warns, Settings, Members | ✅ (exists) | ❌ (tech debt) |
| New plugin touching an existing repository model | ✅ | ❌ |
| New plugin touching a model with no repository yet | Write repository first | Only if expedient |
| Complex multi-table query not covered by repository | OK to use direct ORM | Document why |
| One-time admin/dev script | OK | OK |

**Guidance:** When writing a new plugin that needs to read or write `BanRecord`, `WarnEntry`, `ChatFeatureSettings`, or `ChatMember`, always use the repository functions in `db/repositories/`. For other models, write a repository function if the same access pattern will be used in 2+ places.

---

## Framework 5 — Should This Be Arabic or English?

| Content Type | Language | Rationale |
|-------------|----------|-----------|
| User-facing messages (regular members) | Arabic | Primary user base |
| Error messages for regular members | Arabic | Accessibility |
| Admin commands and their responses | Arabic | Admins are part of the community |
| Game text, rewards, prompts | Arabic | Immersion |
| Owner/developer-only commands | English acceptable | These users are developers |
| Log channel audit messages | Arabic or bilingual | Admins read these |
| Code comments and docstrings | English or Arabic | Developer preference |
| Documentation files | English | Universal developer accessibility |
| Command names (e.g., `/warn`) | English only | Telegram API constraint |

---

## Framework 6 — When Should a Callback Verify Admin Status?

A callback handler should verify admin status if:
1. The action is destructive (ban, mute, kick, warn, delete federation)
2. The action changes group settings
3. The inline keyboard was sent to the group (others can see it and press it)

A callback handler does NOT need to verify if:
1. The keyboard is visible only to the bot owner (sent in owner's PM)
2. The action is purely informational (view only, no side effects)
3. The callback was specifically initiated by the user (and the callback data contains the user_id to verify)

**Always call `await query.answer()` before checking permissions — this prevents the loading spinner from hanging even when the user is rejected.**

---

## Framework 7 — Should This Feature Have an Inline Settings Panel or a Command?

| Criteria | Use Inline Panel | Use Command |
|----------|-----------------|------------|
| Multiple settings to configure | ✅ | ❌ |
| Simple on/off toggle | ✅ | Only if already in panel |
| Complex input required (text, number) | ❌ — use ConversationHandler | ✅ |
| First-time setup wizard | ✅ | ❌ |
| Already in `/settings` panel | Must stay in panel | Don't add duplicate command |

**The `/settings` panel is the canonical entry point for all per-group configuration. Do not add a standalone command if the setting is already in the panel. Duplicate entry points (AD-001 in BOT_UX_RULES.md Section 6.1) are explicitly prohibited.**

---

## Framework 8 — Should This Use the Economy System or the Game Wallet?

| Use Case | Use Economy (`economy/`) | Use Game Wallet (`core/game_wallet.py`) |
|----------|------------------------|-----------------------------------------|
| Social games (farm, castle, ninja, quiz) | ❌ | ✅ |
| Virtual banking, salary, investment, theft | ✅ | ❌ |
| Reward for completing a game action | ✅ (calls wallet internally) | ✅ |
| Penalty/cost for a game action | ✅ (calls wallet internally) | ✅ |
| Direct coin manipulation by admin | ✅ | ✅ |

**In practice:** Always use `core/game_wallet.py` directly. The economy system itself also uses `game_wallet.py` internally. There is no situation where a game plugin should import from `economy/models.py` directly.

---

## Framework 9 — How to Handle an External API Dependency

Before adding any external API call, answer:

1. **Is there a free, no-key-required public API?**
   - YES → Use it, but implement graceful failure (try/except, timeout, silent skip)
   - NO → Is there a Replit integration for this service? If yes, use it. If no, document the required secret in `ARCHITECTURE.md`.

2. **What happens if the API is down?**
   - Define and implement the fallback behaviour BEFORE deployment.
   - Acceptable fallbacks: silent skip, cached last result, "service unavailable" message.
   - Unacceptable: unhandled exception that crashes the handler.

3. **Does this API have rate limits?**
   - YES → Implement per-chat or per-user cooldowns before deploying.

4. **Is the API's data PII-sensitive?**
   - YES → Review privacy implications before sending user content to external APIs.

**Current external dependencies:** CAS API (no key), FallenRobot chatbot (no key, unstable), exchangerate.host (no key), countryinfo library (local), Wikipedia (library), Urban Dictionary (scraper).

---

## Framework 10 — Checklist Before Merging a New Feature

Before any new plugin or feature modification is complete, verify:

- [ ] Plugin file has a module docstring listing all commands and their descriptions
- [ ] `async def register(application)` is implemented and called `logger.info()` to confirm load
- [ ] User-facing messages are in Arabic
- [ ] HTML parse mode used (not Markdown)
- [ ] Admin-only commands use `@user_admin` decorator (not manual checks)
- [ ] Database operations use `async with get_session() as session:` pattern
- [ ] Coin operations use `core/game_wallet.py` (not direct model access)
- [ ] Persistent per-group state uses database (not in-memory sets)
- [ ] New tables have an Alembic migration
- [ ] `FEATURES.md` entry is written or updated
- [ ] If it's a per-group toggle: column added to `ChatFeatureSettings`
- [ ] External API calls have try/except with graceful fallback
- [ ] `ARCHITECTURAL_DECISIONS.md` entry added if a significant design decision was made
- [ ] Bot tested locally with a test group before deployment

---

## Framework 11 — When Is Technical Debt Acceptable?

Technical debt is acceptable when:
1. The correct fix requires significant refactoring with high risk
2. The current behaviour is functional (just not ideal)
3. The debt is explicitly documented in `LIVING_PROJECT_LOG.md` with a clear remediation path
4. No new features depend on the broken behaviour

Technical debt is NOT acceptable when:
1. It causes data loss on restart (in-memory state for persistent settings)
2. It causes inconsistent behaviour between bot restarts
3. It creates a security vulnerability
4. It silently corrupts data

**When you encounter undocumented technical debt, add it to `LIVING_PROJECT_LOG.md` immediately, even if you can't fix it right now.**
