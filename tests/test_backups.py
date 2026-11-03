"""Unit tests for app.services.backups — runs in container."""

import unittest
import uuid

try:
    from app.db import SessionLocal
    from app.models.application import Application
    from app.models.client import Client
    from app.models.backup import BackupRecord, BackupVerification
    from app.services import backups as svc
    from tests.support.factories import create_test_client
    _READY = True
    _IMPORT_ERROR = None
except ImportError as exc:
    _READY = False
    _IMPORT_ERROR = exc


class BackupTestBase(unittest.TestCase):
    def setUp(self):
        if not _READY:
            self.skipTest(f"requires container: {_IMPORT_ERROR}")
        self.db = SessionLocal()
        
        # Clean prior data
        self.db.query(BackupVerification).delete()
        self.db.query(BackupRecord).delete()
        self.db.commit()

        suffix = uuid.uuid4().hex[:8]
        client = create_test_client(self.db, f"bk-{suffix}")
        self.db.flush()
        app = Application(
            client_id=client.id,
            name="backup-app",
            base_url="https://1.1.1.1/",
            environment="production",
            status="active",
        )
        self.db.add(app)
        self.db.flush()
        self.db.commit()
        self.app_id = app.id

    def tearDown(self):
        try:
            self.db.rollback()
            self.db.query(BackupVerification).delete()
            self.db.query(BackupRecord).delete()
            self.db.commit()
        finally:
            self.db.close()


class TestBackupService(BackupTestBase):

    def test_record_backup(self):
        record = svc.record_backup(
            self.db, self.app_id, "database", "s3://bucket/dump.sql", 1024
        )
        self.assertEqual(record.backup_type, "database")
        self.assertEqual(record.location, "s3://bucket/dump.sql")
        self.assertEqual(record.size_bytes, 1024)
        self.assertIsNotNone(record.created_at)

    def test_verify_successful_backup(self):
        record = svc.record_backup(
            self.db, self.app_id, "database", "s3://bucket/good.sql"
        )
        
        verification = svc.verify_backup(self.db, record.id)
        self.assertEqual(verification.status, "success")
        self.assertIsNone(verification.error_message)
        self.assertIsNotNone(verification.duration_ms)

    def test_verify_corrupt_backup(self):
        # The SimulatedRestoreBackend fails if the location contains "corrupt"
        record = svc.record_backup(
            self.db, self.app_id, "database", "s3://bucket/corrupt.sql"
        )
        
        verification = svc.verify_backup(self.db, record.id)
        self.assertEqual(verification.status, "failed")
        self.assertIn("corrupt", verification.error_message.lower())

    def test_list_backups(self):
        svc.record_backup(self.db, self.app_id, "database", "s3://b1.sql")
        svc.record_backup(self.db, self.app_id, "database", "s3://b2.sql")
        
        records, total = svc.list_backups(self.db, self.app_id)
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 2)
        # Order is created_at DESC, so b2 should be first
        self.assertEqual(records[0].location, "s3://b2.sql")

    def test_latest_verification(self):
        record = svc.record_backup(
            self.db, self.app_id, "database", "s3://bucket/good.sql"
        )
        
        # No verification yet
        self.assertIsNone(svc.get_latest_verification(self.db, record.id))
        
        # Verify once
        v1 = svc.verify_backup(self.db, record.id)
        
        # Fetch latest
        latest = svc.get_latest_verification(self.db, record.id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.id, v1.id)
        self.assertEqual(latest.status, "success")


if __name__ == "__main__":
    unittest.main()


