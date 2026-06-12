# FEATURES.md

## What Capabilities Currently Exist?

105 plugins are loaded successfully at runtime. This document catalogues every feature by category.

---

## CATEGORY 1 — New Member Protection

### CAPTCHA Verification (`captcha.py`)
- **Purpose:** Verify new members are human before granting send permissions.
- **Access:** Auto-triggered on join. Commands: `/captcha on|off`, `/setcaptcha button|math|text`, `/captchatime <seconds>`
- **Types:** Button (press to verify), Math (arithmetic), Text (type exact word)
- **Dependencies:** `CaptchaPending` table, `ChatFeatureSettings.captcha_enabled`, PTB JobQueue
- **User Value:** Stops bot waves and auto-join raids without manual admin intervention.

### Adaptive CAPTCHA (`adaptive_captcha.py`)
- **Purpose:** Enhanced CAPTCHA that adapts difficulty based on group threat level.
- **Access:** Admin toggle per group.
- **Dependencies:** `ChatFeatureSettings`
- **User Value:** Harder to bypass than static CAPTCHA during active raids.

### CAS Check (`cas_check.py`)
- **Purpose:** Auto-ban users listed in the Combot Anti-Spam global database on join.
- **Access:** `ChatFeatureSettings.cas_enabled` (default: on). Command: `/casban on|off`
- **Dependencies:** External CAS API (`api.cas.chat`)
- **User Value:** Blocks known global spammers before they send a single message.

### Account Age Check (`account_age.py`)
- **Purpose:** Kick or restrict new members whose Telegram account was created recently (potential spam bots).
- **Access:** Admin command per group.
- **User Value:** Reduces bot-wave effectiveness since fresh accounts are commonly used in raids.

### Anti-Raid (`antiraid.py`)
- **Purpose:** Detects mass-join events and automatically enables lockdown mode.
- **Access:** Toggle per group.
- **User Value:** Automatic response to coordinated raid attacks.

### Shield (`shield.py`)
- **Purpose:** Temporarily locks the group to member-only — prevents new joins.
- **Access:** Commands: `/shield on|off`, `/unshield`
- **User Value:** Manually triggered emergency lockdown during active attacks.

---

## CATEGORY 2 — Spam & Content Filtering

### Bayesian AI Spam Filter (`bayes_filter.py`)
- **Purpose:** Machine-learning spam classifier trained per group on real messages.
- **Access:** Toggle per group. Commands: `/bayes on|off`, `/train` (reply to message to label it)
- **Dependencies:** `BayesianToken` table, `core/spam_bayes.py`, `ChatFeatureSettings.bayes_filter_enabled`
- **User Value:** Learns the specific spam patterns in a community. Improves over time.

### Regex/Word-List Spam Filter (`antispam_panel.py`)
- **Purpose:** Admin-defined word/regex patterns that trigger automatic action.
- **Access:** Settings panel or commands.
- **Dependencies:** `SpamPatternEntry` table, `ChatFeatureSettings.regex_filter_enabled`
- **User Value:** Instant reaction to known spam phrases without waiting for Bayes training.

### Blacklist (`blacklist.py`)
- **Purpose:** Per-group keyword blacklist with automatic action (delete/warn/ban/mute/kick).
- **Access:** Commands: `/addblacklist`, `/unblacklist`, `/blacklist`
- **User Value:** Prevents specific words or phrases from being sent in the group.

### Blacklist Stickers (`blacklist_stickers.py`)
- **Purpose:** Blocks specific sticker file IDs from being sent in the group.
- **Access:** Commands: `/addblsticker` (reply to sticker), `/unblsticker`, `/blstickerlist`
- **User Value:** Prevents offensive or spammy sticker packs.

### Anti-Flood (`antiflood.py`)
- **Purpose:** Rate-limits message sending per user. Kicks/mutes/bans repeat offenders.
- **Access:** Toggle per group. Commands: `/setflood <n>`, `/flood`
- **User Value:** Stops message flooding that disrupts conversation.

### Anti-Links (`antilinks.py`)
- **Purpose:** Deletes messages containing URLs or Telegram invite links.
- **Access:** Toggle per group. Whitelist exemptions supported.
- **User Value:** Prevents advertising and phishing links from non-admins.

### Anti-Forward (`anti_forward.py`)
- **Purpose:** Deletes forwarded messages from specific chats or all chats.
- **Access:** Toggle per group.
- **User Value:** Stops spam campaigns that forward from known spam channels.

