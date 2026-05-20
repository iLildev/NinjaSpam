"""
economy/models.py — ORM models for the virtual economy system.

Tables:
  eco_bank_accounts  — حسابات بنكية وهمية
  eco_stats          — إحصائيات المستخدم (cooldowns, سرقة)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class BankAccount(Base):
    """حساب بنكي وهمي — رقم الحساب فريد عالمياً."""

    __tablename__ = "eco_bank_accounts"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True,
        comment="Telegram user_id — one account per user.",
    )
    account_number: Mapped[str] = mapped_column(
        String(12), nullable=False, unique=True,
        comment="رقم الحساب البنكي — 10 أرقام فريدة.",
    )
    owner_first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    owner_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )


class EconomyStats(Base):
    """
    إحصائيات الاقتصاد لكل مستخدم.
    يتتبع cooldowns الراتب والبخشيش والسرقة، وإجمالي المسروق.
    """

    __tablename__ = "eco_stats"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    last_salary_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="آخر مرة طُلب فيها الراتب (cooldown 20 دقيقة).",
    )
    last_bonus_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="آخر مرة طُلب فيها البخشيش (cooldown 10 دقائق).",
    )
    last_steal_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="آخر مرة تمّت فيها السرقة (cooldown 10 دقائق).",
    )

    total_stolen: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="إجمالي ما سُرق من المستخدمين (للترتيب).",
    )
    steal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="عدد عمليات السرقة الناجحة.",
    )
