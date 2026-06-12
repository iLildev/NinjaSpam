"""
locales/strings.py — Text catalog for Ninja Bot.

All user-facing text is defined here. Variables use the {variable} format
and are replaced at runtime by core/i18n.py::t().

Default language: en
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {

    # =========================================================================
    # English — Default Language
    # =========================================================================
    "en": {
        # ── General ───────────────────────────────────────────────────────────────
        "yes": "Yes",
        "no": "No",
        "on": "Enabled ✅",
        "off": "Disabled ✗",
        "enabled": "Enabled ✅",
        "disabled": "Disabled ✗",
        "error": "An unexpected error occurred, please try again.",
        "no_permission": "You do not have the necessary permission to perform this action.",
        "admin_only": "This command is for admins only.",
        "owner_only": "This command is for the bot owner only.",
        "bot_not_admin": "The bot must be an admin to perform this action.",
        "user_not_found": "User not found. Reply to their message or enter their ID.",
        "cant_action_admin": "This action cannot be performed on an admin.",
        "action_on_self": "You cannot perform this action on yourself.",
        "done": "Done ✅",
        "cancelled": "Cancelled.",
        "back": "« Back",
        "close": "✕ Close",
        "confirm": "✅ Confirm",
        "cancel": "✕ Cancel",
        "current_value": "Current value: {value}",
        "group_only": "⚠️ This command only works inside groups.",

        # ── Language ─────────────────────────────────────────────────────────────
        "lang_changed": "Bot language set to <b>English</b> 🇺🇸",
        "lang_select": "Bot language is set to English.",
        "lang_flag_en": "🇺🇸 English",

        # ── Settings Panel ────────────────────────────────────────────────────
        "settings_title": "⚙️ <b>Settings — {chat_title}</b>",
        "settings_choose": "Choose the category you want to adjust:",
        "settings_spam": "🛡️ Spam Protection",
        "settings_captcha": "🤖 Member Verification",
        "settings_welcome": "👋 Welcome Messages",
        "settings_warns": "⚠️ Warnings",
        "settings_locks": "🔒 Content Locks",
        "settings_general": "⚙️ General",
        "settings_saved": "✅ Setting saved.",

        # Spam Menu
        "spam_menu_title": "🛡️ <b>Spam Protection</b>\n\nAdjust automatic filtering:",
        "spam_bayes": "🤖 AI Filter",
        "spam_regex": "📝 Word Filter",
        "spam_spamwatch": "🌐 SpamWatch",
        "spam_astro": "🕵️ Anti-Coordination Campaigns",
        "spam_threshold": "🎚️ AI Sensitivity",
        "spam_bayes_action": "⚡ AI Action",
        "spam_regex_action": "⚡ Word Filter Action",
        "spam_threshold_current": "Current AI filter sensitivity: <b>{value}</b>\n\nSend a number between 0.5 and 0.99:",
        "spam_threshold_invalid": "Invalid value. Send a number between 0.5 and 0.99.",
        "spam_threshold_set": "Sensitivity set to <b>{value}</b>.",

        # Captcha Menu
        "captcha_menu_title": "🤖 <b>Verification Settings</b>\n\nAutomatic verification for new members:",
        "captcha_toggle": "Member Verification",
        "captcha_type_btn": "Type: Button",
        "captcha_type_math": "Type: Math",
        "captcha_type_text": "Type: Text",
        "captcha_type_adaptive": "Type: Smart 🧠",
        "captcha_timeout_btn": "⏱️ Timeout",
        "captcha_mute_btn": "🔇 Mute until verified",
        "captcha_kick_btn": "👢 Kick on failure",
        "captcha_type_set": "Verification type set to <b>{type}</b>.",
        "captcha_timeout_prompt": "Current timeout: <b>{value} seconds</b>\n\nSend timeout in seconds (30–3600):",
        "captcha_timeout_invalid": "Invalid value. Send a number between 30 and 3600.",
        "captcha_timeout_set": "Verification timeout set to <b>{value} seconds</b>.",

        # Welcome Menu
        "welcome_menu_title": "👋 <b>Welcome Settings</b>",
        "welcome_toggle": "Welcome Message",
        "goodbye_toggle": "Goodbye Message",
        "clean_welcome_btn": "🧹 Delete previous welcome",
        "welcome_set_btn": "✏️ Set Welcome Text",
        "goodbye_set_btn": "✏️ Set Goodbye Text",
        "welcome_prompt": "Send the welcome message text.\n\nAvailable variables:\n<code>{{first}}</code> — First Name\n<code>{{last}}</code> — Last Name\n<code>{{username}}</code> — Username\n<code>{{mention}}</code> — Mention\n<code>{{count}}</code> — Member Count\n<code>{{chatname}}</code> — Group Name",
        "welcome_set": "Welcome message updated ✅",
        "goodbye_prompt": "Send the goodbye message text. Same variables apply.",
        "goodbye_set": "Goodbye message updated ✅",

        # Warnings Menu
        "warns_menu_title": "⚠️ <b>Warning Settings</b>",
        "warn_limit_btn": "🔢 Warning Limit",
        "warn_action_btn": "⚡ Action on limit reached",
        "warn_expiry_btn": "⏳ Warning Expiration",
        "warn_reasons_btn": "📋 Warning Reasons",
        "warn_limit_prompt": "Current warning limit: <b>{value}</b>\n\nSend a number from 1 to 10:",
        "warn_limit_invalid": "Invalid value. Send a number between 1 and 10.",
        "warn_limit_set": "Warning limit set to <b>{value}</b>.",
        "warn_expiry_prompt": "Current expiration: <b>{value} days</b> (0 = never)\n\nSend number of days (0–365):",
        "warn_expiry_set": "Warning expiration set to <b>{value} days</b>.",
        "warn_reasons_title": "📋 <b>Warning Reasons</b>\n\nAdded reasons:",
        "warn_reasons_empty": "No custom reasons added yet.",
        "warn_reason_add_prompt": "Send the new warning reason text:",
        "warn_reason_added": "✅ Reason added: <b>{reason}</b>",
        "warn_reason_deleted": "✅ Reason deleted.",

        # Locks Menu
        "locks_menu_title": "🔒 <b>Content Locks</b>\n\nRestrict content types for regular members:",
        "lock_sticker": "Stickers",
        "lock_gif": "GIFs",
        "lock_photo": "Photos",
        "lock_video": "Videos",
        "lock_audio": "Audio Files",
        "lock_document": "Documents",
        "lock_voice": "Voice Messages",
        "lock_videonote": "Video Notes",
        "lock_contact": "Contacts",
        "lock_location": "Locations",
        "lock_poll": "Polls",
        "lock_forward": "Forwards",
        "lock_link": "Links",
        "lock_game": "Games",

        # General Settings
        "general_menu_title": "⚙️ <b>General Settings</b>",
        "general_language": "🌐 Language",
        "general_log_channel": "📋 Log Channel",
        "general_rules": "📜 Group Rules",
        "general_cas": "🔰 CAS Protection",
        "general_gban": "🌍 Global Ban",
        "log_channel_prompt": "Forward a message from the log channel, or send its ID (negative number):",
        "log_channel_set": "Log channel set to <b>{channel}</b>.",
        "log_channel_removed": "Log channel removed.",

        # Broadcast
        "broadcast_usage": "Usage: /broadcast <message>\n\nOr reply to a message with /broadcast to resend it.",
        "broadcast_started": "📢 Broadcasting to {count} groups…",
        "broadcast_done": "📢 Broadcast completed!\n✅ Sent: {sent}\n❌ Failed: {failed}\n⏱ Duration: {duration} seconds",
        "broadcast_confirm": "📢 <b>Broadcast Preview</b>\n\nWill be sent to <b>{count} groups</b>.\n\nConfirm?",

        # SpamWatch
        "spamwatch_banned": "🚫 <b>SpamWatch Ban</b>\n\nUser <a href='tg://user?id={user_id}'>{name}</a> exists in the SpamWatch database.\n\n<b>Reason:</b> {reason}\n<b>Action:</b> Automatically banned.",
        "spamwatch_error": "SpamWatch verification failed: {error}",
        "spamwatch_not_configured": "SpamWatch token not set. Add SPAMWATCH_TOKEN to environment variables.",

        # Channel Forward Protection
        "chanprotect_menu_title": "📡 <b>Channel Forward Protection</b>",
        "chanprotect_toggle": "Forward Protection",
        "chanprotect_whitelist_btn": "📋 Whitelist",
        "chanprotect_add_prompt": "Forward a message from the channel to allow, or send its @username or ID:",
        "chanprotect_added": "✅ Channel added to whitelist: <b>{channel}</b>",
        "chanprotect_removed": "✅ Channel removed from whitelist.",
        "chanprotect_list_empty": "Whitelist is empty.",
        "chanprotect_blocked": "⛔ Forwarding from unauthorized channels is blocked in this group.",

        # Scheduled Messages
        "schedule_usage": "Usage: /schedule <HH:MM> <message>\n\nExample: /schedule 09:00 Good morning everyone!",
        "schedule_invalid_time": "Invalid time format. Use HH:MM (e.g., 09:30).",
        "schedule_added": "✅ Message scheduled for <b>{time}</b> daily.",
        "schedule_list_empty": "No scheduled messages.",
        "schedule_list_title": "📅 <b>Scheduled Messages</b>",
        "schedule_deleted": "✅ Scheduled message deleted.",
        "schedule_limit": "You have reached the maximum allowed limit (5 scheduled messages) per group.",

        # Ban Appeals
        "appeal_usage": "Send /appeal <reason> in private chat with the bot to appeal your ban.",
        "appeal_submitted": "✅ Appeal request submitted to group admins.\n\n<b>Group:</b> {chat_title}\n<b>Reason:</b> {reason}\n\nYou will be notified of the decision once made.",
        "appeal_no_ban": "You don't seem to be banned from any group managed by this bot.",
        "appeal_already_pending": "You already have a pending appeal request, please wait.",
        "appeal_notify_admins": "🔔 <b>Ban Appeal Request</b>\n\n<b>User:</b> {mention} (<code>{user_id}</code>)\n<b>Ban Reason:</b> {ban_reason}\n<b>Appeal Text:</b>\n{appeal_text}\n\n<b>Time Submitted:</b> {time}",
        "appeal_approved": "✅ <b>Appeal Accepted</b>\n\nYour appeal for group {chat_title} has been accepted, you can rejoin.",
        "appeal_rejected": "❌ <b>Appeal Rejected</b>\n\nYour appeal for group {chat_title} has been rejected.",
        "appeal_approve_btn": "✅ Accept",
        "appeal_reject_btn": "❌ Reject",

        # Adaptive Captcha
        "adaptive_captcha_low": "👋 Welcome {mention}!\n\nClick the button below to verify your identity.",
        "adaptive_captcha_med": "👋 Welcome {mention}!\n\n⚠️ Medium risk score detected on your account.\n\nWhat is the result of <b>{question}</b>?",
        "adaptive_captcha_high": "⚠️ {mention}، your account shows a high-risk pattern.\n\nYou must solve this verification to join the group:\n\n<b>{question}</b>",
        "risk_score_info": "Risk Score: {score}/100",

        # Anti-Coordination Campaigns
        "astro_detected": "🕵️ <b>Coordinated Spam Detected</b>\n\n{count} accounts sent similar messages simultaneously.\n\nAll accounts have been {action}.",
        "astro_admin_notify": "🚨 Anti-Campaign Alert:\n• Count: {count} accounts\n• Similarity: {sim}%\n• Action: {action}",

        # Warning Reasons
        "warn_with_reason": "⚠️ {mention} has been warned ({count}/{limit})\n<b>Reason:</b> {reason}",
        "warn_no_reason": "⚠️ {mention} has been warned ({count}/{limit})",
        "warn_select_reason": "Choose warning reason:",
        "warn_custom_reason": "✏️ Custom Reason",
        "warn_admin": "🛡 Admins cannot be warned.",
        "warn_self": "I won't warn myself.",
        "warn_no_target": "⚠️ Reply to user message or send their @username or ID.",
        "warn_need_user": "⚠️ Specify the user to be warned.",
        "warns_none": "✅ {mention} has no active warnings.",
        "warns_count": "⚠️ {mention} has <b>{count}/{limit}</b> warnings.",
        "warns_cleared": "✅ All warnings for {mention} cleared.",

        # Federations
        "fed_auto_ban": "🌐 <b>Federation Ban — Automatic Application</b>\n\n{mention} was banned in <b>{source_chat}</b> and automatically removed from this group.\n<b>Federation:</b> {fed_name}",

        # Set Language
        "setlang_invalid": "Language code not supported. Supported: en",
        "setlang_changed": "✅ Bot language set to English.",

        # Account Age Filter
        "age_gate_kicked": "👶 {mention}, your account is newly created (≈<b>{age} days</b>). Minimum required: <b>{min_days} days</b>. You can try again later.",
        "age_gate_on": "✅ Account age filter <b>enabled</b>.\nMinimum: <b>{min_days} days</b> · Action: <b>{action}</b>",
        "age_gate_off": "❌ Account age filter <b>disabled</b>.",
        "age_gate_status": "🗓 <b>Account Age Filter</b>\nStatus: {state}\nMinimum: {min_days} days\nAction: {action}",
        "age_gate_usage": "Usage: /setage <days> [kick|restrict]\nExample: /setage 30 kick\n/setage off — disable\n/setage status — show current setting",
        "age_gate_invalid": "Invalid value. Days must be between 1 and 365, and action: kick or restrict.",

        # Anti-Nuke
        "nuke_alert": "🚨 <b>Alert — Takeover Attempt</b>\n\n<b>{count}</b> admin promotions detected within <b>{window} seconds</b>!\n\n<b>Affected members:</b> {users}",
        "nuke_reverted": "🛡 Anti-Nuke: Admin permissions revoked from {user}.",
        "nuke_on": "✅ Anti-Nuke <b>enabled</b>.\nLimit: <b>{threshold}</b> promotions every <b>{window} seconds</b>",
        "nuke_off": "❌ Anti-Nuke <b>disabled</b>.",
        "nuke_status": "🛡 <b>Anti-Nuke</b>\nStatus: {state}\nLimit: {threshold} promotions in {window} seconds\nAction: {action}",
        "nuke_usage": "Usage:\n/antinuke on|off\n/antinuke threshold <2-10>\n/antinuke window <10-300>\n/antinuke action alert|demote\n/antinuke status",

        # Language Filter
        "langfilter_deleted": "🌐 {mention}, please write in <b>{allowed}</b> only. Your message has been deleted.",
        "langfilter_on": "✅ Language filter <b>enabled</b>.\nAllowed scripts: <b>{allowed}</b>",
        "langfilter_off": "❌ Language filter <b>disabled</b>. All languages allowed.",
        "langfilter_status": "🌐 <b>Language Filter</b>\nStatus: {state}\nAllowed: {allowed}\nAction: {action}",
        "langfilter_usage": "Usage: /langfilter english|latin|cjk|english+latin|all\n/langfilter off\n/langfilter action delete|warn|mute\n/langfilter status",
        "langfilter_invalid": "Invalid script. Options: english, latin, cjk (can combine with +: english+latin)",

        # Phishing Detection
        "phishing_detected": "🚨 {mention} sent a <b>phishing or malicious link</b>. Message deleted.",
        "phishing_on": "✅ Phishing link detection <b>enabled</b>.",
        "phishing_off": "❌ Phishing link detection <b>disabled</b>.",
        "phishing_status": "🔗 <b>Phishing Detection</b>\nStatus: {state}\nAction: {action}\nChecked links: {count}",
        "phishing_usage": "Usage: /phishing on|off|status\n/phishing action delete|warn|ban\n/phishing check <link>",

        # Moderation
        "settings_moderation": "🚔 Moderation",
        "mod_menu_title": "🚔 <b>Moderation</b>\n\nManage flood, links, raids, and reports:",
        "mod_flood": "🌊 Anti-Flood",
        "mod_flood_limit_btn": "🌊 Limit: {limit} messages",
        "mod_antilinks": "🔗 Anti-Links",
        "mod_antiraid": "⚔️ Anti-Raid",
        "mod_reports": "📢 Reports",
        "mod_flood_limit_prompt": "Current flood limit: <b>{value}</b> messages.\n\nSend new limit (3–50):",
        "mod_flood_limit_set": "Flood limit set to <b>{value}</b> consecutive messages.",
        "mod_flood_limit_invalid": "Invalid value. Send a number between 3 and 50.",
        "antilinks_mode_off": "🔗 Links: Allowed",
        "antilinks_mode_invite": "🔗 Links: Invites only",
        "antilinks_mode_all": "🔗 Links: Block all",

        # Ban and Kick
        "ban_done": "🔨 {mention} has been permanently banned.{reason}",
        "ban_temp_done": "⏳ {mention} has been banned for <b>{duration}</b>.{reason}",
        "kick_done": "👢 {mention} has been kicked. They can rejoin via invite link.{reason}",
        "unban_done": "✅ {mention} has been unbanned, they can rejoin.",
        "ban_admin": "🛡 This user is an admin — this action cannot be performed on them.",
        "ban_missing_target": "⚠️ I couldn't identify the intended user.\nReply to their message or send their @username or ID.",

        # Mute and Unmute
        "mute_done": "🔇 {mention} has been muted.{reason}",
        "mute_temp_done": "⏳ {mention} has been muted for <b>{duration}</b>.{reason}",
        "unmute_done": "🔊 {mention} can send messages again.",
        "mute_already": "ℹ️ This user is already muted.",
        "unmute_already": "ℹ️ This user already has full posting rights.",
        "mute_admin": "🛡 This user is an admin — they cannot be muted.",
    },
}
