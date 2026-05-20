"""
db/repositories/ — نقطة الدخول الموحّدة لجميع المستودعات.

الاستخدام:
    from db.repositories import bans, warns, settings, members
"""

from db.repositories import bans, members, settings, warns

__all__ = ["bans", "members", "settings", "warns"]