### Anti-Duplicate (`anti_duplicate.py`)
- **Purpose:** Detects and deletes repeated/duplicate messages from the same user.
- **Access:** Toggle per group. Configurable similarity threshold.
- **Dependencies:** `AntiDuplicateSettings` table
- **User Value:** Stops copy-paste spam.

### Anti-NSFW (`anti_nsfw.py`)
- **Purpose:** Detects and removes NSFW content (images/videos).
- **Access:** Toggle per group.
- **User Value:** Keeps group content appropriate.

### Anti-Nuke (`anti_nuke.py`)
- **Purpose:** Detects and responds to admin account takeover/nuke attempts.
- **Access:** Auto-monitoring.
- **User Value:** Protects against compromised admin accounts being used to mass-ban members.

### Phishing Detection (`phishing.py`)
- **Purpose:** Scans all URLs against known phishing/scam domain patterns.
- **Access:** Enabled by default. Commands: `/phishing on|off`, `/phishing action <delete|warn|ban>`
- **Dependencies:** `PhishingSettings` table (pure Python regex, no external API)
- **User Value:** Blocks crypto scams, Telegram impersonation, and fake prize links.

### Homoglyph Detection (`homoglyph.py`)
- **Purpose:** Detects messages using mixed Cyrillic/Latin lookalike characters to evade filters.
- **Access:** Toggle per group.
- **Dependencies:** `HomoglyphSettings` table
- **User Value:** Closes a common spam evasion vector.

### Spacing Check (`spacing_check.py`)
- **Purpose:** Detects abnormal spacing used to evade word-based filters.
- **Access:** Toggle per group.
- **Dependencies:** `SpacingCheckSettings` table
- **User Value:** Closes another common spam evasion vector.

### Reaction Spam (`reaction_spam.py`)
- **Purpose:** Detects and acts on users who spam emoji reactions.
- **Access:** Toggle per group.
- **Dependencies:** `ReactionSpamSettings` table
- **User Value:** Prevents reaction-based notification spam.

### Language Filter (`lang_filter.py`)
- **Purpose:** Restricts messages to specific languages (e.g., Arabic-only groups).
- **Access:** Admin commands per group.
- **User Value:** Keeps multilingual groups focused on the intended language.

### Anti-Astro (`antiastro.py`)
- **Purpose:** Blocks astrology/horoscope spam patterns common in certain communities.
- **Access:** Toggle per group.
- **User Value:** Stops a specific category of repetitive content spam.

### SpamWatch (`spamwatch.py`)
- **Purpose:** Checks joining users against the SpamWatch global ban database.
- **Access:** Requires `SPAMWATCH_TOKEN` secret to be set (currently NOT SET — feature is inactive).
- **Dependencies:** External SpamWatch API
- **User Value:** Additional global spam database coverage beyond CAS.

### Name Ban (`name_ban.py`)
- **Purpose:** Bans users whose display name or username matches configured patterns.
- **Access:** Commands: `/addnameban <pattern>`, `/removenameban`, `/namebans`
- **Dependencies:** `NameBanPattern` table
- **User Value:** Auto-bans accounts with known spam/scam naming conventions.

### Global Ignore (`global_ignore.py`)
- **Purpose:** Bot-owner-level list of users the bot entirely ignores.
- **Access:** Owner commands.
- **User Value:** Permanent per-user blacklist at the bot infrastructure level.

---

## CATEGORY 3 — Moderation Tools

### Bans (`bans.py`)
- **Commands:** `/ban`, `/tban` (temp ban), `/kick`, `/kickme`, `/unban`
- **Dependencies:** `BanRecord` table, `db/repositories/bans.py`
- **User Value:** Core moderation actions with full audit trail.

### Muting (`muting.py`)
- **Commands:** `/mute`, `/tmute` (temp mute), `/unmute`
- **User Value:** Restrict a member's ability to send messages without removing them.

### Warnings (`warns.py`)
- **Commands:** `/warn`, `/warns`, `/resetwarn`, `/warnlimit`, `/addwarn`, `/nowarn`
- **Dependencies:** `WarnEntry` table, `db/repositories/warns.py`, `WarnFilter` table
- **User Value:** Progressive punishment system with configurable threshold and automatic action.

### Admin Tools (`admin.py`)
- **Commands:** `/promote`, `/demote`, `/pin`, `/unpin`, `/title`, `/tpromote`
- **User Value:** Bot-assisted admin management without using Telegram's native UI.

