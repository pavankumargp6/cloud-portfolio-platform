"""CSRF protection — Phase 13.2.

Two distinct mechanisms for two distinct lifecycle stages:

1. Pre-auth CSRF (login form)
   The login page is served before any session exists, so a session-bound
   token is impossible. Use the stateless double-submit pattern:
     - server sets a short-lived `login_csrf` cookie with a random token,
     - the same token is embedded as a hidden form field `_login_csrf`,
     - on POST /auth/login, the server compares cookie vs form with a
       constant-time equality check.
   No server-side storage; the token simply must match itself.

2. Post-auth CSRF (session-bound)
   Once a session exists, the raw CSRF token is delivered in a JS-readable
   `agency_csrf` cookie. Clients echo it back via `X-CSRF-Token` (JSON API
   and HTMX) or `_csrf_token` (dashboard forms). The server hashes the
   candidate and constant-time-compares it against
   `auth_sessions.csrf_token_hash`. Revocation therefore invalidates the
   CSRF token together with the session.

`agency_session` is HttpOnly. `agency_csrf` is deliberately NOT HttpOnly —
JS on the page must be able to read it to echo it back in a header. This
is the standard double-submit + server-hash pattern; it does not weaken
XSS resistance meaningfully because an attacker with XSS already has
full control of the page.
"""

from __future__ import annotations

import secrets

from app.auth.sessions import hash_token

# Pre-auth (login form)
LOGIN_CSRF_COOKIE = "login_csrf"
LOGIN_CSRF_FIELD = "_login_csrf"
LOGIN_CSRF_LIFETIME_SECONDS = 600  # 10 minutes — plenty for a login form

# Post-auth (session-bound)
SESSION_CSRF_COOKIE = "agency_csrf"  # JS-readable
SESSION_CSRF_HEADER = "X-CSRF-Token"
SESSION_CSRF_FIELD = "_csrf_token"


def generate_csrf_token() -> str:
    """Return a fresh 256-bit CSRF token, URL-safe base64 encoded."""
    return secrets.token_urlsafe(32)


def verify_login_csrf(cookie_value: str, form_value: str) -> bool:
    """Stateless double-submit check for the login form.

    Both values must be present and identical. Uses secrets.compare_digest
    to avoid a timing oracle.
    """
    if not cookie_value or not form_value:
        return False
    return secrets.compare_digest(cookie_value, form_value)


def verify_session_csrf(candidate: str, stored_hash: str) -> bool:
    """Compare a candidate CSRF token against the stored session hash.

    `stored_hash` is the value from `auth_sessions.csrf_token_hash`.
    Returns False on empty inputs or mismatch.
    """
    if not candidate or not stored_hash:
        return False
    return secrets.compare_digest(hash_token(candidate), stored_hash)
