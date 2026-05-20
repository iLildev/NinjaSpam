# Ninja Bot

بوت تيليجرام لحماية وإدارة المجموعات مع ميزات ترفيهية (ألعاب ونظام نقود وهمي للتسلية).

## تشغيل البوت

```
cd tgbot && pip install -r requirements.txt -q && python main.py
```

## المتطلبات

- `BOT_TOKEN` — توكن البوت من @BotFather
- `DATABASE_URL` — رابط قاعدة بيانات PostgreSQL
- `OWNER_IDS` — معرّف المالك على تيليجرام

## هيكل المشروع

```
tgbot/
├── main.py               # نقطة الدخول
├── config.py             # الإعدادات من متغيرات البيئة
├── plugins/              # جميع الإضافات (70 إضافة)
├── database/             # نماذج SQLAlchemy وإعداد قاعدة البيانات
├── core/                 # محمّل الإضافات ومعالج الأخطاء
└── locales/              # ملفات الترجمة
```

## الإضافات الرئيسية

- **الحماية**: antispam، captcha، antiraid، cas_check، global_bans
- **الإدارة**: bans، muting، warns، locks، admin، federation
- **الترفيه**: ninja_game، farm_game، castle_game، wallet (نقود وهمية)
- **الأدوات**: notes، filters، rules، rss، scheduler

## Stack

- Python 3.11
- python-telegram-bot v20.7 (async)
- SQLAlchemy 2.0 (async) + asyncpg
- PostgreSQL

## User preferences

_يُعبّأ عند الحاجة._
