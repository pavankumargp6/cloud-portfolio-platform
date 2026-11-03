"""Operator CLI — Phase 13.2.

Provisioning interface for user accounts. Deliberately NOT exposed via
HTTP: the spec forbids public account creation. Invoked as:

    python -m app.cli create-user \\
        --agency-id 1 \\
        --email operator@example.com \\
        --name "Agency Operator"

Design choices:

  * Password is prompted twice via `getpass` — never accepted as a
    command-line argument. Password in argv would appear in `ps`,
    shell history, and process accounting. Explicitly rejected if
    attempted.

  * Validation mirrors the authentication-time rules:
        - password 12–128 characters (spec)
        - email trimmed + lowercased (spec)
        - email uniqueness is case-insensitive (DB-enforced; app
          provides a friendly error before hitting the DB constraint)

  * Agency must exist. `users.agency_id` FK is RESTRICT, so a bad
    agency_id would fail at INSERT anyway, but we check explicitly to
    give a clear error message.

  * No logging of passwords anywhere. The success message names the
    new user_id and email, never the password or its hash.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import hash_password
from app.db import SessionLocal
from app.models.agency import Agency
from app.models.user import User

PASSWORD_MIN = 12
PASSWORD_MAX = 128
NAME_MAX = 255
EMAIL_MAX = 320


def _prompt_password() -> str:
    """Prompt twice and validate length. Exits non-zero on failure."""
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("ERROR: passwords do not match", file=sys.stderr)
        sys.exit(2)
    if len(pw1) < PASSWORD_MIN:
        print(
            f"ERROR: password must be at least {PASSWORD_MIN} characters",
            file=sys.stderr,
        )
        sys.exit(2)
    if len(pw1) > PASSWORD_MAX:
        print(
            f"ERROR: password must be at most {PASSWORD_MAX} characters",
            file=sys.stderr,
        )
        sys.exit(2)
    return pw1


def _cmd_create_user(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    name = args.name.strip()

    if not email or "@" not in email:
        print("ERROR: --email must be a valid email address", file=sys.stderr)
        return 2
    if len(email) > EMAIL_MAX:
        print(f"ERROR: --email exceeds {EMAIL_MAX} chars", file=sys.stderr)
        return 2
    if not name:
        print("ERROR: --name must be non-empty", file=sys.stderr)
        return 2
    if len(name) > NAME_MAX:
        print(f"ERROR: --name exceeds {NAME_MAX} chars", file=sys.stderr)
        return 2

    password = _prompt_password()
    pw_hash = hash_password(password)

    db = SessionLocal()
    try:
        agency = db.get(Agency, args.agency_id)
        if agency is None:
            print(
                f"ERROR: agency_id={args.agency_id} does not exist",
                file=sys.stderr,
            )
            return 3

        # Case-insensitive existence check to give a friendly error
        # before hitting the DB unique-index constraint.
        existing = db.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        if existing is not None:
            print(
                f"ERROR: a user with email {email!r} already exists "
                f"(id={existing.id})",
                file=sys.stderr,
            )
            return 4

        user = User(
            agency_id=agency.id,
            email=email,
            name=name,
            password_hash=pw_hash,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"OK: user created id={user.id} email={user.email} "
              f"agency_id={user.agency_id}")
        return 0

    except IntegrityError as exc:
        db.rollback()
        print(f"ERROR: database rejected insert: {exc.orig}", file=sys.stderr)
        return 5
    finally:
        db.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Operator CLI for Agency Reliability Desk",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cu = sub.add_parser("create-user", help="Create a new operator account")
    cu.add_argument("--agency-id", type=int, required=True,
                    help="Agency id the user belongs to")
    cu.add_argument("--email", required=True,
                    help="User email (will be lowercased)")
    cu.add_argument("--name", required=True,
                    help="Display name")
    cu.set_defaults(func=_cmd_create_user)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
