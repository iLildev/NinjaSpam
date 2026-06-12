"""
economy/models.py — ORM models for the virtual economy system.

Tables:
  eco_bank_accounts  — Virtual bank accounts
  eco_stats          — User stats (cooldowns, stealing)
  eco_loans          — Debt records
  eco_jail           — Inmate records
  eco_heist_sessions — Group heist sessions
  eco_heist_participants — Heist participants
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
    """Virtual bank account — Account number is globally unique."""

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
    """Economy stats for each user — cooldowns and total stolen."""

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
# LoanRecord  — Debt System
# ---------------------------------------------------------------------------

class LoanRecord(Base):
    """
    Active loan record for the user.
    Only one loan allowed at a time.
    10% interest — 24 hour term.
    """

    __tablename__ = "eco_loans"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal: Mapped[int] = mapped_column(Integer, nullable=False, comment="Amount borrowed")
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    total_due: Mapped[int] = mapped_column(Integer, nullable=False, comment="principal * (1 + interest)")
    amount_repaid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Repayment deadline — 24 hours from borrowing",
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
# JailRecord  — Jail System
# ---------------------------------------------------------------------------

class JailRecord(Base):
    """
    Active jail record for the user.
    Prisoner cannot use earning commands until bail is paid or time expires.
    """

    __tablename__ = "eco_jail"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    bail_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    jailed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    auto_release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Automatic release after this time even if bail not paid",
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
        return f"{mins}m {secs}s"


# ---------------------------------------------------------------------------
# HeistSession  — Group Heist Session
# ---------------------------------------------------------------------------

class HeistSession(Base):
    """Active group heist session in a group."""

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
# HeistParticipant  — Heist Participants
# ---------------------------------------------------------------------------

class HeistParticipant(Base):
    """Participant in a heist session."""

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
