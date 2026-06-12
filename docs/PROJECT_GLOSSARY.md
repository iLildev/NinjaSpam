# PROJECT_GLOSSARY.md

## Terms and Definitions

This glossary defines every domain-specific term, technical abbreviation, and project-internal concept used throughout this codebase and documentation. When in doubt about what something means, look here first.

---

## A

**`@loggable`**
A Python decorator defined in `core/log_channel.py`. When applied to a moderation handler function, it automatically forwards a formatted audit message to the group's configured log channel after the action executes. Required on all ban, mute, warn, and kick handlers.

**Adaptive CAPTCHA**
A variant CAPTCHA mode that increases difficulty based on detected threat level. Distinct from the standard CAPTCHA system (`captcha.py`). See: `adaptive_captcha.py`.

**Admin**
A Telegram group member with administrator status. In the bot's permission hierarchy, "admin" refers specifically to Telegram group admins. Distinct from "bot owner" and "sudo users."

**Alembic**
The SQLAlchemy-based database migration tool used to manage schema changes. Migration scripts are in `alembic/versions/`. All schema changes must be applied via Alembic, never manually.

**Anti-Raid Mode**
An automated group lockdown triggered when a mass-join event is detected. See: `antiraid.py`.

**APScheduler**
The job queue library embedded in python-telegram-bot. Used for all time-delayed bot operations (CAPTCHA expiry, nightmode, RSS polling, heist execution). Accessed via `context.job_queue`.

**asyncpg**
The PostgreSQL async driver used by SQLAlchemy. Provides non-blocking database connections compatible with Python's `asyncio` event loop.

---

## B

**BankAccount**
A DB model in `economy/models.py` representing a fictional bank account in the virtual economy. Distinct from `Wallet`. BankAccount holds an account number and participates in salary/transfer commands. Wallet holds the actual spendable coin balance.

**Bayesian Token**
A single word/token stored in the `bayesian_tokens` table with its ham count and spam count per chat. Used by `core/spam_bayes.py` to classify new messages. The more tokens a chat has, the more accurate the filter.

**Bayes Filter**
The machine-learning spam classifier in `bayes_filter.py` backed by `core/spam_bayes.py`. Uses naive Bayes classification trained per chat via the `/train` command. Distinct from the regex/word-list spam filter.

**Bot Owner**
The Telegram user whose ID is in `OWNER_IDS`. Has unrestricted access to all commands, including dangerous developer tools (eval, exec, broadcast, leave). Never shown in public help menus.

---

## C

**CAPTCHA**
Completely Automated Public Turing test to tell Computers and Humans Apart. In this bot, it is the new-member verification system in `captcha.py`. Three types: BUTTON, MATH, TEXT.

**`CaptchaPending`**
A DB model in `database/models.py` that stores an active CAPTCHA challenge. Contains `chat_id`, `user_id`, `challenge_message_id`, `expected_answer`, and `captcha_type`. Deleted on successful verification or timeout.

**CAS**
Combot Anti-Spam — a third-party global ban database at `api.cas.chat`. The bot queries CAS on new member joins when `cas_enabled=True` in `ChatFeatureSettings`. No API key required.

**Castle Gold**
`CastleResources.gold` — an in-game resource used for castle upgrades. NOT the same as `Wallet.coins`. Castle gold can be exchanged for Wallet coins via `/exchange_gold` at a fixed rate.

**`ChatFeatureSettings`**
The central per-group configuration table in `database/models.py`. One row per Telegram group. Contains boolean toggles for every protection feature, warn limit, warn action, CAPTCHA settings, and more. The single source of truth for all per-group configuration.

**`ChatFed`**
DB model in `database/models_extra.py`. Links a `chat_id` to a `fed_id`. One chat can only be in one federation at a time.

**Chatbot**
The AI chat feature in `chatbot.py` that auto-responds when the bot is mentioned or replied to. Uses the external FallenRobot API. **Currently uses in-memory state — settings lost on restart.**

