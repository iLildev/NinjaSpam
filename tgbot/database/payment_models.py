"""
database/payment_models.py — نماذج حسابات الدفع للمستخدمين.

يُسجَّل المستخدم مرة واحدة عند أول /start بالبوت.
يمكنه إضافة حتى حساب واحد لكل طريقة دفع.

طرق الدفع المدعومة:
  - الكريمي  (alkarimi)  — رقم الجوال أو رقم الحساب
  - الراجحي  (alrajhi)   — رقم الآيبان أو رقم الحساب
  - PayPal   (paypal)    — البريد الإلكتروني
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PaymentMethod(str, enum.Enum):
    ALKARIMI = "alkarimi"
    ALRAJHI  = "alrajhi"
    PAYPAL   = "paypal"

    @property
    def arabic_name(self) -> str:
        return {
            PaymentMethod.ALKARIMI: "الكريمي 💳",
            PaymentMethod.ALRAJHI:  "الراجحي 🏦",
            PaymentMethod.PAYPAL:   "PayPal 🌐",
        }[self]

    @property
    def input_hint(self) -> str:
        return {
            PaymentMethod.ALKARIMI: "أدخل رقم جوالك المسجّل في الكريمي:",
            PaymentMethod.ALRAJHI:  "أدخل رقم حسابك (IBAN أو رقم الحساب) في الراجحي:",
            PaymentMethod.PAYPAL:   "أدخل عنوان بريدك الإلكتروني المرتبط بـ PayPal:",
        }[self]


# ---------------------------------------------------------------------------
# Model: UserProfile  (ملف المستخدم)
# ---------------------------------------------------------------------------

class UserProfile(Base):
    """
    سجل تسجيل المستخدم مع البوت.
    يُنشأ عند أول /start في المحادثة الخاصة.
    """
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True,
        comment="Telegram user_id — globally unique.",
    )
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_registered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True عندما يُكمل المستخدم خطوة التسجيل ويضيف حساب دفع واحداً على الأقل.",
    )
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow,
    )

    def __repr__(self) -> str:
        return f"<UserProfile user={self.user_id} registered={self.is_registered}>"


# ---------------------------------------------------------------------------
# Model: PaymentAccount  (حساب الدفع)
# ---------------------------------------------------------------------------

class PaymentAccount(Base):
    """
    حساب دفع واحد مرتبط بمستخدم.
    يُسمح بحساب واحد فقط لكل طريقة دفع لكل مستخدم.
    """
    __tablename__ = "user_payment_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "method", name="uq_payment_user_method"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod), nullable=False,
        comment="طريقة الدفع: alkarimi | alrajhi | paypal",
    )
    account_identifier: Mapped[str] = mapped_column(
        String(256), nullable=False,
        comment="رقم الحساب / رقم الجوال / البريد الإلكتروني — يُخزَّن كما أدخله المستخدم.",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="الحساب الذي يُستخدم للسحب الافتراضي.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow,
    )

    def __repr__(self) -> str:
        return f"<PaymentAccount user={self.user_id} method={self.method} id={self.account_identifier!r}>"
