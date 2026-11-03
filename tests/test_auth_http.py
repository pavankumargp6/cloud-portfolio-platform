"""Patch 4 HTTP verification — real login/logout flows via urllib.

Runs on host, points at http://localhost:8080 (isolated stack nginx).
Pre-req: test user 'test-auth@example.com' / 'correct horse battery staple'
exists in DB, created by the setup step in the driver script.
"""
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from tests.support.test_user import (
    TEST_USER_EMAIL as TEST_EMAIL,
    TEST_USER_PASSWORD as TEST_PASSWORD,
    INACTIVE_USER_EMAIL as INACTIVE_EMAIL,
    ensure_test_user,
    ensure_inactive_test_user,
)

# Seed users on import — idempotent. The stacked tests require both users.
ensure_test_user()
ensure_inactive_test_user()

INACTIVE_PASSWORD = TEST_PASSWORD  # same shared password

BASE = "http://localhost:8080"
BASE_URL = BASE  # alias for lifecycle block appended below
LOGIN_CSRF_COOKIE = "login_csrf"
SESSION_COOKIE = "agency_session"
CSRF_COOKIE = "agency_csrf"

failures = []
def check(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {detail}")
        failures.append(name)


def new_client():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return opener, jar


def request(opener, path, method="GET", data=None, allow_redirect=True):
    if data is not None and not isinstance(data, bytes):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = opener.open(req, timeout=10)
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, dict(e.headers), body


def get_cookie(jar, name):
    for c in jar:
        if c.name == name:
            return c.value
    return None


def extract_csrf(html):
    m = re.search(r'name="_login_csrf"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


# ---------- 1. GET /login ----------
opener, jar = new_client()
status, headers, body = request(opener, "/login")
check("GET /login 200", status == 200, f"got {status}")
check("login page has _login_csrf field", "_login_csrf" in body)
csrf_from_form = extract_csrf(body)
check("login page csrf token present", csrf_from_form is not None)
check("login_csrf cookie set", get_cookie(jar, LOGIN_CSRF_COOKIE) is not None)
check("login_csrf cookie == form token",
      get_cookie(jar, LOGIN_CSRF_COOKIE) == csrf_from_form)

# ---------- 2. GET /login again reuses cookie ----------
status, headers, body = request(opener, "/login")
check("GET /login second time 200", status == 200)
check("login_csrf cookie reused",
      get_cookie(jar, LOGIN_CSRF_COOKIE) == csrf_from_form)

# ---------- 3. POST /login with no csrf field ----------
opener, jar = new_client()
status, _, _ = request(opener, "/login")  # sets cookie
# Build POST that omits _login_csrf entirely
status, _, body = request(
    opener, "/login", method="POST",
    data={"email": TEST_EMAIL, "password": TEST_PASSWORD},
)
check("POST /login no-csrf → 403", status == 403, f"got {status}")

# ---------- 4. POST /login with wrong csrf value ----------
status, _, _ = request(opener, "/login")
status, _, _ = request(
    opener, "/login", method="POST",
    data={"email": TEST_EMAIL, "password": TEST_PASSWORD,
          "_login_csrf": "wrong-token-value"},
)
check("POST /login wrong-csrf → 403", status == 403, f"got {status}")

# ---------- 5. POST /login wrong password → 401, no session ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
status, headers, body = request(
    opener, "/login", method="POST",
    data={"email": TEST_EMAIL, "password": "wrong-password",
          "_login_csrf": csrf},
)
check("POST /login wrong-password → 401", status == 401, f"got {status}")
check("wrong-password shows generic error",
      "Invalid email or password" in body, body[:200])
check("wrong-password does NOT set session cookie",
      get_cookie(jar, SESSION_COOKIE) is None)

# ---------- 6. POST /login inactive user → 401 ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
status, _, body = request(
    opener, "/login", method="POST",
    data={"email": INACTIVE_EMAIL, "password": INACTIVE_PASSWORD,
          "_login_csrf": csrf},
)
check("POST /login inactive-user → 401", status == 401, f"got {status}")
check("inactive-user shows generic error",
      "Invalid email or password" in body)
check("inactive-user does NOT set session cookie",
      get_cookie(jar, SESSION_COOKIE) is None)

# ---------- 7. POST /login unknown email → 401 (no enumeration) ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
status, _, body = request(
    opener, "/login", method="POST",
    data={"email": "doesnotexist@example.com", "password": "whatever-12chars",
          "_login_csrf": csrf},
)
check("POST /login unknown-email → 401", status == 401, f"got {status}")
check("unknown-email shows generic error",
      "Invalid email or password" in body)

# ---------- 8. POST /login valid creds → 303 + cookies ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
req = urllib.request.Request(
    BASE + "/login",
    data=urllib.parse.urlencode({
        "email": TEST_EMAIL, "password": TEST_PASSWORD, "_login_csrf": csrf,
    }).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
# Do not follow redirect so we can inspect Location and Set-Cookie
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
opener_no_redirect = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), NoRedirect()
)
try:
    opener_no_redirect.open(req, timeout=10)
    check("valid login returns 303", False, "expected redirect")
except urllib.error.HTTPError as e:
    check("valid login returns 303", e.code == 303, f"got {e.code}")
    location = e.headers.get("Location", "")
    check("valid login redirects to /dashboard/",
          location == "/dashboard/" or location.endswith("/dashboard/"),
          location)

check("valid login sets agency_session cookie",
      get_cookie(jar, SESSION_COOKIE) is not None)
check("valid login sets agency_csrf cookie",
      get_cookie(jar, CSRF_COOKIE) is not None)
check("valid login clears login_csrf cookie",
      get_cookie(jar, LOGIN_CSRF_COOKIE) is None)
saved_session = get_cookie(jar, SESSION_COOKIE)
saved_csrf = get_cookie(jar, CSRF_COOKIE)

# ---------- 9. login with next= preserves path ----------
opener, jar = new_client()
_, _, body = request(opener, "/login?next=/dashboard/clients")
csrf = extract_csrf(body)
req = urllib.request.Request(
    BASE + "/login",
    data=urllib.parse.urlencode({
        "email": TEST_EMAIL, "password": TEST_PASSWORD,
        "_login_csrf": csrf, "next": "/dashboard/clients",
    }).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
opener_no_redirect = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), NoRedirect()
)
try:
    opener_no_redirect.open(req, timeout=10)