class TestBackupAPI(unittest.TestCase):
    """HTTP API tests for backup endpoints — Phase 13.2.

    Runs inside the api container. All requests go through the container-
    side auth client (localhost:8000 + direct-DB user seeding).
    """

    def setUp(self):
        if not _READY:
            self.skipTest("requires container")
        self.db = SessionLocal()
        self.db.query(BackupVerification).delete()
        self.db.query(BackupRecord).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _create_app(self):
        from tests.support.container_auth import api_request
        status, client = api_request(
            "/clients", "POST",
            {"name": f"api-bk-{uuid.uuid4().hex[:6]}"},
        )
        self.assertEqual(status, 201, client)

        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "api-bk-app", "base_url": "https://1.1.1.1/", "environment": "production"},
        )
        self.assertEqual(status, 201, app)
        return client, app

    def test_create_and_verify_backup_via_api(self):
        from tests.support.container_auth import api_request
        client, app = self._create_app()

        status, backup = api_request(
            f"/applications/{app['id']}/backups", "POST",
            {"backup_type": "database", "location": "s3://my-bucket/db-2026.sql", "size_bytes": 102400},
        )
        self.assertEqual(status, 201, backup)
        self.assertEqual(backup["location"], "s3://my-bucket/db-2026.sql")
        self.assertIsNone(backup["latest_verification"])

        status, verification = api_request(
            f"/backups/{backup['id']}/verify", "POST",
        )
        self.assertEqual(status, 200, verification)
        self.assertEqual(verification["status"], "success")

        status, page = api_request(f"/applications/{app['id']}/backups")
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], backup["id"])
        self.assertIsNotNone(page["items"][0]["latest_verification"])
        self.assertEqual(page["items"][0]["latest_verification"]["status"], "success")

    def test_verify_corrupt_backup_via_api(self):
        from tests.support.container_auth import api_request
        client, app = self._create_app()

        status, backup = api_request(
            f"/applications/{app['id']}/backups", "POST",
            {"backup_type": "database", "location": "s3://my-bucket/corrupt.sql"},
        )
        self.assertEqual(status, 201, backup)

        status, verification = api_request(
            f"/backups/{backup['id']}/verify", "POST",
        )
        self.assertEqual(status, 200, verification)
        self.assertEqual(verification["status"], "failed")
        self.assertIn("corrupt", verification["error_message"].lower())

    def test_verify_missing_backup_404(self):
        from tests.support.container_auth import api_request
        status, _ = api_request("/backups/999999999/verify", "POST")
        self.assertEqual(status, 404)


class TestBackupDashboardIntegration(BackupTestBase):
    """Tests the backup UI on the dashboard — Phase 13.2. Requires auth."""

    def test_backup_status_renders(self):
        from tests.support.container_auth import api_request, get_html
        status, client = api_request(
            "/clients", "POST",
            {"name": f"bk-dash-{uuid.uuid4().hex[:6]}"},
        )
        self.assertEqual(status, 201, client)

        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "bk-dash-app", "base_url": "https://1.1.1.1/", "environment": "production"},
        )
        self.assertEqual(status, 201, app)

        rec = svc.record_backup(self.db, app["id"], "database", "s3://bucket/corrupt.sql")
        self.db.commit()
        svc.verify_backup(self.db, rec.id)

        status, html = get_html(f"/dashboard/clients/{client['id']}")
        self.assertEqual(status, 200)
        self.assertIn("Backups", html)
        self.assertIn("s3://bucket/corrupt.sql", html)
        self.assertIn("\u2717 Failed", html)
        self.assertIn("Re-Verify", html)

    def test_verify_backup_via_dashboard(self):
        from tests.support.container_auth import api_request, get_html, post_form
        status, client = api_request(
            "/clients", "POST",
            {"name": f"bk-dash-{uuid.uuid4().hex[:6]}"},
        )
        self.assertEqual(status, 201, client)

        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "bk-dash-app", "base_url": "https://1.1.1.1/", "environment": "production"},
        )
        self.assertEqual(status, 201, app)

        rec = svc.record_backup(self.db, app["id"], "database", "s3://bucket/good.sql")
        self.db.commit()

        # Trigger verification via dashboard route (POST with CSRF)
        post_form(f"/dashboard/backups/{rec.id}/verify", {})

        status, html = get_html(f"/dashboard/clients/{client['id']}")
        self.assertEqual(status, 200)
        self.assertIn("\u2713 Verified", html)


if __name__ == "__main__":
    unittest.main()
