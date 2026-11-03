"""Shared test user credentials + idempotent seeding.

Tests that need an authenticated session use these credentials. The user
is created once per test session (idempotent) via `ensure_test_user()`,
which shells out to `docker compose exec` inside the running isolated
stack. The seeding script checks for the user by email before inserting,
so re-runs are safe.

The user belongs to the Default Agency. Multi-agency tests (e.g. the ones
in tests/test_agencies.py) create additional users with different
agency_id values via the same subprocess mechanism, not via this module.
"""

import os
import subprocess

TEST_USER_EMAIL = "test-user@example.com"
TEST_USER_PASSWORD = "correct horse battery staple"
TEST_USER_NAME = "Test User"
INACTIVE_USER_EMAIL = "inactive-test@example.com"
TEST_PROJECT = os.environ.get("M8_TEST_PROJECT", "m8_13_2_test")

_SEED_SCRIPT = '''
from app.db import SessionLocal
from app.models.agency import Agency
from app.models.user import User
from app.auth.passwords import hash_password
from sqlalchemy import func

db = SessionLocal()
agency = db.query(Agency).filter_by(name="Default Agency").first()
assert agency is not None, "Default Agency missing"
existing = db.query(User).filter(func.lower(User.email) == "{email}").first()
if existing is None:
    u = User(
        agency_id=agency.id,
        email="{email}",
        name="{name}",
        password_hash=hash_password("{password}"),
        is_active=True,
    )
    db.add(u)
    db.commit()
    print(f"seeded {{u.email}} id={{u.id}}")
else:
    print(f"already present {{existing.email}} id={{existing.id}}")
db.close()
'''

_ensured = False


def ensure_test_user() -> None:
    """Create the shared test user if missing. Cached — no-op after first call."""
    global _ensured
    if _ensured:
        return

    script = _SEED_SCRIPT.format(
        email=TEST_USER_EMAIL,
        name=TEST_USER_NAME,
        password=TEST_USER_PASSWORD,
    )

    cmd = [
        "docker", "compose", "-p", TEST_PROJECT, "exec", "-T",
        "api", "env", "PYTHONPATH=/app", "python3", "-c", script,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to seed test user. Is the m8_13_2_test stack up?\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    _ensured = True


_INACTIVE_SEED_SCRIPT = """
from app.db import SessionLocal
from app.models.agency import Agency
from app.models.user import User
from app.auth.passwords import hash_password
from sqlalchemy import func

EMAIL = %r
PASSWORD = %r

db = SessionLocal()
agency = db.query(Agency).filter_by(name="Default Agency").first()
assert agency is not None, "Default Agency missing"
existing = db.query(User).filter(func.lower(User.email) == EMAIL).first()
if existing is None:
    db.add(User(
        agency_id=agency.id,
        email=EMAIL,
        name="Inactive Test",
        password_hash=hash_password(PASSWORD),
        is_active=False,
    ))
    db.commit()
    print(f"seeded inactive {EMAIL}")
else:
    print(f"already present {existing.email} is_active={existing.is_active}")
db.close()
"""

_inactive_ensured = False


def ensure_inactive_test_user() -> None:
    """Seed the inactive test user (needed for login rejection tests)."""
    global _inactive_ensured
    if _inactive_ensured:
        return
    script = _INACTIVE_SEED_SCRIPT % (INACTIVE_USER_EMAIL, TEST_USER_PASSWORD)
    cmd = [
        "docker", "compose", "-p", TEST_PROJECT, "exec", "-T",
        "api", "env", "PYTHONPATH=/app", "python3", "-c", script,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to seed inactive user:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    _inactive_ensured = True