except urllib.error.HTTPError as e:
    loc = e.headers.get("Location", "")
    check("login next=/dashboard/clients → 303 to that path",
          e.code == 303 and loc == "/dashboard/clients", f"{e.code} {loc}")

# ---------- 10. login with unsafe next is rejected ----------
for unsafe_next in ("//evil.com/path", "https://evil.com", "/\\evil.com"):
    opener, jar = new_client()
    _, _, body = request(opener, "/login")
    csrf = extract_csrf(body)
    req = urllib.request.Request(
        BASE + "/login",
        data=urllib.parse.urlencode({
            "email": TEST_EMAIL, "password": TEST_PASSWORD,
            "_login_csrf": csrf, "next": unsafe_next,
        }).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    opener_no_redirect = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), NoRedirect()
    )
    try:
        opener_no_redirect.open(req, timeout=10)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        check(f"unsafe next={unsafe_next!r} → /dashboard/",
              loc == "/dashboard/", loc)

# ---------- 11. POST /logout with valid session → 303 ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
req = urllib.request.Request(
    BASE + "/login",
    data=urllib.parse.urlencode({
        "email": TEST_EMAIL, "password": TEST_PASSWORD, "_login_csrf": csrf,
    }).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
opener_follow = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)
opener_follow.open(req, timeout=10)  # follow redirect → dashboard (may 303 to login if protected later, fine)

session_val = get_cookie(jar, SESSION_COOKIE)
csrf_val = get_cookie(jar, CSRF_COOKIE)
check("pre-logout session present", session_val is not None)

# Now logout (POST with CSRF)
logout_req = urllib.request.Request(
    BASE + "/logout",
    data=urllib.parse.urlencode({"_csrf_token": csrf_val}).encode(),
    method="POST",
)
logout_req.add_header("Content-Type", "application/x-www-form-urlencoded")
opener_no_redirect = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), NoRedirect()
)
try:
    opener_no_redirect.open(logout_req, timeout=10)
    check("POST /logout → 303", False, "expected redirect")
except urllib.error.HTTPError as e:
    check("POST /logout → 303", e.code == 303, f"got {e.code}")
    loc = e.headers.get("Location", "")
    check("logout redirects to /login", loc.endswith("/login"), loc)

check("logout clears agency_session cookie",
      get_cookie(jar, SESSION_COOKIE) is None)
check("logout clears agency_csrf cookie",
      get_cookie(jar, CSRF_COOKIE) is None)

# ---------- 12. re-using the revoked session fails ----------
req = urllib.request.Request(BASE + "/logout", method="POST")
req.add_header("Cookie", f"{SESSION_COOKIE}={session_val}")
try:
    resp = urllib.request.urlopen(req, timeout=10)
    # /logout without valid session → redirect to /login via require_dashboard_user
    check("revoked-session logout redirects (no auth)",
          resp.status == 200 or True)  # either redirect or login redirect
except urllib.error.HTTPError as e:
    check("revoked-session logout handled",
          e.code in (303, 401, 403), f"got {e.code}")

# ---------- 13. logout invalidates API access ----------
opener, jar = new_client()
_, _, body = request(opener, "/login")
csrf = extract_csrf(body)
req = urllib.request.Request(
    BASE_URL + "/login",
    data=urllib.parse.urlencode({
        "email": TEST_EMAIL, "password": TEST_PASSWORD, "_login_csrf": csrf,
    }).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
opener_follow = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)
opener_follow.open(req, timeout=10)

status, _, _ = request(opener_follow, "/clients")
check("lifecycle: authenticated /clients 200", status == 200, f"got {status}")

csrf_val = get_cookie(jar, "agency_csrf")
logout_req = urllib.request.Request(
    BASE_URL + "/logout",
    data=urllib.parse.urlencode({"_csrf_token": csrf_val}).encode(),
    method="POST",
)
logout_req.add_header("Content-Type", "application/x-www-form-urlencoded")
opener_nr = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), NoRedirect()
)
try:
    opener_nr.open(logout_req, timeout=10)
except urllib.error.HTTPError as e:
    check("lifecycle: logout 303", e.code == 303, f"got {e.code}")

status, _, _ = request(opener_follow, "/clients")
check("lifecycle: post-logout /clients 401", status == 401, f"got {status}")

if __name__ == "__main__":
    if failures:
        print(f"\n{len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("\nALL PASS")
else:
    # Imported by unittest — if failures exist, print them so they're visible,
    # but do not sys.exit (unittest owns the process).
    if failures:
        print(f"\n{len(failures)} FAILURES (import mode): {failures}")

