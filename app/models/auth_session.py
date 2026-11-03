"""AuthSession model — a DB-backed, opaque-token session.

The raw session token never touches the database. Only its SHA-256
hex digest is stored (`token_hash`). The session-bound CSRF token is
handled the same way (`csrf_token_hash`).

A session is considered valid only when all of the following hold:
  * the row exists,
  * revoked_at IS NULL,
  * expires_at > NOW(),
  * the joined users.is_active is TRUE.

The last condition is what makes disabling a user take immediate effect
on existing sessions (Phase 13.2 spec, "Disabled account"). The lookup
query in app.auth.sessions will join to users and filter on is_active —
it is not enough to check revoked_at alone.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest (64 chars) of the raw session token.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    # SHA-256 hex digest (64 chars) of the session-bound CSRF token.
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    user: Mapped["User"] = relationship(back_populates="sessions")
