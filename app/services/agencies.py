"""Agency service — fallback resolution for legacy callers.

Phase 13.1 introduces multi-tenancy at the schema layer (clients.agency_id
NOT NULL, FK to agencies.id ON DELETE RESTRICT). The HTTP API and test
fixtures that predate the change do not supply agency_id.

This module provides a single fallback path: callers that pass agency_id=None
land on the Default Agency inserted by migration 0006_agencies.py. The
fallback is loud — every hit is logged — so that once Phase 13.2 supplies
agency_id from the auth session on every request, any remaining fallback
appears in the log as a defect rather than as silent behaviour.

Lookup is by name, not by a hardcoded id. If the Default Agency is ever
renamed or reseeded with a different id, this module follows the data.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agency import Agency

log = logging.getLogger(__name__)

DEFAULT_AGENCY_NAME = "Default Agency"


def resolve_agency_id(db: Session, agency_id: int | None) -> int:
    """Return the agency id to use for a Client write.

    - If agency_id is provided, return it unchanged (caller's choice wins).
    - If agency_id is None, look up the Default Agency by name and return
      its id, logging a structured fallback event.

    Raises RuntimeError if the Default Agency row is missing — that is a
    schema error (migration 0006 did not seed it), never a silent None.
    """
    if agency_id is not None:
        return agency_id

    default = db.scalar(
        select(Agency).where(Agency.name == DEFAULT_AGENCY_NAME)
    )
    if default is None:
        raise RuntimeError(
            f"Default Agency {DEFAULT_AGENCY_NAME!r} not found; "
            "migration 0006_agencies.py did not seed it"
        )

    log.info(
        "agency_fallback_default_used",
        extra={
            "event": "agency_fallback_default_used",
            "default_agency_id": default.id,
        },
    )
    return default.id
