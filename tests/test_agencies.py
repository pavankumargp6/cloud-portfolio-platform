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


class TestClientAgencyFallback(unittest.TestCase):
    """Option A behaviour: server-controlled Default Agency assignment.

    The direct-router test verifies the internal resolution path. The
    HTTP-level class below verifies the public contract.
    """

    def setUp(self):
        if not _READY:
            self.skipTest("requires container")
        self.db = SessionLocal()
        self.default = (
            self.db.query(Agency).filter_by(name=DEFAULT_AGENCY_NAME).first()
        )
        if self.default is None:
            self.skipTest("Default Agency missing")
        self.default_id = self.default.id
        self.created_client_ids = []

    def tearDown(self):
        try:
            self.db.rollback()
            for cid in self.created_client_ids:
                self.db.query(Client).filter(Client.id == cid).delete()
            self.db.commit()
        finally:
            self.db.close()

    def test_client_create_without_agency_uses_default(self):
        """Direct-router call with no agency_id lands on Default Agency."""
        from app.routers.clients import create_client
        from app.schemas.client import ClientCreate

        name = f"fallback-{uuid.uuid4().hex[:8]}"
        created = create_client(ClientCreate(name=name), db=self.db)
        self.created_client_ids.append(created.id)
        self.assertEqual(created.agency_id, self.default_id)

    def test_client_create_schema_rejects_agency_id(self):
        """ClientCreate must reject agency_id in the payload (extra='forbid')."""
        from pydantic import ValidationError
        from app.schemas.client import ClientCreate

        with self.assertRaises(ValidationError):
            ClientCreate(name="should-reject", agency_id=self.default_id)


class TestPublicAgencyInputRejectedHTTP(unittest.TestCase):
    """HTTP-level: POST /clients must never accept agency_id from callers.

    Regression guard for the public-input vulnerability. Verifies:
      1. POST without agency_id succeeds and lands on Default Agency.
      2. POST with an arbitrary agency_id returns 422.
      3. A rejected request creates no client, under any agency.
    """

    BASE_URL = "http://localhost:8000"

    def setUp(self):
        if not _READY:
            self.skipTest("requires container")
        self.db = SessionLocal()
        self.default = (
            self.db.query(Agency).filter_by(name=DEFAULT_AGENCY_NAME).first()
        )
        if self.default is None:
            self.skipTest("Default Agency missing")
        self.default_id = self.default.id
        self.created_client_ids = []

    def tearDown(self):
        try:
            self.db.rollback()
            for cid in self.created_client_ids:
                self.db.query(Client).filter(Client.id == cid).delete()
            self.db.commit()
        finally:
            self.db.close()

    def _post_clients(self, body):
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL + "/clients",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            exc.close()
            try:
                parsed = json.loads(payload) if payload else None
            except Exception:
                parsed = None
            return exc.code, parsed

    def test_post_clients_without_agency_id_uses_default(self):
        name = f"http-fallback-{uuid.uuid4().hex[:8]}"
        status, body = self._post_clients({"name": name})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["agency_id"], self.default_id)
        self.created_client_ids.append(body["id"])

    def test_post_clients_with_arbitrary_agency_id_returns_422(self):
        suffix = uuid.uuid4().hex[:8]
        other = Agency(name=f"http-other-{suffix}")
        self.db.add(other)
        self.db.commit()
        self.db.refresh(other)
        other_id = other.id
        try:
            status, body = self._post_clients(
                {"name": f"rejected-{suffix}", "agency_id": other_id}
            )
            self.assertEqual(status, 422, body)
        finally:
            self.db.query(Client).filter(Client.agency_id == other_id).delete()
            self.db.query(Agency).filter(Agency.id == other_id).delete()
            self.db.commit()

    def test_dashboard_form_assigns_default_agency(self):
        """Container-side definitive check: POST /dashboard/clients assigns Default Agency."""
        import urllib.parse
        import urllib.request

        name = f"dash-default-{uuid.uuid4().hex[:8]}"
        data = urllib.parse.urlencode(
            {"name": name, "contact_email": "", "notes": ""}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL + "/dashboard/clients",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)

        self.db.expire_all()
        created = self.db.query(Client).filter_by(name=name).one()
        self.created_client_ids.append(created.id)
        self.assertEqual(created.agency_id, self.default_id)

    def test_rejected_request_creates_no_client(self):
        suffix = uuid.uuid4().hex[:8]
        other = Agency(name=f"http-other2-{suffix}")
        self.db.add(other)
        self.db.commit()
        self.db.refresh(other)
        other_id = other.id
        try:
            name = f"rejected-nobody-{suffix}"
            status, _ = self._post_clients({"name": name, "agency_id": other_id})
            self.assertEqual(status, 422)

            self.db.expire_all()
            self.assertEqual(
                self.db.query(Client).filter(Client.name == name).all(),
                [],
                "rejected request created a client by name",
            )
            self.assertEqual(
                self.db.query(Client).filter(Client.agency_id == other_id).count(),
                0,
                "rejected request created a client under the supplied agency",
            )
        finally:
            self.db.query(Client).filter(Client.agency_id == other_id).delete()
            self.db.query(Agency).filter(Agency.id == other_id).delete()
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
