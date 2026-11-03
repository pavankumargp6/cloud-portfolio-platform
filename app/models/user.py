"""User model — an authenticated operator within an agency.

Each user belongs to exactly one agency. Multiple users per agency are
allowed. Email is stored lowercase and enforced unique case-insensitively
at the database level via a functional unique index on lower(email)
(see migration 0007).

The reverse `Agency.users` relationship is intentionally omitted here:
it will be added in Phase 13.3 alongside tenant-scoped query enforcement.
Keeping it out of Phase 13.2 preserves the phase boundary.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agency_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agencies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("TRUE"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    agency: Mapped["Agency"] = relationship()
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
