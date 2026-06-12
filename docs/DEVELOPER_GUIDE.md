# DEVELOPER_GUIDE.md

## How Does a Developer Work on This Project?

This guide covers everything a developer needs to understand, run, extend, and debug Hozan Bot. No prior knowledge of the project is assumed.

---

## 1. Prerequisites

- Python 3.11+
- PostgreSQL database (Replit provides this automatically; see Section 3)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID for `OWNER_IDS`

---

## 2. Project Setup

### Clone and Install
```bash
cd Ninja
pip install -r requirements.txt
```

### Environment Variables
The following secrets must be set (in Replit: use Secrets tab):

| Variable | Value | Required |
|----------|-------|----------|
| `BOT_TOKEN` | Telegram bot token | ✅ |
| `OWNER_IDS` | Your Telegram user ID (e.g., `123456789`) | ✅ |
| `DATABASE_URL` | PostgreSQL connection string | Auto (Replit provides) |

Optional variables (with defaults):
```
LOG_LEVEL=INFO                # DEBUG for development
BAYES_MIN_CORPUS_SIZE=200     # Min training samples before Bayes activates
BAYES_SPAM_THRESHOLD=0.90     # Spam confidence threshold
CAPTCHA_TIMEOUT_SECONDS=120   # Seconds before CAPTCHA expires
STRICT_GBAN=False             # Enforce gbans on every message
SUDO_USERS=                   # Comma-separated Telegram IDs
SUPPORT_USERS=                # Comma-separated Telegram IDs
```

### Database Initialisation
On first run, Alembic creates all tables automatically via `main.py → init_db()`:
```bash
# If you need to run migrations manually:
alembic upgrade head
```

### Run the Bot
```bash
cd Ninja
python main.py
```

---

## 3. Replit-Specific Setup

In Replit, use the "Ninja Bot" workflow (internal name) which runs:
```
cd Ninja && pip install -r requirements.txt -q && python main.py
```

The Replit PostgreSQL database is automatically available via `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` environment variables. `config.py` builds the `DATABASE_URL` from these if `DATABASE_URL` is not explicitly set.

**Known quirk:** The `BOT_TOKEN` secret was previously breaking due to a trailing newline in the Replit Secrets UI. The `_require()` function in `config.py` strips whitespace — this fix is already in place. Do not revert it.

---

## 4. How to Add a New Plugin

Every plugin is a `.py` file in `Ninja/plugins/` with an `async def register(application)` function.

### Minimal Plugin Template
```python
"""
plugins/my_feature.py — Short description of what this plugin does.

Commands:
  /mycommand — What it does
"""
from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.helpers.chat_status import user_admin  # if admin-only
from database.engine import get_session

logger = logging.getLogger(__name__)


async def my_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not message or not user:
        return

    # Group-only guard (if needed):
    if chat.type == "private":
        await message.reply_text("⚠️ This command only works inside groups.")
        return

    # Your logic here
    await message.reply_text(f"Hello {user.first_name}!")


async def register(application: Application) -> None:
    application.add_handler(CommandHandler("mycommand", my_command_handler))
    logger.info("my_feature plugin registered.")
```

### Plugin Rules
1. **Must export** `async def register(application: Application) -> None`
2. **Module docstring** must describe commands and behaviour
3. **Use `@user_admin`** decorator for admin-only commands
4. **Use `get_session()`** for all database access
5. **Use `core/game_wallet.py`** for any coin transactions
6. **English text** for all user-facing messages
7. **HTML parse mode** — use `<b>`, `<code>` tags, not Markdown
8. **Call `logger.info()` at end of `register()`** to confirm loading

---

## 5. How to Add a New Database Model

### Step 1: Define the Model
Add to the appropriate model file:
- Core moderation → `database/models.py`
- Extended per-feature → `database/models_extra.py`
- New game → `database/game_models.py` or create `database/<game>_models.py`

```python
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database.engine import Base

class MyModel(Base):
    __tablename__ = "my_table"
    
    id        = Column(Integer, primary_key=True, autoincrement=True)
    chat_id   = Column(BigInteger, ForeignKey("chats.id"), nullable=False)
    user_id   = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    value     = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### Step 2: Generate Migration
```bash
alembic revision --autogenerate -m "add my_table"
```

### Step 3: Review and Apply
```bash
# Review the generated file in alembic/versions/
alembic upgrade head
```

**Never modify tables manually.** Always use Alembic so the migration history stays accurate.

---

## 6. How to Add a New Repository Function

Repository functions live in `db/repositories/`. Add to an existing file if the model already has a repository, or create a new file:

```python
# db/repositories/my_feature.py
from __future__ import annotations
import logging
from database.engine import get_session
from database.models import MyModel

log = logging.getLogger(__name__)

async def create(chat_id: int, user_id: int, value: str) -> MyModel:
    async with get_session() as session:
        obj = MyModel(chat_id=chat_id, user_id=user_id, value=value)
        session.add(obj)
        await session.flush()
        return obj

