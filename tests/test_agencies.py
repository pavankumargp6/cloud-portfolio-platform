"""DB-level tests for agency multi-tenancy — Phase 13.2.

These tests run inside the api container (see CI workflow). They verify
the database schema, constraints, and the ClientCreate schema directly —
no HTTP. HTTP-level contract tests for the /clients POST endpoint
(anonymous 401, authenticated 422 on agency_id, authenticated 201 with
current_user.agency_id) live in tests/test_clients.py and run host-side
where authentication from the host port works.

Phase 13.1's TestPublicAgencyInputRejectedHTTP was moved to
test_clients.py in Phase 13.2 because:
  * POST /clients now requires authentication
  * The auth_client used host-side (localhost:8080) is simpler than
    replicating a container-local auth flow
  * The contract being tested is API-level, not DB-level

Each test creates its own Agency and Client, and cleans them up in
tearDown. We do NOT delete pre-existing clients or agencies.
"""

import unittest
import uuid

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

try:
    from app.db import SessionLocal
    from app.models.agency import Agency
    from app.models.client import Client
    import app.models  # noqa: F401  (registers every model with the registry)
    _READY = True
except ImportError:
    _READY = False


# The exact name inserted by the M8 migration.
# Verify against alembic/versions/<m8 migration>.py before running.
DEFAULT_AGENCY_NAME = "Default Agency"


class TestAgencies(unittest.TestCase):
    def setUp(self):
        if not _READY:
            self.skipTest("requires container")
        self.db = SessionLocal()
        self.agency = Agency(name=f"test-agency-{uuid.uuid4().hex[:8]}")
        self.db.add(self.agency)
        self.db.commit()
        self.db.refresh(self.agency)
        self.agency_id = self.agency.id

    def tearDown(self):
        try:
            self.db.rollback()
            agency_id = getattr(self, "agency_id", None)
            if agency_id is None:
                return
            self.db.query(Client).filter(
                Client.agency_id == agency_id
            ).delete(synchronize_session=False)
            self.db.query(Agency).filter(
                Agency.id == agency_id
            ).delete(synchronize_session=False)
            self.db.commit()
        finally:
            self.db.close()

    def test_default_agency_exists(self):
        agency = (
            self.db.query(Agency)
            .filter_by(name=DEFAULT_AGENCY_NAME)
            .first()
        )
        self.assertIsNotNone(
            agency,
            f"Migration did not create {DEFAULT_AGENCY_NAME!r}",
        )

    def test_client_requires_agency(self):
        try:
            with self.assertRaises(IntegrityError):
                self.db.add(Client(name="no-agency", agency_id=None))
                self.db.commit()
        finally:
            self.db.rollback()

    def test_all_mappers_configure(self):
        from sqlalchemy.orm import configure_mappers
        configure_mappers()  # raises InvalidRequestError on failure

    def test_cannot_delete_agency_with_clients(self):
        client = Client(name="test-client", agency_id=self.agency_id)
        self.db.add(client)
        self.db.commit()
        client_id = client.id

        try:
            with self.assertRaises(IntegrityError):
                self.db.execute(
                    delete(Agency).where(Agency.id == self.agency_id)
                )
                self.db.commit()
        finally:
            self.db.rollback()

        self.assertIsNotNone(
            self.db.get(Agency, self.agency_id),
            "Agency was deleted despite ON DELETE RESTRICT",
        )
        self.assertIsNotNone(
            self.db.get(Client, client_id),
            "Client was deleted or its agency_id was nulled",
        )


class TestClientCreateSchema(unittest.TestCase):
    """ClientCreate must reject `agency_id` in the payload (extra='forbid').

    Proves that even if authentication is bypassed by a future code change,
    the schema would still reject a caller-supplied agency_id.
    """

    def test_schema_rejects_agency_id(self):
        from pydantic import ValidationError
        from app.schemas.client import ClientCreate

        with self.assertRaises(ValidationError):
            ClientCreate(name="should-reject", agency_id=12345)


if __name__ == "__main__":
    unittest.main()
