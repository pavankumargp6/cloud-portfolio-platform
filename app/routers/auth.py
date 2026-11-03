"""Auth routes — Phase 13.2.

  GET  /login   render login form, set pre-auth CSRF cookie
  POST /login   verify credentials, create DB session, set cookies
  POST /logout  revoke session, clear cookies

Design notes:

  * Generic error message on any credential failure — no account
    enumeration. Timing normalized by verifying against a dummy hash
    when the user doesn't exist, and by hashing inactive users' real
    password before rejecting them.

  * Pre-auth CSRF (login_csrf cookie + _login_csrf form field) uses the
    stateless double-submit pattern from app.auth.csrf. It's HttpOnly —
    JS never needs to read it, the value comes from server-side render.

  * Successful login creates an auth_sessions row via
    app.auth.sessions.create_session, then sets:
        agency_session (HttpOnly, 12h, Secure in prod)
        agency_csrf    (JS-readable so HTMX/API can echo via header)
    and clears login_csrf.

  * `next` param is validated against an allowlist. Protocol-relative
    (//evil.com), backslash-containing, and absolute URLs are all
    rejected in favour of /dashboard/.

  * Logout requires dashboard auth (which enforces CSRF on POST). If a
    user has no valid session, they can't "log out" of it — they were
    already logged out. That's correct behaviour.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.csrf import (
    LOGIN_CSRF_COOKIE,
    LOGIN_CSRF_FIELD,
    LOGIN_CSRF_LIFETIME_SECONDS,
    SESSION_CSRF_COOKIE,
    generate_csrf_token,
    verify_login_csrf,
)
from app.auth.dependencies import require_dashboard_user
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
    create_session,
    revoke_session_by_token,
)
from app.config import COOKIE_SECURE
from app.db import get_db
from app.models.user import User

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Dummy hash for timing normalization on unknown email. Computed once at
# import time (~100ms). Never matches a real password — the constant
# string is chosen to be obviously-wrong to any maintainer reading it.
_DUMMY_HASH = hash_password("dummy-password-never-matches-anyone")

_DEFAULT_REDIRECT = "/dashboard/"


def _safe_next(raw: str) -> str:
    """Return `raw` if it's a safe relative path, else the default."""
    if not raw:
        return _DEFAULT_REDIRECT
    if not raw.startswith("/"):
        return _DEFAULT_REDIRECT
    if raw.startswith("//"):
        return _DEFAULT_REDIRECT
    if "\\" in raw or "\n" in raw or "\r" in raw:
        return _DEFAULT_REDIRECT
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return _DEFAULT_REDIRECT
    return raw


def _authenticate(db: Session, email: str, password: str) -> Optional[User]:
    """Return the User for valid credentials, or None.

    Every failure mode (unknown email, wrong password, inactive user)
    returns None in comparable time:

      * unknown email  → dummy hash verified against supplied password
      * wrong password → real hash verified, returns False
      * inactive user  → real hash verified, then is_active rejected

    No branch returns faster than a wrong-password-active-user, so an
    attacker cannot distinguish "no such account" from "wrong password"
    from "disabled account" by timing.
    """
    email_norm = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email_norm))

    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None

    if not verify_password(password, user.password_hash):
        return None

    if not user.is_active:
        return None

    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login form. Reuse existing CSRF cookie if present."""
    existing = request.cookies.get(LOGIN_CSRF_COOKIE)
    csrf = existing or generate_csrf_token()
    next_param = request.query_params.get("next", "")

    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf, "next": next_param, "error": None},
    )

    if not existing:
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            csrf,
            max_age=LOGIN_CSRF_LIFETIME_SECONDS,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            path="/",
        )
    return response


@router.post("/login")
async def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(default=""),
):
    """Verify credentials and, on success, create a session."""
    # The pre-auth CSRF field is named "_login_csrf" — Pydantic rejects
    # leading underscores in generated body-model field names, so it is read
    # from the parsed form rather than declared as a Form(...) parameter.
    # Starlette caches request.form(); FastAPI has already parsed it for the
    # other Form(...) fields, so this returns the same cached object.
    form = await request.form()
    login_csrf = form.get(LOGIN_CSRF_FIELD, "")

    # --- CSRF: double-submit ---
    cookie_csrf = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not verify_login_csrf(cookie_csrf, login_csrf):
        log.warning("login_csrf_rejected", extra={"event": "login_csrf_rejected"})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid or missing",
        )

    # --- Credentials ---
    user = _authenticate(db, email, password)

    if user is None:
        log.info(
            "login_failed",
            extra={
                "event": "login_failed",
                "ip": request.client.host if request.client else None,
            },
        )
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": cookie_csrf,
                "next": next,
                "error": "Invalid email or password",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        return response

    # --- Create session ---
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    _, raw_sess, raw_csrf = create_session(db, user, ip_address=ip, user_agent=ua)

    log.info(
        "login_success",
        extra={
            "event": "login_success",
            "user_id": user.id,
            "agency_id": user.agency_id,
            "ip": ip,
        },
    )

    # --- Redirect + cookies ---
    target = _safe_next(next)
    response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

    max_age = int(SESSION_LIFETIME.total_seconds())

    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_sess,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        SESSION_CSRF_COOKIE,
        raw_csrf,
        max_age=max_age,
        httponly=False,  # JS must read this for HTMX/API headers
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")

    return response


@router.post("/logout")
def logout_submit(
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    """Revoke the current session and clear auth cookies."""
    raw_sess = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_sess:
        revoke_session_by_token(db, raw_sess)

    log.info(
        "logout",
        extra={"event": "logout", "user_id": user.id},
    )

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(SESSION_CSRF_COOKIE, path="/")
    return response