async def get(chat_id: int, user_id: int) -> MyModel | None:
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(MyModel).where(
                MyModel.chat_id == chat_id,
                MyModel.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()
```

---

## 7. How to Add a New Per-Group Feature Toggle

1. **Add a boolean column** to `ChatFeatureSettings` in `database/models.py`:
   ```python
   my_feature_enabled = Column(Boolean, default=False, nullable=False)
   ```

2. **Generate and apply migration** (see Section 5, Step 2-3).

3. **Read setting in plugin:**
   ```python
   from db.repositories.settings import get_or_create
   settings = await get_or_create(chat.id)
   if not settings.my_feature_enabled:
       return
   ```

4. **Add toggle to settings panel** in `settings_panel.py` (optional but preferred over standalone command).

---

## 8. Understanding the Spam Filter Pipeline

Messages pass through handlers registered in numbered groups (PTB mechanism):
- Group 0: Default handlers (commands)
- Group 1: Bayes spam filter
- Group 2: Regex/word-list filter
- Groups 3–20: Individual protection plugins

To interrupt the pipeline (stop lower-priority handlers from running):
```python
raise ApplicationHandlerStop  # Stops further handler groups
```

To continue the pipeline (let other handlers also process this message):
```python
return  # or return None (default)
```

---

## 9. Adding a New Game

New games must follow the Shared Economy Convention:

```python
from core.game_wallet import add_coins, deduct_coins, get_wallet

# Award coins:
async with get_session() as session:
    wallet = await add_coins(session, user.id, 100)
    await update.message.reply_text(f"🎉 You won 100 coins! Balance: {wallet.coins}")

# Deduct coins (with insufficient-funds check):
async with get_session() as session:
    wallet = await deduct_coins(session, user.id, 50)
    if wallet is None:
        await update.message.reply_text("❌ Insufficient balance.")
        return
```

Create a dedicated model file if the game has persistent state (e.g., `database/my_game_models.py`).

---

## 10. Permission Checking

### Command-Level Permission Checks
```python
from core.helpers.chat_status import user_admin, bot_admin

@user_admin  # Requires calling user to be a group admin
async def my_admin_command(update, context):
    ...

@bot_admin   # Requires bot to have admin rights in the group
async def my_bot_admin_command(update, context):
    ...
```

### Owner-Only Commands
```python
from config import settings

async def my_owner_command(update, context):
    if update.effective_user.id not in settings.OWNER_IDS:
        await update.message.reply_text("❌ This command is for the owner only.")
        return
    ...
```

### Elevated Users (Sudo/Support/Tiger/Wolf)
```python
from disasters import is_sudo_user, is_support_user, is_tiger_user

if is_sudo_user(user_id) or is_support_user(user_id):
    # Has elevated privileges
    ...
```

---

## 11. Rate Limiting and Bulk Operations

When performing actions on many chats (federation bans, global bans, broadcasts):

```python
from core.rate_limiter import RateLimiter

limiter = RateLimiter()

for chat_id in all_chats:
    await limiter.acquire()  # Throttle to safe rate
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    except (BadRequest, Forbidden):
        pass  # Silently skip chats where bot can't act
```

---

## 12. Log Channel Integration

To make a new moderation action log to the admin's log channel:

```python
from core.log_channel import log_action

# Anywhere in a handler (does not require @loggable decorator):
await log_action(
    context=context,
    chat_id=chat.id,
    action_type="BAN",
    target_user_id=user.id,
    actor_user_id=update.effective_user.id,
    reason=reason,
)
```

Or use the `@loggable` decorator for automatic wrapping.

---

## 13. Testing and Debugging

### Development Bot
Create a separate bot via @BotFather for development to avoid disrupting production groups.

### Enable Debug Logging
```bash
LOG_LEVEL=DEBUG python main.py
```

Or via bot command (owner only):
```
/debug on
```

### Check Plugin Load Status
The startup log shows each plugin's load result:
```
INFO:core.plugin_loader: Loaded plugin: bans
INFO:core.plugin_loader: Loaded plugin: captcha
WARNING:core.plugin_loader: Skipped (no register): fun_strings  ← expected
ERROR:core.plugin_loader: Failed to load: broken_plugin — ImportError: ...
```

`fun_strings` generates a WARNING at every start — this is expected and correct. It is a data module imported by `fun.py`, not a plugin.

### Check Bot Permissions
```
/checkperms
```
Shows exactly which permissions the bot has in the current group.

### Health Check
```
/health
```
Shows uptime, database connectivity, and plugin count.

---

## 14. Common Gotchas

| Gotcha | Explanation | Fix |
|--------|-------------|-----|
| `fun_strings.py` WARNING at startup | Expected — it's a data module, not a plugin | Ignore |
| `BOT_TOKEN` fails with whitespace error | Trailing newline in secret value | `config.py._require()` already strips — don't revert |
| `CaptchaPending` not deleted after kick | Timeout job fires after manual kick | Race condition — safe to ignore, `_remove_pending` handles None |
| Alembic `autogenerate` missing a column | `models.py` not imported in `alembic/env.py` | Add import to `env.py` target_metadata |
| `get_session()` rollback on flush | Transaction rolled back silently | Check for IntegrityError (FK violation) — ensure_user_and_chat first |
| `chatbot.py` state lost on restart | Uses in-memory set | Known tech debt — tracked in LIVING_PROJECT_LOG.md |
| `elevated_users.json` not found | Bot runs from wrong directory | Ensure working directory is `Ninja/` |
| Plugin load order matters | Alphabetical — prefix file name to force order | Use `00_` prefix if a plugin must load first |

---

## 15. File Structure Quick Reference

```
Ninja/
├── main.py              ← START HERE — startup sequence
├── config.py            ← All configuration + secrets
├── core/
│   ├── plugin_loader.py ← How plugins are discovered
│   ├── game_wallet.py   ← ALWAYS use this for coin transactions
│   ├── spam_bayes.py    ← Bayes classifier engine (don't modify internals)
│   └── helpers/
│       └── chat_status.py ← @user_admin, @bot_admin decorators
├── database/
│   ├── engine.py        ← get_session() lives here
│   ├── models.py        ← Core tables (User, Chat, ChatFeatureSettings, etc.)
│   └── models_extra.py  ← Extended tables
├── db/repositories/     ← Preferred data access layer
├── economy/             ← Economy sub-package
├── quiz/                ← Quiz sub-package
├── plugins/             ← Drop new .py files here
└── alembic/             ← Database migrations
```
