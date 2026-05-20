"""
economy/models.py — ORM models for the virtual economy system.

Tables:
  eco_bank_accounts  — حسابات بنكية وهمية
  eco_stats          — إحصائيات المستخدم (cooldowns, سرقة)
  eco_loans          — سجلات الديون
  eco_jail           — سجل المعتقلين
  eco_heist_sessions — جلسات السطو الجماعي
  eco_heist_participants — المشاركون في السطو
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# BankAccount
# ---------------------------------------------------------------------------

class BankAccount(Base):
    """حساب بنكي وهمي — رقم الحساب فريد عالمياً."""

    __tablename__ = "eco_bank_accounts"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_number: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    owner_first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    owner_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )


# ---------------------------------------------------------------------------
# EconomyStats
# ---------------------------------------------------------------------------

class EconomyStats(Base):
    """إحصائيات الاقتصاد لكل مستخدم — cooldowns وإجمالي مسروق."""

    __tablename__ = "eco_stats"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    last_salary_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bonus_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_steal_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    total_stolen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# LoanRecord  — نظام الدين
# ---------------------------------------------------------------------------

class LoanRecord(Base):
    """
    سجل القرض النشط للمستخدم.
    لا يُسمح بأكثر من قرض واحد في وقت واحد.
    الفائدة 10% — الأجل 24 ساعة.
    """

    __tablename__ = "eco_loans"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal: Mapped[int] = mapped_column(Integer, nullable=False, comment="المبلغ المقترض")
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    total_due: Mapped[int] = mapped_column(Integer, nullable=False, comment="principal * (1 + interest)")
    amount_repaid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="موعد السداد — 24 ساعة من وقت الاقتراض",
    )
    is_repaid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    @property
    def remaining(self) -> int:
        return max(0, self.total_due - self.amount_repaid)

    @property
    def is_overdue(self) -> bool:
        return not self.is_repaid and _utcnow() > self.deadline


# ---------------------------------------------------------------------------
# JailRecord  — نظام السجن
# ---------------------------------------------------------------------------

class JailRecord(Base):
    """
    سجل الاعتقال النشط للمستخدم.
    السجين لا يستطيع استخدام أوامر الكسب حتى يدفع الكفالة أو تنتهي المدة.
    """

    __tablename__ = "eco_jail"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    bail_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    jailed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    auto_release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="يُطلق سراحه تلقائياً بعد هذا الوقت حتى لو لم يدفع الكفالة",
    )
    is_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @property
    def is_active(self) -> bool:
        if self.is_released:
            return False
        if _utcnow() >= self.auto_release_at:
            return False
        return True

    @property
    def time_left_str(self) -> str:
        remaining = self.auto_release_at - _utcnow()
        total = max(0, int(remaining.total_seconds()))
        mins, secs = divmod(total, 60)
        return f"{mins}د {secs}ث"


# ---------------------------------------------------------------------------
# HeistSession  — جلسة السطو الجماعي
# ---------------------------------------------------------------------------

class HeistSession(Base):
    """جلسة سطو جماعي نشطة في مجموعة."""

    __tablename__ = "eco_heist_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    started_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_by_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="recruiting",
        comment="recruiting | success | failed | cancelled",
    )
    loot_per_person: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    participants: Mapped[list["HeistParticipant"]] = relationship(
        "HeistParticipant", back_populates="session", cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# HeistParticipant  — مشاركو السطو
# ---------------------------------------------------------------------------

class HeistParticipant(Base):
    """مشارك في جلسة سطو."""

    __tablename__ = "eco_heist_participants"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_heist_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eco_heist_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    session: Mapped["HeistSession"] = relationship("HeistSession", back_populates="participants")
