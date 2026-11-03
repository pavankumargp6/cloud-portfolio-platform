"""Auth dependencies for FastAPI — Phase 13.2.

Design:
  * get_auth_context_optional: reads the `agency_session` cookie, loads
    the live session row (via app.auth.sessions.load_session, which
    enforces revoked_at NULL, expires_at > now, and users.is_active=TRUE).
    Returns None if no valid session — no error. This is the single source
    of truth for "who is calling".

  * require_api_user: for JSON API routes. Returns a User. Raises 401 if
    no valid session. On state-changing methods (POST/PUT/PATCH/DELETE)
    ALSO enforces CSRF via the `X-CSRF-Token` header. This bundling is
    deliberate: an authenticated route that forgets CSRF would be silently
    vulnerable; bundling means the route must forget auth first, which is
    far more visible and produces 401 not silent CSRF bypass.

  * require_dashboard_user: for browser routes. Returns a User, or raises
    NotAuthenticatedRedirect (converted to 303 → /login?next=... by the
    handler installed in main.py). Same state-changing method CSRF check,
    but reads `_csrf_token` from the form body instead of the header.

  * install_exception_handlers: called once by main.py to wire the
    NotAuthenticatedRedirect → RedirectResponse conversion.

Reusable with any FastAPI app — no dependency on app.main.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.csrf import (
    SESSION_CSRF_FIELD,
    SESSION_CSRF_HEADER,
    verify_session_csrf,
)
from app.auth.sessions import SESSION_COOKIE_NAME, load_session
from app.db import get_db
from app.models.auth_session import AuthSession
from app.models.user import User


STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class AuthContext:
    """Resolved session + user for the current request."""
    user: User
    session: AuthSession


class NotAuthenticatedRedirect(Exception):
    """Raised by require_dashboard_user when no valid session exists.

    Not a user-facing exception — install_exception_handlers converts it to
    a 303 See Other redirect to `login_url`. Using an exception (rather than
    returning a RedirectResponse from the dependency) is required because
    FastAPI dependencies return data to the route, not responses to the
    client.
    """
    def __init__(self, login_url: str):
        self.login_url = login_url


def get_auth_context_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[AuthContext]:
    """Load the current session from the cookie if present and valid.

    Returns None for: missing cookie, unknown token, revoked session,
    expired session, or user is_active=FALSE. All those conditions look
    identical to the caller — no information about which one it was leaks.
    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    session = load_session(db, raw)
    if session is None:
        return None
    return AuthContext(user=session.user, session=session)


def require_auth_context(
    ctx: Optional[AuthContext] = Depends(get_auth_context_optional),
) -> AuthContext:
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return ctx


def _check_api_csrf(request: Request, ctx: AuthContext) -> None:
    candidate = request.headers.get(SESSION_CSRF_HEADER)
    if not candidate or not verify_session_csrf(
        candidate, ctx.session.csrf_token_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid or missing",
        )


def require_api_user(
    request: Request,
    ctx: AuthContext = Depends(require_auth_context),
) -> User:
    """API route dependency. 401 if unauthenticated.

    For state-changing methods, additionally requires `X-CSRF-Token`
    matching the session's CSRF hash — 403 otherwise. GET/HEAD/OPTIONS
    skip CSRF.
    """
    if request.method in STATE_CHANGING_METHODS:
        _check_api_csrf(request, ctx)
    request.state.user = ctx.user
    return ctx.user


async def require_dashboard_user(
    request: Request,
    ctx: Optional[AuthContext] = Depends(get_auth_context_optional),
) -> User:
    """Dashboard route dependency. Redirects to /login if unauthenticated.

    For state-changing methods, reads `_csrf_token` from the form body and
    verifies it against the session's CSRF hash. Starlette caches form
    parsing, so the route's own `await request.form()` (or FastAPI
    Form(...) parameters) sees the same parsed data.
    """
    if ctx is None:
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        raise NotAuthenticatedRedirect(
            login_url=f"/login?next={quote(next_path, safe='/')}"
        )

    if request.method in STATE_CHANGING_METHODS:
        form = await request.form()
        candidate = form.get(SESSION_CSRF_FIELD, "")
        if not verify_session_csrf(candidate, ctx.session.csrf_token_hash):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token invalid or missing",
            )
    request.state.user = ctx.user
    return ctx.user


def install_exception_handlers(app: FastAPI) -> None:
    """Wire NotAuthenticatedRedirect → 303 redirect on `app`."""

    @app.exception_handler(NotAuthenticatedRedirect)
    async def _handle_not_authenticated(
        request: Request, exc: NotAuthenticatedRedirect
    ) -> RedirectResponse:
        return RedirectResponse(url=exc.login_url, status_code=303)
