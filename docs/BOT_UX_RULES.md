# BOT_UX_RULES.md — The UX Constitution

## How Should the User Experience Remain Consistent?

This document is the official UX standard for Hozan Bot. Every new feature, every modified command, and every new interaction pattern must conform to these rules. Deviating from these rules requires a documented decision in `ARCHITECTURAL_DECISIONS.md`.

---

## 1. Language Rules

### 1.1 English Is the Only Language
All user-facing text must be in English. Hozan is a global bot — every message, menu label, error, and game prompt must be in English without exception.

- ✅ `"🔒 You are in jail!"`
- ❌ Any non-English text in user-facing output

### 1.2 Command Names Are in English (Telegram Convention)
Telegram commands must use ASCII-only names. The command itself (`/warn`, `/mute`, `/balance`) must be English, as must all labels, buttons, and inline menus.

### 1.3 Numbers Use Standard Western Numerals
- ✅ `"Your balance: 1,500 coins"`
- Do not use Arabic-Indic numerals (٠١٢...) in bot output.

---

## 2. Message Format Rules

### 2.1 HTML Parse Mode Is the Default
The PTB Application is configured with `parse_mode=ParseMode.HTML`. All plugins must use HTML tags (`<b>`, `<i>`, `<code>`, `<a href="...">`) and not Markdown.

- ✅ `"<b>المستخدم:</b> <a href='tg://user?id=123'>اسم</a>"`
- ❌ `"**المستخدم:** [اسم](tg://user?id=123)"`

### 2.2 Separator Lines in Action Messages
Moderation action messages (ban, mute, warn, etc.) use a consistent separator:
```
⚠️ <b>Warning</b>
━━━━━━━━━━━━━━━
👤 <b>User:</b> ...
👮 <b>By:</b> ...
```

### 2.3 Emoji Prefixes for Message Categories
| Category | Prefix |
|----------|--------|
| Success | ✅ |
| Warning / Caution | ⚠️ |
| Error / Failure | ❌ |
| Ban | 🚫 |
| Mute | 🔇 |
| Warn | ⚠️ |
| Info / Status | ℹ️ |
| Game | 🎮 |
| Economy / Money | 💰 |
| Admin action | 👮 |
| Lock / Jail | 🔒 |

---

## 3. Navigation Rules

### 3.1 Every Inline Menu Must Have a Back Button
Any inline keyboard that opens a submenu must include a `🔙 Back` button that returns to the parent menu. Do not leave users stranded in a submenu.

### 3.2 Confirmation for Destructive Actions
Actions that cannot be undone (delete federation, reset all warns, clear all notes) must present an inline confirmation button before executing:
```
Are you sure?
[✅ Yes, confirm]  [❌ Cancel]
```

### 3.3 Message Editing Over New Messages
When a user interaction results in a follow-up response, prefer editing the existing message over sending a new one. Use `query.edit_message_text()` in callback handlers wherever practical. This reduces clutter.

### 3.4 Cancel Buttons in Conversations
Every `ConversationHandler` flow (registration, adding payment method, etc.) must include a `/cancel` command and a visible `❌ Cancel` inline button that ends the conversation cleanly.

---

## 4. Command Rules

### 4.1 Group-Only Commands
Commands that require a group context must check for `update.effective_chat.type == "private"` and respond:
```python
"⚠️ This command only works inside groups."
```

### 4.2 Private-Only Commands
Commands that require a private chat (registration, account management) must check accordingly:
```python
"⚠️ This command only works in private chat."
```

### 4.3 Admin-Only Commands
Commands requiring admin privileges must use the `@user_admin` decorator from `core/helpers/chat_status.py`. Do not write manual admin checks inline.

### 4.4 Usage Hints on Bad Input
When a command is called without required arguments, reply with a usage example using `<code>` tags:
```
⚠️ Usage: <code>/warn @username reason</code>
```

---

## 5. Button Rules

