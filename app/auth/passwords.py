"""Password hashing — Argon2id via pwdlib.

The hash format is whatever `pwdlib.PasswordHash.recommended()` produces
(Argon2id with default tuning as of pwdlib 0.2+). The full encoded hash
string is stored verbatim in `users.password_hash` — pwdlib embeds the
algorithm, salt and parameters in that string, so verification requires
only the stored hash.

Verification fails closed on any exception (corrupt hash, unknown format,
tampered row): the function returns False rather than raising. A bad hash
in the database must never allow login; it must only deny it.
"""

from __future__ import annotations

import logging

from pwdlib import PasswordHash

log = logging.getLogger(__name__)

# Single shared instance — Argon2id is CPU/memory-expensive by design; the
# library instance itself is stateless and safe to reuse across requests.
_password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    """Return the encoded Argon2id hash of `plain`. Never returns empty."""
    if not plain:
        raise ValueError("password must be non-empty")
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches `hashed`.

    Fails closed (returns False) on empty input or any exception raised by
    the underlying hash library — e.g. UnknownHashError on a corrupt row.
    """
    if not plain or not hashed:
        return False
    try:
        return _password_hash.verify(plain, hashed)
    except Exception as exc:  # pwdlib raises on malformed hash
        log.warning(
            "password_verify_failed",
            extra={"event": "password_verify_failed", "error": str(exc)},
        )
        return False
