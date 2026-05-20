"""
locales/strings.py — كتالوج النصوص لبوت نينجا.

جميع النصوص الظاهرة للمستخدم محددة هنا. المتغيرات تستخدم صيغة {variable}
وتُستبدل في وقت التشغيل بواسطة core/i18n.py::t().

اللغة الوحيدة: ar — العربية الفصحى
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {

    # =========================================================================
    # العربية — اللغة الوحيدة والافتراضية
    # =========================================================================
    "ar": {
        # ── عام ───────────────────────────────────────────────────────────────
        "yes": "نعم",
        "no": "لا",
        "on": "مفعّل ✅",
        "off": "معطّل ✗",
        "enabled": "مفعّل ✅",
        "disabled": "معطّل ✗",
        "error": "حدث خطأ غير متوقع، يرجى المحاولة مجدداً.",
        "no_permission": "ليس لديك الصلاحية اللازمة لتنفيذ هذا الإجراء.",
        "admin_only": "هذا الأمر مخصص للمشرفين فحسب.",
        "owner_only": "هذا الأمر مخصص لمالك البوت فحسب.",
        "bot_not_admin": "يلزم أن يكون البوت مشرفاً لتنفيذ هذا الإجراء.",
        "user_not_found": "لم يُعثر على المستخدم. رد على رسالته أو أدخل معرّفه.",
        "cant_action_admin": "لا يمكن تنفيذ هذا الإجراء على مشرف.",
        "action_on_self": "لا يمكنك تنفيذ هذا الإجراء على نفسك.",
        "done": "تمّ ✅",
        "cancelled": "تم الإلغاء.",
        "back": "« رجوع",
        "close": "✕ إغلاق",
        "confirm": "✅ تأكيد",
        "cancel": "✕ إلغاء",
        "current_value": "القيمة الحالية: {value}",
        "group_only": "يعمل هذا الأمر في المجموعات فقط.",

        # ── اللغة ─────────────────────────────────────────────────────────────
        "lang_changed": "تم تعيين لغة البوت إلى <b>العربية</b> 🇸🇦",
        "lang_select": "لغة البوت مثبّتة على العربية.",
        "lang_flag_ar": "🇸🇦 العربية",

        # ── لوحة الإعدادات ────────────────────────────────────────────────────
        "settings_title": "⚙️ <b>الإعدادات — {chat_title}</b>",
        "settings_choose": "اختر الفئة التي تودّ ضبطها:",
        "settings_spam": "🛡️ الحماية من البريد المزعج",
        "settings_captcha": "🤖 التحقق من الأعضاء",
        "settings_welcome": "👋 رسائل الترحيب",
        "settings_warns": "⚠️ التحذيرات",
        "settings_locks": "🔒 أقفال المحتوى",
        "settings_general": "⚙️ عام",
        "settings_saved": "✅ تم حفظ الإعداد.",

        # قائمة البريد المزعج
        "spam_menu_title": "🛡️ <b>الحماية من البريد المزعج</b>\n\nضبط الفلترة التلقائية:",
        "spam_bayes": "🤖 فلتر الذكاء الاصطناعي",
        "spam_regex": "📝 فلتر الكلمات",
        "spam_spamwatch": "🌐 SpamWatch",
        "spam_astro": "🕵️ مكافحة الحملات المنسّقة",
        "spam_threshold": "🎚️ حساسية الذكاء الاصطناعي",
        "spam_bayes_action": "⚡ إجراء الذكاء الاصطناعي",
        "spam_regex_action": "⚡ إجراء فلتر الكلمات",
        "spam_threshold_current": "الحساسية الحالية لفلتر الذكاء الاصطناعي: <b>{value}</b>\n\nأرسل رقماً بين 0.5 و0.99:",
        "spam_threshold_invalid": "قيمة غير صحيحة. أرسل رقماً بين 0.5 و0.99.",
        "spam_threshold_set": "تم ضبط الحساسية على <b>{value}</b>.",

        # قائمة التحقق
        "captcha_menu_title": "🤖 <b>إعدادات التحقق</b>\n\nالتحقق التلقائي من الأعضاء الجدد:",
        "captcha_toggle": "التحقق من الأعضاء",
        "captcha_type_btn": "النوع: زر",
        "captcha_type_math": "النوع: حسابي",
        "captcha_type_text": "النوع: نصي",
        "captcha_type_adaptive": "النوع: ذكي 🧠",
        "captcha_timeout_btn": "⏱️ المهلة الزمنية",
        "captcha_mute_btn": "🔇 كتم حتى التحقق",
        "captcha_kick_btn": "👢 طرد عند الفشل",
        "captcha_type_set": "تم ضبط نوع التحقق على <b>{type}</b>.",
        "captcha_timeout_prompt": "المهلة الحالية: <b>{value} ثانية</b>\n\nأرسل المهلة بالثواني (30–3600):",
        "captcha_timeout_invalid": "قيمة غير صحيحة. أرسل رقماً بين 30 و3600.",
        "captcha_timeout_set": "تم ضبط مهلة التحقق على <b>{value} ثانية</b>.",

        # قائمة الترحيب
        "welcome_menu_title": "👋 <b>إعدادات الترحيب</b>",
        "welcome_toggle": "رسالة الترحيب",
        "goodbye_toggle": "رسالة الوداع",
        "clean_welcome_btn": "🧹 حذف الترحيب السابق",
        "welcome_set_btn": "✏️ تعيين نص الترحيب",
        "goodbye_set_btn": "✏️ تعيين نص الوداع",
        "welcome_prompt": "أرسل نص رسالة الترحيب.\n\nالمتغيرات المتاحة:\n<code>{{first}}</code> — الاسم الأول\n<code>{{last}}</code> — الاسم الأخير\n<code>{{username}}</code> — المعرّف\n<code>{{mention}}</code> — الإشارة\n<code>{{count}}</code> — عدد الأعضاء\n<code>{{chatname}}</code> — اسم المجموعة",
        "welcome_set": "تم تحديث رسالة الترحيب ✅",
        "goodbye_prompt": "أرسل نص رسالة الوداع. تنطبق عليها نفس المتغيرات.",
        "goodbye_set": "تم تحديث رسالة الوداع ✅",

        # قائمة التحذيرات
        "warns_menu_title": "⚠️ <b>إعدادات التحذيرات</b>",
        "warn_limit_btn": "🔢 حد التحذيرات",
        "warn_action_btn": "⚡ الإجراء عند بلوغ الحد",
        "warn_expiry_btn": "⏳ انتهاء صلاحية التحذيرات",
        "warn_reasons_btn": "📋 أسباب التحذير",
        "warn_limit_prompt": "حد التحذيرات الحالي: <b>{value}</b>\n\nأرسل رقماً من 1 إلى 10:",
        "warn_limit_invalid": "قيمة غير صحيحة. أرسل رقماً بين 1 و10.",
        "warn_limit_set": "تم ضبط حد التحذيرات على <b>{value}</b>.",
        "warn_expiry_prompt": "الانتهاء الحالي: <b>{value} يوماً</b> (0 = لا تنتهي)\n\nأرسل عدد الأيام (0–365):",
        "warn_expiry_set": "تم ضبط انتهاء صلاحية التحذيرات على <b>{value} يوماً</b>.",
        "warn_reasons_title": "📋 <b>أسباب التحذير</b>\n\nالأسباب المضافة:",
        "warn_reasons_empty": "لا توجد أسباب مخصصة بعد.",
        "warn_reason_add_prompt": "أرسل نص سبب التحذير الجديد:",
        "warn_reason_added": "✅ تمت إضافة السبب: <b>{reason}</b>",
        "warn_reason_deleted": "✅ تم حذف السبب.",

        # قائمة الأقفال
        "locks_menu_title": "🔒 <b>أقفال المحتوى</b>\n\nتقييد أنواع المحتوى للأعضاء العاديين:",
        "lock_sticker": "الملصقات",
        "lock_gif": "الصور المتحركة",
        "lock_photo": "الصور",
        "lock_video": "مقاطع الفيديو",
        "lock_audio": "الملفات الصوتية",
        "lock_document": "المستندات",
        "lock_voice": "الرسائل الصوتية",
        "lock_videonote": "رسائل الفيديو",
        "lock_contact": "جهات الاتصال",
        "lock_location": "المواقع الجغرافية",
        "lock_poll": "الاستطلاعات",
        "lock_forward": "إعادة التوجيه",
        "lock_link": "الروابط",
        "lock_game": "الألعاب",

        # الإعدادات العامة
        "general_menu_title": "⚙️ <b>الإعدادات العامة</b>",
        "general_language": "🌐 اللغة",
        "general_log_channel": "📋 قناة السجلات",
        "general_rules": "📜 قواعد المجموعة",
        "general_cas": "🔰 حماية CAS",
        "general_gban": "🌍 الحظر العالمي",
        "log_channel_prompt": "أعِد توجيه رسالة من قناة السجلات، أو أرسل معرّفها (رقم سالب):",
        "log_channel_set": "تم ضبط قناة السجلات على <b>{channel}</b>.",
        "log_channel_removed": "تمت إزالة قناة السجلات.",

        # الإذاعة
        "broadcast_usage": "الاستخدام: /broadcast <رسالة>\n\nأو رد على رسالة بـ /broadcast لإعادة إرسالها.",
        "broadcast_started": "📢 جارٍ الإذاعة إلى {count} مجموعة…",
        "broadcast_done": "📢 اكتملت الإذاعة!\n✅ أُرسلت: {sent}\n❌ فشلت: {failed}\n⏱ المدة: {duration} ثانية",
        "broadcast_confirm": "📢 <b>معاينة الإذاعة</b>\n\nسيتم الإرسال إلى <b>{count} مجموعة</b>.\n\nتأكيد؟",

        # SpamWatch
        "spamwatch_banned": "🚫 <b>حظر SpamWatch</b>\n\nالمستخدم <a href='tg://user?id={user_id}'>{name}</a> موجود في قاعدة بيانات SpamWatch.\n\n<b>السبب:</b> {reason}\n<b>الإجراء:</b> تم الحظر تلقائياً.",
        "spamwatch_error": "فشل التحقق عبر SpamWatch: {error}",
        "spamwatch_not_configured": "رمز SpamWatch غير مضبوط. أضف SPAMWATCH_TOKEN إلى متغيرات البيئة.",

        # حماية التوجيه من القنوات
        "chanprotect_menu_title": "📡 <b>حماية التوجيه من القنوات</b>",
        "chanprotect_toggle": "حماية التوجيه",
        "chanprotect_whitelist_btn": "📋 القائمة البيضاء",
        "chanprotect_add_prompt": "أعِد توجيه رسالة من القناة المراد السماح بها، أو أرسل @معرّفها أو رقمها:",
        "chanprotect_added": "✅ تمت إضافة القناة إلى القائمة البيضاء: <b>{channel}</b>",
        "chanprotect_removed": "✅ تمت إزالة القناة من القائمة البيضاء.",
        "chanprotect_list_empty": "القائمة البيضاء فارغة.",
        "chanprotect_blocked": "⛔ إعادة التوجيه من قنوات غير مصرح بها محظورة في هذه المجموعة.",

        # الرسائل المجدولة
        "schedule_usage": "الاستخدام: /schedule <HH:MM> <رسالة>\n\nمثال: /schedule 09:00 صباح الخير جميعاً!",
        "schedule_invalid_time": "صيغة الوقت غير صحيحة. استخدم HH:MM (مثل 09:30).",
        "schedule_added": "✅ تمت جدولة الرسالة عند <b>{time}</b> يومياً.",
        "schedule_list_empty": "لا توجد رسائل مجدولة.",
        "schedule_list_title": "📅 <b>الرسائل المجدولة</b>",
        "schedule_deleted": "✅ تم حذف الرسالة المجدولة.",
        "schedule_limit": "وصلت إلى الحد الأقصى المسموح به (5 رسائل مجدولة) لكل مجموعة.",

        # طلبات الاعتراض على الحظر
        "appeal_usage": "أرسل /appeal <السبب> في المحادثة الخاصة مع البوت للاعتراض على حظرك.",
        "appeal_submitted": "✅ تم تقديم طلب الاعتراض إلى مشرفي المجموعة.\n\n<b>المجموعة:</b> {chat_title}\n<b>السبب:</b> {reason}\n\nستُبلَّغ بالقرار فور اتخاذه.",
        "appeal_no_ban": "لا يبدو أنك محظور من أي مجموعة يديرها هذا البوت.",
        "appeal_already_pending": "لديك طلب اعتراض قيد المراجعة بالفعل، يرجى الانتظار.",
        "appeal_notify_admins": "🔔 <b>طلب اعتراض على الحظر</b>\n\n<b>المستخدم:</b> {mention} (<code>{user_id}</code>)\n<b>سبب الحظر:</b> {ban_reason}\n<b>نص الاعتراض:</b>\n{appeal_text}\n\n<b>وقت التقديم:</b> {time}",
        "appeal_approved": "✅ <b>تم قبول الاعتراض</b>\n\nتم قبول اعتراضك على مجموعة {chat_title}، يمكنك الانضمام مجدداً.",
        "appeal_rejected": "❌ <b>تم رفض الاعتراض</b>\n\nتم رفض اعتراضك على مجموعة {chat_title}.",
        "appeal_approve_btn": "✅ قبول",
        "appeal_reject_btn": "❌ رفض",

        # التحقق التكيّفي
        "adaptive_captcha_low": "👋 أهلاً بك {mention}!\n\nاضغط الزر أدناه للتحقق من هويتك.",
        "adaptive_captcha_med": "👋 أهلاً بك {mention}!\n\n⚠️ رُصدت درجة خطر متوسطة على حسابك.\n\nما نتيجة <b>{question}</b>؟",
        "adaptive_captcha_high": "⚠️ {mention}، يُظهر حسابك نمطاً مرتفع الخطورة.\n\nيجب حل هذا التحقق للانضمام إلى المجموعة:\n\n<b>{question}</b>",
        "risk_score_info": "درجة الخطر: {score}/100",

        # مكافحة الحملات المنسّقة
        "astro_detected": "🕵️ <b>رُصد بريد مزعج منسّق</b>\n\nأرسل {count} حساباً رسائل متشابهة في آنٍ واحد.\n\nتم {action} جميع الحسابات.",
        "astro_admin_notify": "🚨 تنبيه مكافحة الحملات:\n• العدد: {count} حساب\n• نسبة التشابه: {sim}%\n• الإجراء: {action}",

        # أسباب التحذير
        "warn_with_reason": "⚠️ تم تحذير {mention} ({count}/{limit})\n<b>السبب:</b> {reason}",
        "warn_no_reason": "⚠️ تم تحذير {mention} ({count}/{limit})",
        "warn_select_reason": "اختر سبب التحذير:",
        "warn_custom_reason": "✏️ سبب مخصص",
        "warn_admin": "🛡 لا يمكن تحذير المشرفين.",
        "warn_self": "لن أُحذّر نفسي.",
        "warn_no_target": "⚠️ رد على رسالة المستخدم أو أرسل @معرّفه أو رقمه.",
        "warn_need_user": "⚠️ حدّد المستخدم المراد تحذيره.",
        "warns_none": "✅ لا توجد تحذيرات نشطة لدى {mention}.",
        "warns_count": "⚠️ لدى {mention} <b>{count}/{limit}</b> تحذير.",
        "warns_cleared": "✅ تم مسح جميع تحذيرات {mention}.",

        # الاتحادات
        "fed_auto_ban": "🌐 <b>حظر الاتحاد — تطبيق تلقائي</b>\n\nتم حظر {mention} في <b>{source_chat}</b> وإزالته تلقائياً من هذه المجموعة.\n<b>الاتحاد:</b> {fed_name}",

        # ضبط اللغة
        "setlang_invalid": "رمز اللغة غير مدعوم. المدعوم الوحيد: ar",
        "setlang_changed": "✅ لغة البوت مثبّتة على العربية.",

        # فلتر عمر الحساب
        "age_gate_kicked": "👶 {mention}، حسابك حديث الإنشاء (≈<b>{age} يوماً</b>). الحد الأدنى المطلوب: <b>{min_days} يوماً</b>. يمكنك إعادة المحاولة لاحقاً.",
        "age_gate_on": "✅ فلتر عمر الحساب <b>مفعّل</b>.\nالحد الأدنى: <b>{min_days} يوماً</b> · الإجراء: <b>{action}</b>",
        "age_gate_off": "❌ فلتر عمر الحساب <b>معطّل</b>.",
        "age_gate_status": "🗓 <b>فلتر عمر الحساب</b>\nالحالة: {state}\nالحد الأدنى: {min_days} يوم\nالإجراء: {action}",
        "age_gate_usage": "الاستخدام: /setage &lt;أيام&gt; [kick|restrict]\nمثال: /setage 30 kick\n/setage off — تعطيل\n/setage status — عرض الإعداد الحالي",
        "age_gate_invalid": "قيمة غير صحيحة. يجب أن تكون الأيام بين 1 و365، والإجراء: kick أو restrict.",

        # مضاد النيوك
        "nuke_alert": "🚨 <b>تنبيه — محاولة استيلاء</b>\n\nرُصدت <b>{count}</b> عملية ترقية لمشرف خلال <b>{window} ثانية</b>!\n\n<b>الأعضاء المتأثرون:</b> {users}",
        "nuke_reverted": "🛡 مضاد النيوك: تم سحب صلاحيات الإشراف من {user}.",
        "nuke_on": "✅ مضاد النيوك <b>مفعّل</b>.\nالحد: <b>{threshold}</b> ترقية كل <b>{window} ثانية</b>",
        "nuke_off": "❌ مضاد النيوك <b>معطّل</b>.",
        "nuke_status": "🛡 <b>مضاد النيوك</b>\nالحالة: {state}\nالحد: {threshold} ترقية في {window} ثانية\nالإجراء: {action}",
        "nuke_usage": "الاستخدام:\n/antinuke on|off\n/antinuke threshold &lt;2-10&gt;\n/antinuke window &lt;10-300&gt;\n/antinuke action alert|demote\n/antinuke status",

        # فلتر اللغة
        "langfilter_deleted": "🌐 {mention}، يُرجى الكتابة بـ<b>{allowed}</b> فقط. تم حذف رسالتك.",
        "langfilter_on": "✅ فلتر اللغة <b>مفعّل</b>.\nالخطوط المسموحة: <b>{allowed}</b>",
        "langfilter_off": "❌ فلتر اللغة <b>معطّل</b>. جميع اللغات مسموحة.",
        "langfilter_status": "🌐 <b>فلتر اللغة</b>\nالحالة: {state}\nالمسموح: {allowed}\nالإجراء: {action}",
        "langfilter_usage": "الاستخدام: /langfilter arabic|latin|cjk|arabic+latin|all\n/langfilter off\n/langfilter action delete|warn|mute\n/langfilter status",
        "langfilter_invalid": "خط غير صحيح. الخيارات: arabic, latin, cjk (يمكن الجمع بـ +: arabic+latin)",

        # كشف التصيد الاحتيالي
        "phishing_detected": "🚨 {mention} أرسل <b>رابطاً احتيالياً أو ضاراً</b>. تم حذف الرسالة.",
        "phishing_on": "✅ كشف روابط التصيد <b>مفعّل</b>.",
        "phishing_off": "❌ كشف روابط التصيد <b>معطّل</b>.",
        "phishing_status": "🔗 <b>كشف التصيد الاحتيالي</b>\nالحالة: {state}\nالإجراء: {action}\nروابط مفحوصة: {count}",
        "phishing_usage": "الاستخدام: /phishing on|off|status\n/phishing action delete|warn|ban\n/phishing check &lt;رابط&gt;",

        # الإشراف
        "settings_moderation": "🚔 الإشراف",
        "mod_menu_title": "🚔 <b>الإشراف</b>\n\nإدارة الفيضان والروابط والغارات والبلاغات:",
        "mod_flood": "🌊 مكافحة الفيضان",
        "mod_flood_limit_btn": "🌊 الحد: {limit} رسالة",
        "mod_antilinks": "🔗 مكافحة الروابط",
        "mod_antiraid": "⚔️ مكافحة الغارات",
        "mod_reports": "📢 البلاغات",
        "mod_flood_limit_prompt": "حد الفيضان الحالي: <b>{value}</b> رسالة.\n\nأرسل الحد الجديد (3–50):",
        "mod_flood_limit_set": "تم ضبط حد الفيضان على <b>{value}</b> رسالة متتالية.",
        "mod_flood_limit_invalid": "قيمة غير صحيحة. أرسل رقماً بين 3 و50.",
        "antilinks_mode_off": "🔗 الروابط: مسموح",
        "antilinks_mode_invite": "🔗 الروابط: دعوات فقط",
        "antilinks_mode_all": "🔗 الروابط: محظور الكل",

        # الحظر والطرد
        "ban_done": "🔨 تم حظر {mention} نهائياً.{reason}",
        "ban_temp_done": "⏳ تم حظر {mention} لمدة <b>{duration}</b>.{reason}",
        "kick_done": "👢 تم طرد {mention}. يمكنه العودة عبر رابط الدعوة.{reason}",
        "unban_done": "✅ تم رفع الحظر عن {mention}، يمكنه الانضمام مجدداً.",
        "ban_admin": "🛡 هذا المستخدم مشرف — لا يمكن تنفيذ هذا الإجراء عليه.",
        "ban_missing_target": "⚠️ لم أتمكن من تحديد المستخدم المقصود.\nرد على رسالته أو أرسل @معرّفه أو رقمه.",

        # الكتم وإلغاؤه
        "mute_done": "🔇 تم كتم {mention}.{reason}",
        "mute_temp_done": "⏳ تم كتم {mention} لمدة <b>{duration}</b>.{reason}",
        "unmute_done": "🔊 يستطيع {mention} إرسال الرسائل مجدداً.",
        "mute_already": "ℹ️ هذا المستخدم مكتوم بالفعل.",
        "unmute_already": "ℹ️ هذا المستخدم يتمتع بحقوق إرسال كاملة بالفعل.",
        "mute_admin": "🛡 هذا المستخدم مشرف — لا يمكن كتمه.",
    },
}
