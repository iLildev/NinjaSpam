"""
database/game_models.py — ORM models for the Castle Kingdom game and universal Wallet.

Schema overview:
  Wallet            — عملة عامة لجميع الألعاب (مستقلة عن ذهب القلعة)
  Castle            — بيانات القلعة الأساسية (مستوى، خبرة)
  CastleResources   — موارد القلعة: خشب، حجر، طعام، ذهب (ذهب القلعة ≠ عملة المحفظة)
  Barracks          — المعسكر العسكري وقوة الجيش
  ImmunityCard      — بطاقات الحصانة المجمّعة
  TreasureHunt      — تتبع آخر تنقيب لكل مستخدم (cooldown)
  GlobalBattle      — جلسة المعركة الكبرى لكل مجموعة
  BattleParticipant — المشتركون في معركة كبرى
  AllianceRequest   — طلبات التحالف بين المستخدمين
  RulerTitle        — سجل ألقاب الحكام الفائزين
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
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AllianceStatus(str, enum.Enum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Model: Wallet  (عملة عامة — مستقلة عن ذهب القلعة)
# ---------------------------------------------------------------------------

class Wallet(Base):
    """
    محفظة المستخدم العامة.
    تُستخدم العملات هنا في شراء موارد القلعة، الجيش، وألعاب أخرى.
    الذهب داخل لعبة القلعة (CastleResources.gold) منفصل تماماً.
    """
    __tablename__ = "game_wallets"

    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True,
        comment="Telegram user_id — globally unique across all chats.",
    )
    coins: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Current coin balance. New users start with 100 coins.",
    )
    total_earned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Lifetime coins earned (for stats).",
    )
    last_daily_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="UTC timestamp of the last /daily reward claim.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow,
    )

    def __repr__(self) -> str:
        return f"<Wallet user={self.user_id} coins={self.coins}>"


# ---------------------------------------------------------------------------
# Model: Castle  (القلعة)
# ---------------------------------------------------------------------------

class Castle(Base):
    """
    القلعة الخاصة بمستخدم داخل مجموعة بعينها.
    المستوى يرتفع بالتطوير؛ الوصول لمستوى 10 يمنح لقب الحاكم.
    """
    __tablename__ = "game_castles"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_castle_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="قلعة مجهولة",
        comment="اسم القلعة الذي يختاره المستخدم عند الإنشاء.",
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_upgraded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="آخر وقت تمّ فيه رفع مستوى القلعة.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    def __repr__(self) -> str:
        return f"<Castle user={self.user_id} chat={self.chat_id} level={self.level}>"


# ---------------------------------------------------------------------------
# Model: CastleResources  (موارد القلعة)
# ---------------------------------------------------------------------------

class CastleResources(Base):
    """
    مستودع موارد القلعة.
    الذهب هنا (gold) هو مورد داخل اللعبة ويُشتى بالعملات العامة بسعر 1:1.
    لا علاقة له بعملة المحفظة (Wallet.coins).
    """
    __tablename__ = "game_castle_resources"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_res_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    wood:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stone: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    food:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gold:  Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="ذهب القلعة — مورد لعبة مستقل عن Wallet.coins.",
    )

    def __repr__(self) -> str:
        return (
            f"<CastleResources user={self.user_id} "
            f"wood={self.wood} stone={self.stone} food={self.food} gold={self.gold}>"
        )


# ---------------------------------------------------------------------------
# Model: Barracks  (المعسكر)
# ---------------------------------------------------------------------------

class Barracks(Base):
    """
    المعسكر العسكري للمستخدم داخل المجموعة.
    يجب إنشاء المعسكر أولاً قبل شراء الجيش.
    كل 1000 جندي = نقطة قوة واحدة تُستخدم في المبارزات.
    """
    __tablename__ = "game_barracks"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_bar_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    soldiers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    power_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="نقاط القوة المحسوبة = soldiers // 1000. تُرفع بأمر تطوير الجيش.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    def __repr__(self) -> str:
        return f"<Barracks user={self.user_id} soldiers={self.soldiers} power={self.power_level}>"


# ---------------------------------------------------------------------------
# Model: ImmunityCard  (بطاقة الحصانة)
# ---------------------------------------------------------------------------

class ImmunityCard(Base):
    """
    بطاقات الحصانة التي يجمعها المستخدم.
    كل بطاقة = 24 ساعة حماية عند التفعيل.
    تُكتسب من التنقيب عن الكنز.
    """
    __tablename__ = "game_immunity"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_imm_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    cards: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="متى تنتهي الحصانة الفعّالة. None = غير مفعّلة.",
    )

    @property
    def is_active(self) -> bool:
        if self.active_until is None:
            return False
        return _utcnow() < self.active_until


# ---------------------------------------------------------------------------
# Model: TreasureHunt  (التنقيب عن الكنز — cooldown tracker)
# ---------------------------------------------------------------------------

class TreasureHunt(Base):
    """يتتبع آخر وقت تنقيب لكل مستخدم في كل مجموعة (cooldown ساعتان)."""
    __tablename__ = "game_treasure_hunts"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_dig_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    last_hunt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )


# ---------------------------------------------------------------------------
# Model: GlobalBattle  (المعركة الكبرى)
# ---------------------------------------------------------------------------

class GlobalBattle(Base):
    """
    جلسة المعركة الكبرى لمجموعة ما.
    يُنشئها المشرف؛ ينضم إليها المستخدمون خلال فترة التسجيل.
    """
    __tablename__ = "game_global_battles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registration_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="انتهاء فترة التسجيل؛ بعدها لا يُقبل انضمام جديد.",
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    participants: Mapped[list["BattleParticipant"]] = relationship(
        "BattleParticipant", back_populates="battle", cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# Model: BattleParticipant  (المشاركون في المعركة الكبرى)
# ---------------------------------------------------------------------------

class BattleParticipant(Base):
    __tablename__ = "game_battle_participants"
    __table_args__ = (UniqueConstraint("battle_id", "user_id", name="uq_bp_battle_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_global_battles.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    castle_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    army_power: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_power: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="castle_level * 10 + army_power — نقطة الترتيب النهائية.",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    battle: Mapped["GlobalBattle"] = relationship("GlobalBattle", back_populates="participants")


# ---------------------------------------------------------------------------
# Model: AllianceRequest  (طلبات التحالف للغارة)
# ---------------------------------------------------------------------------

class AllianceRequest(Base):
    __tablename__ = "game_alliance_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    requester_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requester_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    raid_target_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
        comment="الضحية المستهدفة بالغارة المشتركة (من قائمة الحكام).",
    )
    status: Mapped[AllianceStatus] = mapped_column(
        Enum(AllianceStatus), nullable=False, default=AllianceStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )


# ---------------------------------------------------------------------------
# Model: RulerTitle  (سجل ألقاب الحكام)
# ---------------------------------------------------------------------------

class RulerTitle(Base):
    """يُسجَّل هنا كل من فاز بلقب الحاكم في أي معركة كبرى."""
    __tablename__ = "game_ruler_titles"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_ruler_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_win_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )
