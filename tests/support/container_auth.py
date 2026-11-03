"""Container-side auth client for tests that run via `docker compose exec api`.

Distinct from tests/support/auth_client.py because:

  - Seeds user via direct DB (SessionLocal), not subprocess `docker compose
    exec`. The `docker` binary is not installed inside the api container.
  - Hits localhost:8000 (container-local), not localhost:8080 (host nginx).
  - Uses a distinct test user (container-test@) so host and container test
    runs don't collide if they ever overlap.

Interface mirrors auth_client.py: api_request, post_form, plus a get_html
helper for dashboard page checks. Tests import whichever is appropriate
based on where they run.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

CONTAINER_BASE_URL = "http://localhost:8000"
TEST_USER_EMAIL = "container-test@example.com"
TEST_USER_PASSWORD = "correct horse battery staple"

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_ensured = False
_client = None


def _ensure_user() -> None:
    global _ensured
    if _ensured:
        return
    from app.db import SessionLocal
    from app.models.agency import Agency
    from app.models.user import User
    from app.auth.passwords import hash_password
    from sqlalchemy import func

    db = SessionLocal()
    try:
        agency = db.query(Agency).filter_by(name="Default Agency").first()
        assert agency is not None, "Default Agency missing"
        existing = (
            db.query(User)
            .filter(func.lower(User.email) == TEST_USER_EMAIL)
            .first()
        )
        if existing is None:
            db.add(User(
                agency_id=agency.id,
                email=TEST_USER_EMAIL,
                name="Container Test",
                password_hash=hash_password(TEST_USER_PASSWORD),
                is_active=True,
            ))
            db.commit()
    finally:
        db.close()
    _ensured = True


class _Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.csrf: str | None = None
        self._logged_in = False

    def _cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)

    def _login(self) -> None:
        if self._logged_in:
            return
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        r = urllib.request.Request(CONTAINER_BASE_URL + "/login")
        with opener.open(r, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
        m = re.search(r'name="_login_csrf"\s+value="([^"]+)"', body)
        if not m:
            raise RuntimeError(
                f"no _login_csrf in /login body (status {resp.status})"
            )
        csrf = m.group(1)

        data = urllib.parse.urlencode({
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD,
            "_login_csrf": csrf,
        }).encode()
        r = urllib.request.Request(
            CONTAINER_BASE_URL + "/login", data=data, method="POST"
        )
        r.add_header("Content-Type", "application/x-www-form-urlencoded")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener_nr = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )
        try:
            opener_nr.open(r, timeout=10)
            raise RuntimeError("login POST did not redirect")
        except urllib.error.HTTPError as e:
            if e.code != 303:
                raise RuntimeError(f"login failed: HTTP {e.code}")

        self.csrf = self._cookie("agency_csrf")
        if not self.csrf:
            raise RuntimeError(
                f"no agency_csrf cookie; got {[c.name for c in self.jar]}"
            )
        self._logged_in = True

    def request(self, method: str, path: str, payload: dict | None = None, *, form: bool = False):
        self._login()
        method = method.upper()
        headers: dict[str, str] = {}
        data: bytes | None = None

        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"

        if method in STATE_CHANGING:
            headers["X-CSRF-Token"] = self.csrf  # type: ignore[assignment]

        r = urllib.request.Request(
            CONTAINER_BASE_URL + path, data=data, headers=headers, method=method
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        try:
            with opener.open(r, timeout=10) as resp:
                body = resp.read()
                try:
                    parsed = json.loads(body) if body else None
                except Exception:
                    parsed = body.decode("utf-8", "replace") if body else None
                return resp.status, parsed
        except urllib.error.HTTPError as e:
            body = e.read()
            e.close()
            try:
                parsed = json.loads(body) if body else None
            except Exception:
                parsed = body.decode("utf-8", "replace") if body else None
            return e.code, parsed


def _get_client() -> _Client:
    global _client
    if _client is None:
        _ensure_user()
        _client = _Client()
        _client._login()
    return _client


def api_request(path: str, method: str = "GET", payload: dict | None = None):
    return _get_client().request(method, path, payload)


def post_form(path: str, data: dict):
    merged = dict(data)
    merged.setdefault("_csrf_token", _get_client().csrf)
    return _get_client().request("POST", path, merged, form=True)


def get_html(path: str):
    """GET that returns (status, body) — body is HTML str for dashboard pages."""
    return _get_client().request("GET", path)
