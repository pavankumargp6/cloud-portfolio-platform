"""Authentication subsystem — Phase 13.2.

Modules:
  passwords  — Argon2id hashing/verification (pwdlib)
  sessions   — DB-backed opaque session token lifecycle
  csrf       — pre-auth (login) and post-auth (session-bound) CSRF tokens

Dependencies (get_current_user, require_api_user, require_dashboard_user)
and the auth router live in later patches (Patch 3+). This package holds
only the pure primitives.
"""
