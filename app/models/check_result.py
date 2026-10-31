"""Check result: one row per probe. High-volume table."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(64))
    ssl_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ssl_issuer: Mapped[Optional[str]] = mapped_column(String(255))
    final_url: Mapped[Optional[str]] = mapped_column(String(2048))
    redirect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
