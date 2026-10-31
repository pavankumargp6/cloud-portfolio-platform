"""Unit tests for app.services.backups — runs in container."""

import unittest
import uuid

try:
    from app.db import SessionLocal
    from app.models.application import Application
    from app.models.client import Client
    from app.models.backup import BackupRecord, BackupVerification
    from app.services import backups as svc
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
        client = Client(name=f"bk-{suffix}")
        self.db.add(client)
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
    """HTTP API tests for backup endpoints. Runs inside the container."""

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
        import urllib.request
        import json
        BASE_URL = "http://localhost:8000"
        
        req = urllib.request.Request(
            BASE_URL + "/clients",
            data=json.dumps({"name": f"api-bk-{uuid.uuid4().hex[:6]}"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            client = json.loads(resp.read())
            
        req = urllib.request.Request(
            BASE_URL + f"/clients/{client['id']}/applications",
            data=json.dumps({
                "name": "api-bk-app",
                "base_url": "https://1.1.1.1/",
                "environment": "production"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            app = json.loads(resp.read())
            
        return client, app

    def test_create_and_verify_backup_via_api(self):
        import urllib.request
        import json
        BASE_URL = "http://localhost:8000"
        
        client, app = self._create_app()
        
        # 1. Create a backup
        req = urllib.request.Request(
            BASE_URL + f"/applications/{app['id']}/backups",
            data=json.dumps({
                "backup_type": "database",
                "location": "s3://my-bucket/db-2026.sql",
                "size_bytes": 102400
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            backup = json.loads(resp.read())
            
        self.assertEqual(backup["location"], "s3://my-bucket/db-2026.sql")
        self.assertIsNone(backup["latest_verification"])

        # 2. Verify it
        req = urllib.request.Request(
            BASE_URL + f"/backups/{backup['id']}/verify",
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            verification = json.loads(resp.read())
            
        self.assertEqual(verification["status"], "success")
        
        # 3. List backups and check latest_verification is present
        req = urllib.request.Request(f"{BASE_URL}/applications/{app['id']}/backups")
        with urllib.request.urlopen(req) as resp:
            page = json.loads(resp.read())
            
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], backup["id"])
        self.assertIsNotNone(page["items"][0]["latest_verification"])
        self.assertEqual(page["items"][0]["latest_verification"]["status"], "success")

    def test_verify_corrupt_backup_via_api(self):
        import urllib.request
        import json
        BASE_URL = "http://localhost:8000"
        
        client, app = self._create_app()
        
        # Create a corrupt backup
        req = urllib.request.Request(
            BASE_URL + f"/applications/{app['id']}/backups",
            data=json.dumps({
                "backup_type": "database",
                "location": "s3://my-bucket/corrupt.sql"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            backup = json.loads(resp.read())
            
        # Verify it
        req = urllib.request.Request(
            BASE_URL + f"/backups/{backup['id']}/verify",
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            verification = json.loads(resp.read())
            
        self.assertEqual(verification["status"], "failed")
        self.assertIn("corrupt", verification["error_message"].lower())

    def test_verify_missing_backup_404(self):
        import urllib.request
        import urllib.error
        BASE_URL = "http://localhost:8000"
        
        req = urllib.request.Request(
            BASE_URL + "/backups/999999999/verify",
            method="POST"
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


class TestBackupDashboardIntegration(IncidentTestBase):
    """Tests the backup UI on the dashboard."""

    def test_backup_status_renders(self):
        import urllib.request
        import urllib.parse
        import uuid
        import json
        BASE_URL = "http://localhost:8000"

        # 1. Create client/app
        req = urllib.request.Request(
            BASE_URL + "/clients",
            data=json.dumps({"name": f"bk-dash-{uuid.uuid4().hex[:6]}"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            client = json.loads(resp.read())

        req = urllib.request.Request(
            BASE_URL + f"/clients/{client['id']}/applications",
            data=json.dumps({
                "name": "bk-dash-app",
                "base_url": "https://1.1.1.1/",
                "environment": "production"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            app = json.loads(resp.read())

        # 2. Create a backup and verify it (via service)
        rec = svc.record_backup(self.db, app["id"], "database", "s3://bucket/corrupt.sql")
        self.db.commit()
        svc.verify_backup(self.db, rec.id)

        # 3. Fetch dashboard HTML
        req = urllib.request.Request(f"{BASE_URL}/dashboard/clients/{client['id']}")
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode()

        # Verify the UI elements are present
        self.assertIn("Backups", html)
        self.assertIn("s3://bucket/corrupt.sql", html)
        self.assertIn("✗ Failed", html)
        self.assertIn("Re-Verify", html)

    def test_verify_backup_via_dashboard(self):
        import urllib.request
        import urllib.parse
        import uuid
        import json
        BASE_URL = "http://localhost:8000"

        # 1. Create client/app
        req = urllib.request.Request(
            BASE_URL + "/clients",
            data=json.dumps({"name": f"bk-dash-{uuid.uuid4().hex[:6]}"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            client = json.loads(resp.read())

        req = urllib.request.Request(
            BASE_URL + f"/clients/{client['id']}/applications",
            data=json.dumps({
                "name": "bk-dash-app",
                "base_url": "https://1.1.1.1/",
                "environment": "production"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            app = json.loads(resp.read())

        # 2. Create a valid backup (unverified)
        rec = svc.record_backup(self.db, app["id"], "database", "s3://bucket/good.sql")
        self.db.commit()

        # 3. Verify it via the dashboard route
        req = urllib.request.Request(
            BASE_URL + f"/dashboard/backups/{rec.id}/verify",
            data=b"",
            method="POST"
        )
        # Use urllib to POST (it might redirect to the detail page, which is fine)
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode()

        # 4. The final page should show "✓ Verified"
        self.assertIn("✓ Verified", html)
