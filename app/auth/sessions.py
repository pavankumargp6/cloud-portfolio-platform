"""Database-backed session lifecycle — Phase 13.2.

Design:
  * Opaque 256-bit tokens, generated with `secrets.token_urlsafe(32)`.
  * Only the SHA-256 hex digest of the raw token is stored (`token_hash`).
    The raw value never touches the database; loss of a DB dump therefore
    does not yield working session credentials.
  * Absolute 12-hour lifetime. No sliding extension.
  * Revocation is a `revoked_at` timestamp — kept for auditability, never
    deleted eagerly.
  * Lookup joins `users` and enforces `users.is_active = TRUE`. This is
    what makes disabling a user take effect immediately on existing
    sessions (spec: "Disabled account — existing sessions immediately
    become invalid"). Checking `revoked_at` alone is insufficient.
  * Session-bound CSRF: `csrf_token_hash` is stored alongside
    `token_hash`. Revocation invalidates both, atomically.

Callers (the auth router, in Patch 4) are expected to:
  * call `create_session` on successful login,
  * pass the raw tokens into `response.set_cookie(...)`,
  * discard the raw values afterwards.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.auth_session import AuthSession
from app.models.user import User

log = logging.getLogger(__name__)

# Absolute session lifetime — spec: "12 hours", no sliding extension.
SESSION_LIFETIME = timedelta(hours=12)

# Cookie name — spec: "agency_session"
SESSION_COOKIE_NAME = "agency_session"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of `raw_token`. Used for both session
    tokens and session-bound CSRF tokens; the two are stored in different
    columns but hashed the same way."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """Return a fresh 256-bit token, URL-safe base64 encoded (43 chars)."""
    return secrets.token_urlsafe(32)


def create_session(
    db: Session,
    user: User,
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[AuthSession, str, str]:
    """Create a new session row for `user`.

    Returns (session_row, raw_session_token, raw_csrf_token).
    The raw tokens must be handed to `response.set_cookie` and never logged,
    stored, or returned in any API response body.
    """
    raw_session = generate_token()
    raw_csrf = generate_token()
    now = _utcnow()

    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(raw_session),
        csrf_token_hash=hash_token(raw_csrf),
        expires_at=now + SESSION_LIFETIME,
        ip_address=ip_address,
        user_agent=user_agent[:500] if user_agent else None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    log.info(
        "session_created",
        extra={
            "event": "session_created",
            "user_id": user.id,
            "agency_id": user.agency_id,
            "session_id": session.id,
        },
    )
    return session, raw_session, raw_csrf


def load_session(db: Session, raw_token: str) -> Optional[AuthSession]:
    """Return the live AuthSession for `raw_token`, or None.

    A session is valid iff ALL hold:
      * a row exists with the matching token_hash,
      * revoked_at IS NULL,
      * expires_at > now,
      * the joined users.is_active is TRUE.

    The user is eager-loaded so callers can read `session.user.agency_id`
    without an extra round-trip.
    """
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    stmt = (
        select(AuthSession)
        .join(User, AuthSession.user_id == User.id)
        .options(joinedload(AuthSession.user))
        .where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > _utcnow(),
            User.is_active.is_(True),
        )
    )
    return db.scalar(stmt)


def revoke_session(db: Session, session: AuthSession) -> None:
    """Mark `session` as revoked. Idempotent."""
    if session.revoked_at is not None:
        return
    session.revoked_at = _utcnow()
    db.commit()
    log.info(
        "session_revoked",
        extra={
            "event": "session_revoked",
            "session_id": session.id,
            "user_id": session.user_id,
        },
    )


def revoke_session_by_token(db: Session, raw_token: str) -> bool:
    """Revoke by raw token — used at logout time when the caller has the
    cookie value but may not have a loaded AuthSession (e.g. early return).

    Returns True if a live session was revoked, False otherwise.
    """
    if not raw_token:
        return False
    token_hash = hash_token(raw_token)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
        )
    )
    if session is None:
        return False
    revoke_session(db, session)
    return True
