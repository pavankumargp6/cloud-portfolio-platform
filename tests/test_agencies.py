"""Tests for agency multi-tenancy.

Each test creates its own Agency and Client, and cleans them up in tearDown.
We do NOT delete pre-existing clients or agencies.
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
            self.db.rollback()  # clear any failed transaction left by the test
            agency_id = getattr(self, "agency_id", None)
            if agency_id is None:
                return
            # Agency-scoped cleanup only. The Default Agency is never touched
            # because agency_id is the id of the agency created in setUp, whose
            # name is generated with a unique prefix and can never equal the
            # Default Agency's name.
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

        # Direct DELETE bypasses ORM relationship handling, so the database's
        # ON DELETE RESTRICT constraint is what fires (not an ORM attempt to
        # null out the client's FK).
        try:
            with self.assertRaises(IntegrityError):
                self.db.execute(
                    delete(Agency).where(Agency.id == self.agency_id)
                )
                self.db.commit()
        finally:
            self.db.rollback()

        # Both rows must still exist before teardown.
        self.assertIsNotNone(
            self.db.get(Agency, self.agency_id),
            "Agency was deleted despite ON DELETE RESTRICT",
        )
        self.assertIsNotNone(
            self.db.get(Client, client_id),
            "Client was deleted or its agency_id was nulled",
        )


if __name__ == "__main__":
    unittest.main()