### 5.1 Callback Data Format
Callback data must follow a consistent pattern:
```
prefix_action:chat_id:user_id
```
Example: `"captcha_verify:123456789:987654321"`

### 5.2 Buttons Perform Actions Directly
Inline buttons should execute the action immediately when pressed, not navigate to another text message asking the user to type something. Buttons are actions, not prompts.

### 5.3 Answer Before Editing
All `CallbackQueryHandler` functions must call `await query.answer()` before editing the message, to dismiss the loading indicator:
```python
await query.answer()
await query.edit_message_text(...)
```

### 5.4 Admin Verification in Callbacks
Any callback that triggers a privileged action must re-verify admin status:
```python
member = await chat.get_member(user.id)
if member.status not in ("creator", "administrator"):
    await query.answer("Admins only.", show_alert=True)
    return
```

---

## 6. Settings Panel Rules

### 6.1 One Entry Point Per Setting
If a setting is configurable via the `/settings` inline panel, it should NOT also be configurable via a separate standalone command. Duplicate entry points create confusion about which is authoritative.

*Current violation:* `/warnlimit` and `/strongwarn` commands coexist with the settings panel. These standalone commands should be deprecated in favour of the panel.

### 6.2 Settings Show Current Value
Every settings menu must display the current value of each toggle before the user changes it:
```
🔔 CAPTCHA: ✅ Enabled
Action: Delete + Warn
Type: Button
```

---

## 7. Feature Placement Rules

### 7.1 Every Feature Has One Home
Features must not appear in multiple menus or plugin categories. Decide where it belongs and put it there:
- Protection settings → `/settings` panel
- Economy actions → economy commands
- Game actions → game-specific commands
- Moderation → admin commands

### 7.2 Games Are Group-Only
All game commands (ninja, castle, farm, quiz) must refuse to operate in private chats. The social/competitive nature of the games requires a group context.

### 7.3 Owner Commands Are Not in Help
Commands restricted to `OWNER_IDS` (eval, dev_cmds, broadcast, leave) must not appear in the public `/help` menu.

---

## 8. Error Handling Rules

### 8.1 Never Silently Fail on User-Facing Actions
If a moderation action fails (BadRequest, Forbidden), inform the admin:
```python
await message.reply_text(f"⚠️ Mute failed: <code>{exc.message}</code>")
```

### 8.2 Log Errors to Console
All exceptions in handlers must be logged:
```python
logger.warning("Action failed for user %d: %s", user_id, exc)
```

### 8.3 Graceful Degradation for External APIs
Plugins that call external APIs (chatbot API, phishing APIs, weather, currency) must handle connection failures gracefully and inform the user when the service is unavailable, rather than raising an unhandled exception.

---

## 9. Naming Conventions

### 9.1 Plugin Files
- Snake_case filenames: `anti_duplicate.py`, `farm_game.py`
- Protection plugins prefixed with their category: `anti_*`, `captcha`, `bayes_*`

### 9.2 Handler Functions
- Command handlers: `cmd_<command_name>` or `<verb>` (e.g., `ban`, `warn`, `cmd_wallet`)
- Callback handlers: `<feature>_callback` or `cb_<name>`
- Message handlers: `<action>_handler` or descriptive verb

### 9.3 Callback Data Prefixes
Each plugin must use a unique prefix to avoid callback data collisions between plugins.

---

## 10. Future Integration Rules

### 10.1 New Games Must Use `core/game_wallet.py`
Any new game that awards or deducts coins must use `add_coins()` and `deduct_coins()` from `core/game_wallet.py`. Coins must never be modified directly on the `Wallet` model outside of this module.

### 10.2 New Protection Features Must Integrate with `ChatFeatureSettings`
Any new protection toggle must be added as a boolean column to `ChatFeatureSettings`, not as a separate in-memory set or a new standalone table. This ensures per-group configurability.

### 10.3 New Features Must Be Listed in `FEATURES.md`
Before a feature is released, its entry in `FEATURES.md` must be written or updated.
