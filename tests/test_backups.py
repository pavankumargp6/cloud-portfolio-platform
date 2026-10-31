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
