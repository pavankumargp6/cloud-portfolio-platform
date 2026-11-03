"""Reusable authenticated HTTP client for tests.

Uses the real login flow (GET /login → POST /login) — no test-only bypass.
The `agency_csrf` cookie is captured after login and auto-injected as
`X-CSRF-Token` on state-changing methods, so tests don't need to think
about CSRF.

Two entry points:

  AuthClient:           explicit instance, useful for lifecycle tests
  api_request(...):     drop-in replacement for the old unauthenticated
                        helper — returns (status, body_or_none)
  api_request_anon(..): deliberately unauthenticated, for /health and
                        401 regression tests
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from tests.support.test_user import (
    TEST_USER_EMAIL,
    TEST_USER_PASSWORD,
    ensure_test_user,
)

BASE_URL = "http://localhost:8080"

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AuthClient:
    """Cookie-jar-backed HTTP client that logs in on first request."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        email: str = TEST_USER_EMAIL,
        password: str = TEST_USER_PASSWORD,
    ) -> None:
        self.base_url = base_url
        self.email = email
        self.password = password
        self.jar = http.cookiejar.CookieJar()
        self.csrf: str | None = None
        self._logged_in = False

    def _cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)

    def _login(self) -> None:
        if self._logged_in:
            return
        ensure_test_user()

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

        # 1. GET /login — fetch pre-auth CSRF token
        r = urllib.request.Request(self.base_url + "/login")
        with opener.open(r, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
        m = re.search(r'name="_login_csrf"\s+value="([^"]+)"', body)
        if not m:
            raise RuntimeError(
                "Could not find _login_csrf in /login body. "
                f"Status was {resp.status}, body starts: {body[:200]!r}"
            )
        login_csrf = m.group(1)

        # 2. POST /login — should redirect 303 with Set-Cookie
        data = urllib.parse.urlencode(
            {
                "email": self.email,
                "password": self.password,
                "_login_csrf": login_csrf,
            }
        ).encode()
        r = urllib.request.Request(
            self.base_url + "/login", data=data, method="POST"
        )
        r.add_header("Content-Type", "application/x-www-form-urlencoded")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener_nr = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )
        try:
            opener_nr.open(r, timeout=10)
            raise RuntimeError(
                "Login POST did not redirect — expected 303, got 200. "
                "Check credentials or CSRF handling."
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 303:
                body_text = exc.read().decode("utf-8", "replace")
                raise RuntimeError(
                    f"Login failed: HTTP {exc.code}. Body: {body_text[:200]}"
                ) from exc

        self.csrf = self._cookie("agency_csrf")
        if not self.csrf:
            raise RuntimeError(
                "Login succeeded but no agency_csrf cookie. "
                f"Cookies present: {[c.name for c in self.jar]}"
            )
        if not self._cookie("agency_session"):
            raise RuntimeError(
                "Login succeeded but no agency_session cookie. "
                f"Cookies present: {[c.name for c in self.jar]}"
            )
        self._logged_in = True

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        form: bool = False,
    ) -> tuple[int, dict | str | None]:
        """Send an authenticated request. Returns (status, parsed_body_or_none)."""
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
            self.base_url + path, data=data, headers=headers, method=method
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        try:
            with opener.open(r, timeout=10) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            exc.close()
            try:
                parsed = json.loads(body) if body else None
            except Exception:
                parsed = body.decode("utf-8", "replace") if body else None
            return exc.code, parsed

    # --- convenience wrappers -------------------------------------------------
    def get(self, path: str) -> tuple[int, dict | str | None]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict) -> tuple[int, dict | str | None]:
        return self.request("POST", path, payload)

    def patch(self, path: str, payload: dict) -> tuple[int, dict | str | None]:
        return self.request("PATCH", path, payload)

    def delete(self, path: str) -> tuple[int, dict | str | None]:
        return self.request("DELETE", path)

    def post_form(self, path: str, form_data: dict) -> tuple[int, dict | str | None]:
        """Dashboard form submission (x-www-form-urlencoded + _csrf_token)."""
        merged = dict(form_data)
        merged.setdefault("_csrf_token", self.csrf)
        return self.request("POST", path, merged, form=True)


# --- module-level singletons ------------------------------------------------

_default_client: AuthClient | None = None


def api_request(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict | str | None]:
    """Authenticated drop-in for the old `api_request` helper."""
    global _default_client
    if _default_client is None:
        _default_client = AuthClient()

    upper = method.upper()
    if upper == "GET":
        return _default_client.get(path)
    if upper == "POST":
        return _default_client.post(path, payload or {})
    if upper == "PATCH":
        return _default_client.patch(path, payload or {})
    if upper == "DELETE":
        return _default_client.delete(path)
    return _default_client.request(method, path, payload)


def api_request_anon(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict | str | None]:
    """Anonymous request — for /health and 401 regression tests."""
    headers: dict[str, str] = {}
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    r = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        try:
            parsed = json.loads(body) if body else None
        except Exception:
            parsed = body.decode("utf-8", "replace") if body else None
        return exc.code, parsed
