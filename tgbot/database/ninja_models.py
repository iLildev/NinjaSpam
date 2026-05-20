"""
database/ninja_models.py — نماذج لعبة النينجا (اغتيال + اختطاف).

الجداول:
  ninja_profiles   — ملف كل لاعب (مستوى، XP، صحة، إحصائيات)
  kidnap_records   — سجلات الاختطاف الجارية والمنتهية
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.engine import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NinjaLevel(str, enum.Enum):
    STUDENT = "student"    # مبتدئ       0–49 XP
    TRAINEE = "trainee"    # متدرب      50–149 XP
    NINJA   = "ninja"      # نينجا     150–349 XP
    SHADOW  = "shadow"     # الظل      350–699 XP
    MASTER  = "master"     # ماستر     700–1199 XP
    LEGEND  = "legend"     # أسطورة    1200+ XP

    @property
    def arabic(self) -> str:
        return {
            NinjaLevel.STUDENT: "مبتدئ 🥷",
            NinjaLevel.TRAINEE: "متدرب ⚔️",
            NinjaLevel.NINJA:   "نينجا 🗡",
            NinjaLevel.SHADOW:  "الظل 🌑",
            NinjaLevel.MASTER:  "ماستر 🔥",
            NinjaLevel.LEGEND:  "أسطورة 💀",
        }[self]

    @property
    def power(self) -> int:
        """قوة الهجوم/الدفاع الأساسية."""
        return {
            NinjaLevel.STUDENT: 10,
            NinjaLevel.TRAINEE: 20,
            NinjaLevel.NINJA:   35,
            NinjaLevel.SHADOW:  50,
            NinjaLevel.MASTER:  70,
            NinjaLevel.LEGEND:  95,
        }[self]


class KidnapStatus(str, enum.Enum):
    ACTIVE   = "active"    # جارٍ
    RANSOMED = "ransomed"  # دُفعت الفدية
    RESCUED  = "rescued"   # أُنقذ
    ESCAPED  = "escaped"   # هرب بنفسه
    EXPIRED  = "expired"   # انتهت المدة وأُفرج عنه تلقائياً


# XP مطلوب لكل مستوى
LEVEL_XP: dict[NinjaLevel, int] = {
    NinjaLevel.STUDENT: 0,
    NinjaLevel.TRAINEE: 50,
    NinjaLevel.NINJA:   150,
    NinjaLevel.SHADOW:  350,
    NinjaLevel.MASTER:  700,
    NinjaLevel.LEGEND:  1200,
}

LEVEL_ORDER: list[NinjaLevel] = [
    NinjaLevel.STUDENT,
    NinjaLevel.TRAINEE,
    NinjaLevel.NINJA,
    NinjaLevel.SHADOW,
    NinjaLevel.MASTER,
    NinjaLevel.LEGEND,
]


def xp_to_level(xp: int) -> NinjaLevel:
    """احسب المستوى من مجموع XP."""
    current = NinjaLevel.STUDENT
    for lvl in LEVEL_ORDER:
        if xp >= LEVEL_XP[lvl]:
            current = lvl
    return current


def xp_to_next(xp: int) -> tuple[int, int]:
    """(XP المتبقي للمستوى التالي, حد المستوى التالي)."""
    for i, lvl in enumerate(LEVEL_ORDER):
        if xp < LEVEL_XP[lvl]:
            return LEVEL_XP[lvl] - xp, LEVEL_XP[lvl]
    return 0, LEVEL_XP[NinjaLevel.LEGEND]


# ---------------------------------------------------------------------------
# Model: NinjaProfile
# ---------------------------------------------------------------------------

class NinjaProfile(Base):
    __tablename__ = "ninja_profiles"

    user_id:  Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id:  Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64),  nullable=True)
    first_name: Mapped[str]         = mapped_column(String(128), nullable=False, default="")

    # تقدم اللاعب
    xp:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[NinjaLevel] = mapped_column(
        Enum(NinjaLevel), nullable=False, default=NinjaLevel.STUDENT
    )

    # الصحة (3 كحد أقصى، تتجدد 1 كل 6 ساعات)
    health:          Mapped[int]              = mapped_column(Integer, nullable=False, default=3)
    last_health_regen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # إحصائيات
    kills:             Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deaths:            Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_attacks:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kidnaps_done:      Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kidnaps_survived:  Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rescues:           Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # حالة الاختطاف
    is_kidnapped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<NinjaProfile {self.user_id}@{self.chat_id} lvl={self.level}>"


# ---------------------------------------------------------------------------
# Model: KidnapRecord
# ---------------------------------------------------------------------------

class KidnapRecord(Base):
    __tablename__ = "kidnap_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    kidnapper_id:   Mapped[int] = mapped_column(BigInteger, nullable=False)
    kidnapper_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    victim_id:   Mapped[int] = mapped_column(BigInteger, nullable=False)
    victim_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    ransom_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    status: Mapped[KidnapStatus] = mapped_column(
        Enum(KidnapStatus), nullable=False, default=KidnapStatus.ACTIVE
    )

    kidnapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<KidnapRecord #{self.id} {self.kidnapper_id}→{self.victim_id} {self.status}>"
