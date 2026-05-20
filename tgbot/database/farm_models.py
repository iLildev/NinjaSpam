"""
database/farm_models.py — ORM models for the Farm game (لعبة المزرعة).

Schema:
  Farm          — بيانات مزرعة المستخدم (مستوى / عدد الأراضي)
  FarmPlot      — قطعة أرض واحدة داخل المزرعة (محصول + وقت النضج)
  FarmInventory — مخزون المحاصيل التي جُمعت ولم تُباع بعد
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

class CropType(str, enum.Enum):
    WHEAT    = "wheat"    # قمح
    BARLEY   = "barley"   # شعير
    TOMATO   = "tomato"   # طماطم
    APPLE    = "apple"    # تفاح
    GRAPE    = "grape"    # عنب


# ---------------------------------------------------------------------------
# Model: Farm  (المزرعة)
# ---------------------------------------------------------------------------

class Farm(Base):
    """
    المزرعة الخاصة بمستخدم داخل مجموعة.
    المستوى يحدد عدد قطع الأرض المتاحة.
    """
    __tablename__ = "game_farms"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_farm_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="مستوى المزرعة (1-5). كل مستوى يُضيف أراضي جديدة.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow,
    )

    plots: Mapped[list["FarmPlot"]] = relationship(
        "FarmPlot", back_populates="farm", cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# Model: FarmPlot  (قطعة أرض)
# ---------------------------------------------------------------------------

class FarmPlot(Base):
    """
    قطعة أرض واحدة داخل المزرعة.
    يمكن زراعتها بمحصول واحد في كل مرة.
    """
    __tablename__ = "game_farm_plots"
    __table_args__ = (
        UniqueConstraint("farm_id", "plot_number", name="uq_plot_farm_num"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_farms.id", ondelete="CASCADE"), nullable=False,
    )
    plot_number: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="رقم القطعة داخل المزرعة (1-based).",
    )
    crop: Mapped[Optional[CropType]] = mapped_column(
        Enum(CropType), nullable=True,
        comment="نوع المحصول المزروع. None = الأرض فارغة.",
    )
    planted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="وقت نضج المحصول وإمكانية الحصاد.",
    )
    is_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    farm: Mapped["Farm"] = relationship("Farm", back_populates="plots")

    @property
    def can_harvest(self) -> bool:
        if not self.crop or not self.ready_at:
            return False
        return _utcnow() >= self.ready_at


# ---------------------------------------------------------------------------
# Model: FarmInventory  (مخزون المحاصيل)
# ---------------------------------------------------------------------------

class FarmInventory(Base):
    """
    مخزون المحاصيل التي حصدها المستخدم ولم يبعها بعد.
    البيع يحوّل المحاصيل إلى عملات في المحفظة العامة.
    """
    __tablename__ = "game_farm_inventory"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_inv_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    wheat:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    barley: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tomato: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    apple:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grape:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
