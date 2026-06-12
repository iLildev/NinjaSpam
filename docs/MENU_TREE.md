# MENU_TREE.md

## How Does a User Move Through the System?

This document maps every navigable path in the bot. Commands are organized by entry point. Inline menus show their navigation structure.

---

## Entry Points

### `/start` (Private Chat)
```
/start
  ├─ New user → Registration flow (account.py)
  │     ├─ Choose payment method [💳 الكريمي | 🏦 الراجحي | 🌐 PayPal | ❌ إلغاء]
  │     │     └─ Enter account identifier (text input)
  │     │           └─ Confirmation → Registration complete
  │     └─ /cancel → EXIT
  └─ Registered user → Summary of registered accounts + command list
```

### `/start` (Group Chat)
```
/start → Redirects user to private chat or shows basic info
```

### `/help`
```
/help
  └─ Inline keyboard: Category buttons
        ├─ [🛡 الحماية]    → Protection commands list
        ├─ [⚠️ الإدارة]    → Moderation commands list
        ├─ [🎮 الألعاب]    → Games commands list
        ├─ [💰 الاقتصاد]   → Economy commands list
        ├─ [📋 الأدوات]    → Utility commands list
        └─ [🔙 رجوع]       → Back to main help
```

---

## Protection & Settings Navigation

### `/settings` (Group Admin Only)
```
/settings
  └─ Main Settings Panel (inline keyboard)
        ├─ [🛡 الحماية]
        │     ├─ [كابتشا: ✅/❌]        → Toggle CAPTCHA on/off
        │     │     ├─ [نوع: زر/رياضيات/نص] → Change CAPTCHA type
        │     │     ├─ [المهلة: 120ث]   → Set timeout
        │     │     └─ [🔙 رجوع]
        │     ├─ [بايز: ✅/❌]           → Toggle Bayes filter
        │     │     ├─ [الإجراء]         → Set spam action
        │     │     └─ [🔙 رجوع]
        │     ├─ [فلتر الروابط: ✅/❌]   → Toggle anti-links
        │     ├─ [CAS: ✅/❌]            → Toggle CAS check
        │     └─ [🔙 رجوع]
        ├─ [⚠️ التحذيرات]
        │     ├─ [الحد: 3]               → Set warn limit
        │     ├─ [الإجراء: حظر]          → Set warn action (nothing/mute/kick/ban)
        │     ├─ [المدة: دائم]           → Set warn action duration
        │     └─ [🔙 رجوع]
        └─ [🔙 إغلاق]
```

### CAPTCHA Flow (Auto-triggered on join)
```
New member joins
  └─ CAPTCHA enabled?
        ├─ NO → No action
        └─ YES → Send challenge message
              ├─ Button type → [✅ I am not a robot] button
              │     ├─ Correct press → Unmute + delete challenge + confirm ✅
              │     └─ Timeout     → Kick (if configured) + delete challenge
              ├─ Math type → "What is 4 + 7?" text
              │     ├─ Correct answer → Unmute + delete challenge + confirm ✅
              │     └─ Timeout        → Kick + delete challenge
              └─ Text type → "Type the word CONFIRM exactly"
                    ├─ Correct answer → Unmute + delete challenge + confirm ✅
                    └─ Timeout        → Kick + delete challenge
```

---

## Moderation Command Paths

### Ban Flow
```
/ban @user reason
  ├─ User is admin → "لا يمكن حظر المشرفين"
  ├─ Bot lacks permission → Error message
  └─ Success → Ban + audit log to log channel
               [Inline button: ❌ رفع الحظر] (in some contexts)

/tban @user 2h reason → Temporary ban (auto-expires)
/kick @user → Ban + immediate unban (removes from group)
/unban @user → Remove ban
```

### Warn Flow
```
/warn @user
  ├─ No reason → If warn reasons list exists:
  │     [Reason 1] [Reason 2] [✏️ سبب مخصص] → Select reason
  │     └─ Selected → Execute warn
  └─ With reason → Execute warn directly
        ├─ count < limit → Warning message with progress bar [▓▓░░░░░░] 2/3
        │                   [❌ إلغاء تحذير] button
        └─ count >= limit → Execute WarnAction (ban/kick/mute)
                            Log to audit channel

/warns @user → Show warn count + progress bar + reason history
/resetwarn @user → Clear all warns → Confirmation + reset bar [░░░░░░░░] 0/3
rmwarn callback → Remove one warn (from ❌ إلغاء تحذير button)
```

### Mute Flow
```
/mute @user reason   → Permanent mute (send_messages=False)
/tmute @user 2h      → Temporary mute (expires after duration)
/unmute @user        → Restore full permissions
```

---

## Federation Navigation

### Federation Owner (Private Chat)
```
/newfed <name>
  └─ Returns: ✅ Federation created! ID: <uuid>

/delfed <fed_id>
  └─ Returns: ✅ Federation deleted

/fpromote @user → Grant federation admin rights
/fdemote @user  → Revoke federation admin rights
/myfeds         → List federations owned
```

### Group Admin (Group Chat)
```
/joinfed <fed_id>
  └─ Applies all existing federation bans to this chat
/leavefed → Leave current federation
/chatfed  → Show which federation this chat is in

/fban @user reason → Ban from ALL federation chats (propagated)
/funban @user      → Lift federation ban
/fbanlist          → Download .txt ban list
```

---

## Game Navigation

### Ninja Game
```
/myprofile     → Show XP, level, stats, kidnap status
/kill @user    → Attack attempt (30% success at Student level)
  └─ Success → Victim loses health, attacker gains XP
  └─ Failure → Attacker loses XP
/kidnap @user  → Hold hostage for ransom
  └─ Active kidnap → Victim sees kidnap message
        ├─ /ransom → Pay ransom (from Wallet)
        └─ /rescue → Other player attempts rescue
/ninjarank     → Full XP leaderboard
/ninjatop      → Top kills leaderboard
```

### Castle Game
```
/create_castle → Name castle + initialize resources
/my_castle     → View castle level, resources, army
/resource_shop → [قمح] [حجر] [خشب] [طعام] → Buy with Wallet coins
/upgrade_castle → Spend resources to level up (up to level 10)
/create_barracks → Build military base
/buy_army <count> → Purchase soldiers with coins
/upgrade_army    → Increase army power
/dig             → Treasure hunt (cooldown-based coin reward)
/immunity        → View/use immunity cards (from dig)
/duel @user      → 1v1 combat based on army strength
/start_battle    → Open group battle (60s recruitment)
/join_battle     → Join ongoing battle
/end_battle      → Manually end battle + crown winner
/top_rulers      → Hall of fame: users who reached castle level 10
/alliance @user  → Send alliance request
  └─ Target receives: [✅ قبول] [❌ رفض] buttons
/alliance_requests → View incoming alliance requests
/exchange_gold <amount> → Convert Castle gold to Wallet coins
```

### Farm Game
```
/create_farm   → Initialize farm (3 plots at level 1)
/farm_shop     → View crops table (name, cost, grow time, sell price)
/plant <crop> <plot#> → Plant seed in specific plot
/plant_all <crop>     → Fill all empty plots with same crop
/my_farm       → View all plots with status:
  ├─ [1] 🟫 فارغة
  ├─ [2] 🌾 قمح — ⏳ 28د
  └─ [3] 🍅 طماطم — ✅ جاهز للحصاد
/harvest       → Collect all ready crops to inventory
/my_harvest    → View inventory (wheat: 5, tomato: 2, etc.)
/sell <crop> <qty> → Convert harvest to Wallet coins
/sell_all      → Sell entire inventory
/upgrade_farm  → Pay coins to add more plots (max level 5, 16 plots)
```

### Economy Flow
```
/openbank      → Create bank account (generates account number)
/mybank        → Show account number + balance
/balance       → Show Wallet coin balance
/salary        → Claim salary (daily cooldown)
/steal @user   → Attempt to steal coins (risk/reward)
/invest <amount> → Gambling investment (-90% to +90% return)
/luck          → Random small reward
/trade @user <amount> → Propose coin trade
/richlist      → Top 5 richest users
/thieftop      → Top 5 thieves
/top           → Combined leaderboard

/loan <amount> → Borrow up to 3000 coins (requires bank account)
/repay <amount|all> → Repay loan
/myloan        → View loan status, interest, due date
/debtors       → List overdue loan holders
/myjail        → Check jail status
/bail          → Pay bail to leave jail
/bailout @user → Pay someone else's bail

/rob           → Initiate group bank heist (60s recruitment)
/joinrob       → Join active heist
  └─ After 60s: 65% success (each gets 300-700 coins)
              35% failure (all participants jailed 1 hour)
```

### Quiz Flow
```
/quiz      → Random question from general pool (30s timer)
/animequiz → Random question from anime pool
/carquiz   → Random question from cars pool
  └─ Question displayed with clue + timer
        ├─ First correct text answer → Winner gets 50 coins
        └─ Timeout → Show correct answer + hint
/endquiz   → Admin force-ends active question
```

---

## Account/Payment Navigation (Private Chat)

```
/my_account
  └─ Show registered fictional accounts
        ├─ [➕ إضافة محفظة]    → add_payment flow
        ├─ [🗑 حذف محفظة]     → remove_payment flow  
        └─ [⭐ تغيير الافتراضي] → set_primary flow

/add_payment (Private only)
  └─ [💳 الكريمي] [🏦 الراجحي] [🌐 PayPal] [❌ إلغاء]
        └─ Enter account name → Saved

/remove_payment
  └─ [🗑 الكريمي — اسم_الحساب]
     [🗑 الراجحي — اسم_الحساب]
     [❌ إلغاء]
        └─ Select → Deleted

/set_primary
  └─ [💳 الكريمي ✅] [🏦 الراجحي] [❌ إلغاء]
        └─ Select → Marked as primary
```

---

## Chatbot Toggle (Group Admin)
```
/chatbot
  └─ Panel: "Chatbot for GroupName: ✅/❌"
        ├─ [Enable]  → Sets _CHATBOT_ENABLED (in-memory only)
        └─ [Disable] → Removes from set
```

---

## Font Conversion
```
/font <text>
  └─ Panel with font style buttons:
        [𝐁𝐨𝐥𝐝] [𝐼𝑡𝑎𝑙𝑖𝑐] [𝒮𝒸𝓇𝒾𝓅𝓉] [𝔊𝔬𝔱𝔥𝔦𝔠] [𝙼𝚘𝚗𝚘] ...
        └─ Press style → Edit message to show converted text
```

---

## Navigation Summary

| Navigation Type | Example | Notes |
|----------------|---------|-------|
| Direct command | `/ban @user` | No menu, immediate action |
| Command + inline panel | `/settings` | Multi-level inline navigation |
| ConversationHandler | `/register`, `/add_payment` | Step-by-step text input flow |
| Auto-triggered | CAPTCHA, welcome, CAS check | No user command needed |
| Callback-driven | Font selector, CAPTCHA verify | Button-based navigation |
| Reply-based | `/warn`, `/kick` | Reply to target message |
