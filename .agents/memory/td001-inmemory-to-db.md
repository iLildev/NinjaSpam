---
name: TD-001 in-memory to DB migration
description: chatbot/clean_blue/global_ignore persistent state now in PostgreSQL
---

## What was done

- `ChatFeatureSettings` model gained `chatbot_enabled` and `clean_blue_enabled` bool columns
- `GlobalIgnore` table added in `database/models_extra.py`
- `db/repositories/global_ignore.py` created with add/remove/check/list methods
- Alembic migration `36fda794cb68` applied successfully

**Why:** In-memory sets (`_CHATBOT_ENABLED`, `_ENABLED_CHATS`) lost state on every bot restart, causing features to reset silently.

**How to apply:** Any new toggle feature must use `ChatFeatureSettings` columns, not module-level sets.
