"""Shared test fixtures for ORM-path Client creation.

Tests that insert Client rows directly (bypassing the HTTP API) must supply
agency_id — the column is NOT NULL in the DB. This helper mirrors production
behaviour (Option A: fall back to the Default Agency when agency_id is not
supplied) so those tests keep working without duplicating the lookup.

Lookup is by name, matching app/services/agencies.py. If the Default Agency
is missing, the helper raises — a test that cannot find it is broken and
should fail loudly, not silently pass with a NULL client.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agency import Agency
from app.models.client import Client

DEFAULT_AGENCY_NAME = "Default Agency"


def create_test_client(
    db: Session,
    name: str,
    *,
    agency_id: int | None = None,
    **kwargs,
) -> Client:
    """Create a Client ORM row, defaulting agency_id to the Default Agency.

    Adds the client to `db` (like `db.add(client)`), but does NOT flush.
    Callers control flush/commit so they can add related rows first.
    """
    if agency_id is None:
        default = db.scalar(
            select(Agency).where(Agency.name == DEFAULT_AGENCY_NAME)
        )
        if default is None:
            raise RuntimeError(
                "Default Agency missing; run migrations before tests"
            )
        agency_id = default.id

    client = Client(agency_id=agency_id, name=name, **kwargs)
    db.add(client)
    return client
