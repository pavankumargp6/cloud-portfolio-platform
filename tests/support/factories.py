"""Shared test fixtures for ORM-path Client creation.

Tests that insert Client rows directly (bypassing the HTTP API) must supply
agency_id — the column is NOT NULL in the DB. When no explicit agency_id is
passed, this helper delegates to the production resolver
(app.services.agencies.resolve_default_agency_id), so tests and production
share one lookup path.

Internal agency-isolation tests may still pass an explicit agency_id to
attach a client to a non-default agency.
"""

from sqlalchemy.orm import Session

from app.models.client import Client
from app.services.agencies import resolve_default_agency_id


def create_test_client(
    db: Session,
    name: str,
    *,
    agency_id: int | None = None,
    **kwargs,
) -> Client:
    """Create a Client ORM row, defaulting agency_id via the shared resolver.

    Adds the client to `db` (like `db.add(client)`), but does NOT flush.
    Callers control flush/commit so they can add related rows first.
    """
    if agency_id is None:
        agency_id = resolve_default_agency_id(db)

    client = Client(agency_id=agency_id, name=name, **kwargs)
    db.add(client)
    return client