**Clean Blue**
The `clean_blue.py` plugin that deletes failed command attempts (messages starting with `/` that don't match any handler). "Blue text" refers to the blue link Telegram renders for messages starting with `/`. **Currently uses in-memory state — settings lost on restart.**

**Coins**
The universal virtual currency. Stored in `Wallet.coins`. Earned via `/daily`, game wins, quiz answers, farm sales, economy salary, heist proceeds. Spent on game purchases, ransoms, army purchases, investments.

**Connect**
The `connect.py` feature allowing a group admin to issue group commands from their private chat. The connection is stored in `UserConnection` table. Also maintains an in-memory cache for performance.

---

## D

**Daily**
The `/daily` command in `wallet.py`. Awards 10 coins every 24 hours with a cooldown tracked in `Wallet.last_daily_at`.

**Demon**
Tier 2 in the bot's permission hierarchy (below Dragon, above Tiger). Set via `/addsupport` in `disasters.py`. Persisted to `elevated_users.json`.

**Disable System**
The `disable.py` plugin and `DisabledCommand` DB model that allow per-group command suppression. Uses a custom `DisableAbleCommandHandler` wrapper. Plugins must call `register_disableable()` to make their commands toggleable.

**Dragon**
Tier 1 (top) in the bot's non-owner permission hierarchy. Set via `/addsudo` in `disasters.py`. Persisted to `elevated_users.json`. Equivalent to "sudo user."

**DRM / Disaster Hierarchy**
Dragons → Demons → Tigers → Wolves. Each tier inherits the privileges of lower tiers. Managed in `disasters.py` via JSON file (`elevated_users.json`).

---

## E

**Economy**
The virtual coin-based economy system in `economy/` package. Includes salary, stealing, investment, heist, loans, and jail. Distinct from the game wallet (which is for games) but shares the same `Wallet` table.

**`ensure_user_and_chat()`**
A helper function in `db/repositories/base.py` that inserts User and Chat rows if they don't exist. Must be called before any foreign-key-constrained insert. This is the canonical way to handle FK integrity in repository functions.

---

## F

**FallenRobot API**
An external chatbot API at `https://fallenxbot.vercel.app/chatbot/message=<text>`. Used by `chatbot.py`. No API key required but availability is not guaranteed. The bot silently swallows failures.

**Farm**
The `farm_game.py` game. Players plant crops in `FarmPlot` rows, wait for real elapsed time, harvest into `FarmInventory`, and sell for `Wallet.coins`.

**`FedAdmin`**
DB model in `database/models_extra.py`. Grants a user federation-admin rights within a specific federation. Federation admins can `/fban` but cannot delete the federation.

**`FedBan`**
DB model in `database/models_extra.py`. Records a user who has been banned from a federation. When a new chat joins the federation, all existing `FedBan` rows are applied.

**Federation**
A named collection of Telegram groups that share a ban list. Created via `/newfed`, joined via `/joinfed`. When `/fban` is issued, the user is banned from every group linked to the federation.

**`fun_strings.py`**
A data-only file in `plugins/fun_strings.py` containing string templates, GIF file IDs, and sticker IDs used by `fun.py`. It is NOT a plugin (has no `register()` function). The plugin loader skips it with a WARNING — this is expected and correct behaviour.

---

## G

**`game_wallet.py`**
`core/game_wallet.py` — the canonical API for reading and modifying coin balances. Provides `get_wallet()`, `add_coins()`, `deduct_coins()`. All game plugins must use these functions rather than manipulating the `Wallet` model directly.

**`get_session()`**
The async context manager from `database/engine.py` that provides a SQLAlchemy async session with auto-commit on success and auto-rollback on exception. Always use this as `async with get_session() as session:`.

**Global Ban (gban)**
A ban applied to a user across ALL groups managed by the bot. Issued by sudo users via `/gban`. Stored in `GlobalBannedUser` table. When `STRICT_GBAN=True`, gbanned users are also kicked if they send any message.

**Global Ignore**
A bot-level list (managed by bot owner) of users the bot entirely ignores in all groups. Managed by `global_ignore.py`. **Currently uses in-memory state.**

---

## H

**Ham**
In Bayesian spam filtering: a legitimate (non-spam) message. The opposite of "spam." When an admin marks a message as not-spam with `/train`, its tokens are counted as ham.

**Heist**
The group cooperative robbery feature in `economy/heist.py`. Initiated by `/rob`, others join via `/joinrob` within 60 seconds. Success = coins for all participants, failure = jail for all.

**Homoglyph**
A character that visually resembles another but has a different Unicode code point. Example: Cyrillic "а" (U+0430) vs Latin "a" (U+0061). Used to evade word filters. Detected by `homoglyph.py`.

---

## I

**`ImmunityCard`**
A DB model in `database/game_models.py`. Players earn immunity cards from `/dig` in the Castle game. An active immunity card protects a castle from battle attacks.

**In-Memory State**
Bot state stored in Python variables (dictionaries, sets) rather than the database. Lost when the bot restarts. Current in-memory state usages: `chatbot.py` (`_CHATBOT_ENABLED`), `clean_blue.py` (`_ENABLED_CHATS`), `global_ignore.py`, `couples.py` (daily cache), `filters.py` (trigger cache), `connect.py` (connection cache).

---

## J

**Jail**
A virtual economy punishment. Players jailed via `/rob` failure or overdue loans cannot use economy commands until they `/bail` out. State stored in `JailRecord` table in `economy/models.py`.

**JobQueue**
python-telegram-bot's built-in wrapper around APScheduler. Used for scheduled jobs (CAPTCHA timeout, nightmode, RSS, heist execution). Accessed via `context.job_queue`.

---

## K

**Kidnap**
A Ninja game action where one player captures another and demands a coin ransom. State stored in `KidnapRecord` table. The kidnapped player must `/ransom` (pay) or another player can `/rescue` them.

---

## L

**Laplace Smoothing**
A mathematical technique applied in `spam_bayes.py` that adds 1 to every token count before computing spam probability. Prevents zero-probability tokens from making the entire message probability zero.

**Lock**
Restricting a specific message type in a group. Managed by `locks.py` and `LockSettings` table. Lock types include: sticker, gif, poll, forward, game, inline, photo, video, audio, document, contact, location.

**Log Channel**
A Telegram channel or group designated to receive audit logs of moderation actions. Set via `/setlog <channel_id>`. The `@loggable` decorator sends formatted action logs here.

---

## M

**Message Handler Group**
PTB's mechanism for ordering message handlers. Lower group numbers run first. The three-phase spam pipeline uses groups 1 (Bayes), 2 (regex), and 3+ (attribute filters). Each plugin registers its handlers in a specific group to control execution order.

**Migration**
A versioned database schema change tracked by Alembic. Run with `alembic upgrade head` to apply all pending migrations. Required after any changes to SQLAlchemy model definitions.

**Mute**
Restricting a user's ability to send messages. In Telegram, implemented via `ChatPermissions(can_send_messages=False)`. Stored as a time-limited restriction, managed by `muting.py`.

---

## N

**NinjaProfile**
DB model in `database/ninja_models.py`. Stores a player's XP, kill count, death count, kidnap target, and level in the Ninja game.

**Nightmode**
A scheduled group restriction that automatically restricts sending during configured hours. Managed by `nightmode.py` using PTB JobQueue.

---

## O

**Onboarding**
The `onboarding.py` plugin that guides a new group admin through initial bot setup when the bot is first added to a group. Sends a step-by-step setup guide.

**OWNER_IDS**
Environment variable containing comma-separated Telegram user IDs of bot operators with full unrestricted access. Parsed in `config.py` as a `set[int]`.

---

## P

**Parse Mode**
How Telegram renders text in messages. This bot uses `ParseMode.HTML` globally. HTML tags (`<b>`, `<i>`, `<code>`, `<a>`) must be used. Markdown is not supported.

**PaymentAccount**
DB model in `database/payment_models.py`. Stores a fictional payment method (Alkarimi/Alrajhi/PayPal) and account identifier for the account role-play system. NOT connected to real banking.

**Permission Hierarchy**
Owner > Dragon (sudo) > Demon (support) > Tiger > Wolf > Admin > Regular Member

**Plugin**
Any `.py` file in `Ninja/plugins/` that exports an `async def register(application: Application)` function. The plugin loader discovers and calls this function at startup.

**Plugin Loader**
`core/plugin_loader.py` — scans `plugins/*.py`, imports each file, and calls its `register()` coroutine. Continues on failure. Alphabetical load order.

**PTB**
python-telegram-bot — the async library wrapping the Telegram Bot API. Version 20.7 (async).

---

## Q

**Quiz**
The knowledge game in `quiz/plugin.py`. Questions stored in `quiz/questions.py` grouped by category (general, anime, cars). Uses PTB JobQueue for 30-second timeout. Rewards first correct answer with coins.

---

## R

**Rate Limiter**
`core/rate_limiter.py` — throttles Telegram API calls during bulk operations (federation bans, global bans) to stay within Telegram's 20 msg/s global limit and 1 msg/s per-chat limit.

**Raid**
A coordinated attack where many accounts join a group simultaneously (often bots). Detected by `antiraid.py`. Countered by Shield mode, CAPTCHA, and CAS.

**Repository Pattern**
A software design pattern where database access logic is abstracted into dedicated "repository" functions. Implemented in `db/repositories/`. Preferred over inline SQL in plugins.

**RulerTitle**
DB model in `database/game_models.py`. A title awarded to a player who reaches Castle level 10. Recorded permanently in the Hall of Fame.

---

## S

**Session**
A SQLAlchemy async database session. Always obtained via `async with get_session() as session:`. Each session auto-commits on context exit, auto-rolls back on exception.

**Shield**
The `shield.py` emergency lockdown feature. `/shield on` prevents new members from joining by setting ChatPermissions to disallow new member joins.

**SpamWatch**
An optional third-party global ban database. Requires `SPAMWATCH_TOKEN` secret to be set. **Currently NOT active** because the token is not configured.

**Strict GBAN**
When `STRICT_GBAN=True`, globally banned users are kicked not only on join but also when they send any message. Configured in `config.py`.

**Sudo User**
A "Dragon" tier user. Has elevated privileges across all groups the bot manages. Designated by bot owner via `/addsudo`. Stored in `elevated_users.json`.

---

## T

**Tiger**
Tier 3 in the bot's permission hierarchy (below Demon, above Wolf). Set via `/addtiger` in `disasters.py`. Persisted to `elevated_users.json`.

**`@user_admin`**
A decorator in `core/helpers/chat_status.py` that checks whether the calling user is a Telegram group administrator before allowing the command to proceed. Required on all admin-only commands.

---

## U

**`UserConnection`**
DB model in `connect_models.py`. Stores the link between a user's private chat and a group chat for the `/connect` feature.

**`UserProfile`**
DB model in `database/payment_models.py`. Stores a user's display name and registration status for the fictional payment account system. Distinct from `database/models.py` User model.

---

## V

**Virtual Economy**
The fictional coin-based economic system in this bot. Coins have no real-world value. The system exists purely for entertainment and community engagement. All game coins, economy coins, and wallet coins are the same virtual currency.

---

## W

**Wallet**
DB model in `database/game_models.py`. Table name: `game_wallets`. Stores `user_id`, `coins`, `total_earned`, `last_daily_at`. The universal virtual currency balance across all games and economy features.

**WarnAction**
An enum in `database/models.py`: `NOTHING`, `MUTE`, `KICK`, `BAN`. Defines what happens when a user reaches their warn limit. Configurable per group via `/settings` or `/warnlimit`.

**WarnEntry**
DB model in `database/models.py`. One row per issued warning. Includes `chat_id`, `user_id`, `reason`, `issued_by_id`, `expires_at`. The canonical warn history for a user in a group.

**WarnFilter**
DB model in `database/models_extra.py`. Auto-warn triggers — if a message matches a filter pattern, the user is automatically warned.

**Wolf**
Tier 4 (bottom) in the bot's permission hierarchy. Set via `/addwolf` in `disasters.py`. Has minimal elevated permissions above regular members.

---

## X

**XP**
Experience Points in the Ninja game. Earned by successful kills, lost on failed attacks. Determines a player's level (Student → Trainee → Ninja → Shadow → Master → Legend). Stored in `NinjaProfile.xp`.
