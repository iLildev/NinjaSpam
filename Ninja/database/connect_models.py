"""
database/connect_models.py — ORM model for the PM connection system.

Separated from plugins/connect.py so that init_db can register the table
before the plugin loader imports the plugin (avoiding duplicate-table errors).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.engine import Base


class UserConnection(Base):
    """Stores the active PM-to-group connection for a user."""

    __tablename__ = "user_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
        comment="Telegram user_id of the connected user.",
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Telegram chat_id of the group this user is connected to.",
    )
    chat_title: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
        comment="Cached group title for display.",
    )

    def __repr__(self) -> str:
        return f"<UserConnection user={self.user_id} → chat={self.chat_id}>"