### Locks (`locks.py`)
- **Purpose:** Restricts specific message types (stickers, GIFs, polls, forwards, games, etc.)
- **Commands:** `/lock <type>`, `/unlock <type>`, `/locks`
- **Dependencies:** `LockSettings` table
- **User Value:** Fine-grained content control beyond Telegram's native group settings.

### Purge (`purge.py`)
- **Commands:** `/purge` (delete from replied message to current), `/del` (delete replied message)
- **User Value:** Bulk message deletion for spam cleanup.

### Approve (`approve.py`)
- **Purpose:** Whitelist specific users to bypass antiflood and other filters.
- **Commands:** `/approve`, `/unapprove`, `/approved`
- **User Value:** Trusted member bypass without granting full admin rights.

### Permit (`permit.py`)
- **Purpose:** Temporarily allows a user to bypass automated filters for N messages or N minutes.
- **Commands:** `/permit @user [count|time]`, `/unpermit`
- **Dependencies:** `PermittedUser` table
- **User Value:** Short-term bypass for legitimate activity without permanent approval.

### Report (`report.py`)
- **Commands:** `/report` (reply to message) — sends alert to admins.
- **User Value:** Allows regular members to flag problematic content for admin review.

### Report Vote (`report_vote.py`)
- **Purpose:** Democratic reporting — N member reports trigger admin notification.
- **Access:** Admin configurable threshold.
- **User Value:** Community moderation layer without requiring individual admin attention.

### Vote Mute (`vote_mute.py`)
- **Commands:** `/votemute @user` — group vote to mute a member.
- **User Value:** Community-driven moderation for borderline cases.

### Slowmode (`slowmode.py`)
- **Commands:** `/slowmode <seconds>`, `/slowmode off`
- **User Value:** Enforces minimum time between messages from each user.

### Purge/Clean Service (`cleanservice.py`)
- **Commands:** `/cleanservice` — auto-delete Telegram system messages (join, leave, pin notifications).
- **User Value:** Keeps chat clean from service message clutter.

