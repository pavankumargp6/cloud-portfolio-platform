"""Agency service — server-controlled Default Agency resolution.

Phase 13.1 introduces multi-tenancy at the schema layer (clients.agency_id
NOT NULL, FK to agencies.id ON DELETE RESTRICT). Until authentication lands
(Phase 13.2/13.3), the unauthenticated HTTP API and ORM test fixtures
resolve the Default Agency inserted by migration 0006_agencies.py.

Security note: this resolver intentionally accepts NO caller-supplied agency
id. The public API must never be able to select another agency; every write
is pinned to the Default Agency. When the auth session supplies agency_id in
a later phase, this fallback will be replaced — until then it is the only
path, and it must not trust request input.

Lookup is by name, not by a hardcoded id. If the Default Agency is ever
renamed or reseeded with a different id, this module follows the data.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agency import Agency

log = logging.getLogger(__name__)

DEFAULT_AGENCY_NAME = "Default Agency"


def resolve_default_agency_id(db: Session) -> int:
    """Return the Default Agency's id for a server-controlled Client write.

    - Accepts no agency id from the caller. Public input is never trusted.
    - Looks up the agency by its exact name.
    - Logs a structured event every time the Default Agency is assigned, so
      any usage of this transitional fallback is visible in the logs.
    - Raises RuntimeError if the row is missing — that is a schema error
      (migration 0006 did not seed it), never a silent NULL.
    """
    default = db.scalar(
        select(Agency).where(Agency.name == DEFAULT_AGENCY_NAME)
    )
    if default is None:
        raise RuntimeError(
            f"Default Agency {DEFAULT_AGENCY_NAME!r} not found; "
            "migration 0006_agencies.py did not seed it"
        )

    log.info(
        "default_agency_assigned",
        extra={
            "event": "default_agency_assigned",
            "default_agency_id": default.id,
        },
    )
    return default.id