### Clean Blue (`clean_blue.py`)
- **Commands:** `/cleanblue on|off` — delete failed command attempts (messages starting with `/` that don't match any command).
- **⚠️ State lost on restart** (in-memory only — see LIVING_PROJECT_LOG.md)
- **User Value:** Removes clutter from users trying random commands.

---

## CATEGORY 4 — Federation & Global Actions

### Federation (`federation.py`)
- **Purpose:** Shared ban list across multiple groups.
- **Commands:** `/newfed`, `/joinfed`, `/leavefed`, `/fban`, `/funban`, `/fpromote`, `/fdemote`, `/fbanlist`, `/fedinfo`, `/myfeds`, `/chatfed`
- **Dependencies:** `Federation`, `FedAdmin`, `FedBan`, `ChatFed` tables
- **User Value:** One ban command propagates to all groups in the federation instantly.

### Global Bans (`global_bans.py`)
- **Purpose:** Bot-level ban across all groups the bot manages.
- **Commands:** `/gban`, `/ungban`, `/gbanlist` (sudo only); `/gbanstat` (admins)
- **Dependencies:** `GlobalBannedUser` table (in models_extra)
- **User Value:** Removes known bad actors from every managed group simultaneously.

### Global Ignore (`global_ignore.py`)
- **Purpose:** Silently ignore a user across all bot interactions.
- **Access:** Owner commands.

---

## CATEGORY 5 — Group Management & Configuration

### Settings Panel (`settings_panel.py`)
- **Purpose:** Inline keyboard UI to configure all major protection features.
- **Commands:** `/settings`
- **User Value:** One-stop configuration panel instead of memorizing individual commands.

### Log Channel (`log_channel.py`)
- **Purpose:** Forward audit logs of moderation actions to a designated channel.
- **Commands:** `/setlog <channel>`, `/unsetlog`
- **Dependencies:** `@loggable` decorator in `core/log_channel.py`
- **User Value:** Permanent audit trail of all moderation actions.

### Welcome (`welcome.py`)
- **Commands:** `/setwelcome`, `/welcome`, `/resetwelcome`, `/setgoodbye`, `/goodbye`, `/cleanwelcome`, `/welcomehelp`
- **Dependencies:** `WelcomeSettings`, `WelcomeButton` tables
- **User Value:** Custom welcome/goodbye messages with template variables and inline buttons.

### Rules (`rules.py`)
- **Commands:** `/setrules`, `/rules`, `/resetrules`
- **User Value:** Store and display group rules accessible to all members.

### Notes (`notes.py`)
- **Commands:** `/save <key> <text>`, `/get <key>` (or `#key`), `/notes`, `/clear <key>`
- **User Value:** Save and retrieve rich content (text, media, buttons) by keyword.

### Filters (`filters.py`)
- **Commands:** `/filter <key> <reply>`, `/stop <key>`, `/filters`
- **User Value:** Auto-reply when a keyword is detected in any message (not command-triggered).

### Night Mode (`nightmode.py`)
- **Commands:** `/nightmode <HH:MM> <HH:MM>` — schedule group lock/unlock
- **Dependencies:** PTB JobQueue
- **User Value:** Automatic group restriction during off-hours.

### Scheduler (`scheduler.py`)
- **Commands:** Admin-configurable message scheduler.
- **User Value:** Schedule recurring announcements.

### RSS (`rss.py`)
- **Commands:** `/addrss <url>`, `/delrss <url>`, `/rsslist`
- **Dependencies:** `feedparser`, PTB JobQueue
- **User Value:** Automatic posting of RSS feed updates to the group.

### Backup (`backup.py`)
- **Commands:** `/backup` — export group settings as JSON
- **User Value:** Disaster recovery if bot data is lost.

### Broadcast (`broadcast.py`)
- **Commands:** `/broadcast <message>` (owner only) — send to all groups
- **User Value:** Mass announcements from bot owner.

### Group Tools (`group_tools.py`)
- **Commands:** `/invite`, `/zombies`, `/kickme`, `/setdesc`, `/setphoto`, etc.
- **User Value:** Administrative utility commands.

### Connect (`connect.py`)
- **Commands:** `/connect <chat_id>`, `/disconnect`, `/connected`
- **Purpose:** Admin manages group from private chat
- **Dependencies:** `UserConnection` table
- **User Value:** Admin convenience — issue group commands from DM.

### Channel Protect (`channel_protect.py`)
- **Purpose:** Prevent channel accounts from sending messages to the group.
- **Access:** Toggle per group.
- **User Value:** Stops channel-identity spam.

### Channel Sub (`channel_sub.py`)
- **Purpose:** Require users to subscribe to a specific channel before participating.
- **Access:** Admin configurable.
- **User Value:** Channel growth tactic — require subscription before chatting.

### Disable (`disable.py`)
- **Commands:** `/disable <cmd>`, `/enable <cmd>`, `/cmds`, `/listcmds`
- **User Value:** Prevent regular members from using specific bot commands.

---

## CATEGORY 6 — Games

### Ninja Game (`ninja_game.py`)
- **Purpose:** Assassination and kidnapping game with XP progression system.
- **Commands:** `/kill @user`, `/kidnap @user`, `/rescue @user`, `/ransom`, `/myprofile`, `/ninjarank`, `/ninjatop`
- **Levels:** Student → Trainee → Ninja → Shadow → Master → Legend
- **Dependencies:** `NinjaProfile`, `KidnapRecord` tables, `Wallet` for ransom coins
- **User Value:** Competitive engagement with long-term progression.

### Castle Kingdom (`castle_game.py`)
- **Purpose:** Strategy game — build and upgrade castles, raise armies, fight battles.
- **Commands:** `/create_castle`, `/my_castle`, `/upgrade_castle`, `/resource_shop`, `/buy_resource`, `/create_barracks`, `/buy_army`, `/duel`, `/start_battle`, `/join_battle`, `/alliance`, `/dig`, `/immunity`, `/top_rulers`, `/exchange_gold`
- **Dependencies:** `Castle`, `CastleResources`, `Barracks`, `ImmunityCard`, `TreasureHunt`, `AllianceRequest`, `RulerTitle` tables
- **User Value:** Deep strategy game with community battles and leaderboards.

### Farm Game (`farm_game.py`)
- **Purpose:** Agricultural simulation — plant crops, wait for growth, harvest, sell for coins.
- **Commands:** `/create_farm`, `/my_farm`, `/farm_shop`, `/plant`, `/plant_all`, `/harvest`, `/my_harvest`, `/sell`, `/sell_all`, `/upgrade_farm`
- **Crops:** Wheat (30m), Barley (45m), Tomato (90m), Apple (3h), Grape (6h)
- **Dependencies:** `Farm`, `FarmPlot`, `FarmInventory` tables, `Wallet`
- **User Value:** Idle-game mechanics with real waiting periods.

### Quiz (`quiz.py` → `quiz/plugin.py`)
- **Purpose:** Knowledge quiz game. First correct answer wins coins.
- **Commands:** `/quiz` (general), `/animequiz`, `/carquiz`, `/endquiz`
- **Categories:** General knowledge, anime, cars
- **Dependencies:** `quiz/questions.py`, `Wallet`
- **User Value:** Educational entertainment that rewards fastest correct answer.

### Couples (`couples.py`)
- **Commands:** `/couple` — randomly selects "Couple of the Day" from group members
- **User Value:** Daily social game that generates conversation.

### Truth or Dare (`truth_dare.py`)
- **Commands:** `/truth`, `/dare`
- **User Value:** Classic social game for group entertainment.

---

## CATEGORY 7 — Virtual Economy

### Wallet (`wallet.py` + `core/game_wallet.py`)
- **Commands:** `/wallet`, `/daily`
- **Purpose:** Universal coin balance shared across all games.
- **User Value:** Single balance visible across all game activities.

### Economy System (`economy/plugin.py`)
- **Commands:** `/richlist`, `/openbank`, `/closebank`, `/mybank`, `/transfer`, `/balance`, `/checkbal`, `/salary`, `/bonus`, `/steal`, `/thieftop`, `/invest`, `/luck`, `/trade`, `/top`
- **Dependencies:** `BankAccount`, `EconomyStats`, `Wallet` tables
- **User Value:** Full virtual banking system with theft, investment, and competition.

### Loans & Jail (`economy/loans.py`)
- **Commands:** `/loan`, `/repay`, `/myloan`, `/debtors`, `/bail`, `/bailout`, `/myjail`
- **User Value:** Borrow coins with interest. Overdue loans result in in-game jail.

### Heist (`economy/heist.py`)
- **Commands:** `/rob`, `/joinrob`
- **Purpose:** Cooperative group heist — succeed to earn coins, fail and go to jail.
- **User Value:** Social, high-stakes group activity.

### Fictional Payment Accounts (`account.py`)
- **Commands:** `/register`, `/my_account`, `/add_payment`, `/remove_payment`, `/set_primary`
- **Purpose:** Fun role-play system simulating bank accounts (Alkarimi, Alrajhi, PayPal) — NOT real money.
- **Dependencies:** `UserProfile`, `PaymentAccount` tables
- **User Value:** Adds immersive role-play element to the virtual economy.

---

## CATEGORY 8 — User Tools & Information

### AFK (`afk.py`)
- **Commands:** `/afk [reason]`
- **Purpose:** Mark yourself as away. Bot notifies when mentioned.
- **User Value:** Social awareness feature.

### Bio (`bio.py`)
- **Commands:** `/setbio`, `/bio @user`
- **User Value:** Personal profile text within the group.

### User Info (`userinfo.py`)
- **Commands:** `/info @user`
- **User Value:** Displays user profile, ID, join date, warn count.

### Account Age (`account_age.py`)
- **Commands:** `/accountage @user`
- **User Value:** Shows how old a Telegram account is.

### Top Users (`topusers.py`)
- **Commands:** `/topusers`
- **User Value:** Leaderboard of most active members.

### User Tracking (`users_tracking.py`)
- **Purpose:** Tracks user activity statistics per group.
- **User Value:** Powers `/topusers` and admin analytics.

### Stats (`stats.py`)
- **Commands:** `/stats` (owner only) — global bot statistics
- **User Value:** Bot operator monitoring.

### Timezone (`timezone_cmd.py`)
- **Commands:** `/timezone <city>`
- **User Value:** Display current time in any city.

### Time (`time_cmd.py`)
- **Commands:** `/time [zone]`
- **User Value:** Quick time lookup.

---

## CATEGORY 9 — Utility Commands

### Translator (`translator.py`)
- **Commands:** `/tr [lang]` (reply to message)
- **Dependencies:** `deep-translator` library
- **User Value:** Translate any message to any language.

### Currency (`currency.py`)
- **Commands:** `/cash <amount> <from> <to>`
- **Dependencies:** `exchangerate.host` API
- **User Value:** Real-time currency conversion.

### Country Info (`country_info.py`)
- **Commands:** `/country <name>`
- **Dependencies:** `countryinfo` library
- **User Value:** Country facts, capital, population, flag.

### Wiki (`wiki.py`)
- **Commands:** `/wiki <query>`
- **User Value:** Quick Wikipedia lookup.

### Urban Dictionary (`ud.py`)
- **Commands:** `/ud <word>`
- **User Value:** Slang definitions.

### Wallpaper (`wallpaper.py`)
- **Commands:** `/wallpaper <query>`
- **User Value:** Search and send wallpapers.

### Math (`math_cmd.py`)
- **Commands:** `/math <expression>`
- **User Value:** Evaluate mathematical expressions.

### JSON (`json_cmd.py`)
- **Commands:** `/json` (reply to message) — show raw message JSON
- **User Value:** Developer/admin diagnostic tool.

### Ping (`ping_cmd.py`)
- **Commands:** `/ping`
- **User Value:** Check bot response time.

### Speed Test (`speed_test.py`)
- **Commands:** `/speedtest`
- **User Value:** Test server internet speed.

### Health (`health.py`)
- **Commands:** `/health`
- **User Value:** Bot health check (uptime, DB status).

### Alive (`alive.py`)
- **Commands:** `/alive`, `/uptime`
- **User Value:** Confirm bot is running.

### Source (`source_cmd.py`)
- **Commands:** `/source`
- **User Value:** Link to bot's source code.

### Sticker Tools (`sticker_tools.py`)
- **Commands:** `/stickerid`, `/getsticker`
- **User Value:** Get file IDs and download stickers.

### Fonts (`fonts.py`)
- **Commands:** `/font <text>`
- **User Value:** Convert text to Unicode font styles.

### Sed (`sed.py`)
- **Commands:** `s/find/replace` (regex substitution in reply)
- **User Value:** IRC-style message correction.

### Onboarding (`onboarding.py`)
- **Purpose:** Guides new group admins through bot setup.
- **Access:** Auto-triggered when bot is added to a group.

### Check Perms (`checkperms.py`)
- **Commands:** `/checkperms`
- **User Value:** Shows bot's current permission level in the group.

---

## CATEGORY 10 — Fun & Social

### Fun (`fun.py`)
- **Commands:** `/runs`, `/slap`, `/pat`, `/roll`, `/toss`, `/shrug`, `/decide`, `/8ball`, `/table`, `/rlg`, `/shout`, `/react`
- **Dependencies:** `plugins/fun_strings.py` (data file)
- **User Value:** Social interaction commands for entertainment.

### Tag All (`tagall.py`)
- **Commands:** `/tagall [message]`, `/tagadmins`
- **User Value:** Mention all group members or all admins.

### Chatbot (`chatbot.py`)
- **Commands:** `/chatbot` — toggle AI chat responses
- **Auto-responds:** To bot mentions and replies
- **Dependencies:** FallenRobot external API
- **⚠️ State lost on restart** (in-memory only — see LIVING_PROJECT_LOG.md)
- **User Value:** Conversational AI within the group.

### Set Language (`setlang.py`)
- **Commands:** `/setlang`
- **User Value:** Change bot interface language per group.

### Help Menu (`help_menu.py`)
- **Commands:** `/help`, `/start` (in group)
- **User Value:** Discover available commands.

### Fuzzy Commands (`fuzzy_commands.py`)
- **Purpose:** Suggests the correct command name when a user types a close approximation (Levenshtein distance matching).
- **User Value:** Reduces friction for users unfamiliar with exact command names.

---

## CATEGORY 11 — Owner / Developer Tools

### Dev Commands (`dev_cmds.py`)
- **Commands:** `/leave <chat_id>`, `/logs`, `/debug on|off`
- **Access:** `OWNER_IDS` only

### Eval (`eval_cmd.py`)
- **Commands:** `/eval`, `/exec`, `/sh`, `/clearlocals`
- **Access:** `OWNER_IDS` only — executes arbitrary Python/shell code

### Disasters (`disasters.py`)
- **Commands:** `/addsudo`, `/removesudo`, `/addsupport`, etc. — manages bot permission hierarchy
- **Commands (public):** `/dragons`, `/demons`, `/tigers`, `/wolves`, `/disasters`
- **⚠️ Uses JSON file, not database** — see LIVING_PROJECT_LOG.md

### Broadcast (`broadcast.py`)
- **Commands:** `/broadcast <message>`
- **Access:** `OWNER_IDS` only

### Misc (`misc.py`)
- **Commands:** Various small utility commands
