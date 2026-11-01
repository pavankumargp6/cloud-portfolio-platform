"""Tests for the reporting service."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    from app.db import SessionLocal
    from app.models.application import Application
    from app.models.check_result import CheckResult
    from app.models.client import Client
    from app.models.incident import Incident
    from app.models.backup import BackupRecord
    from app.services import reporting as svc
    from tests.support.factories import create_test_client
    _READY = True
except ImportError:
    _READY = False


class ReportTestBase(unittest.TestCase):
    def setUp(self):
        if not _READY:
            self.skipTest("requires container")
        self.db = SessionLocal()
        
        # Cleanup
        self.db.query(CheckResult).delete()
        self.db.query(Incident).delete()
        self.db.query(BackupRecord).delete()
        self.db.commit()

        suffix = uuid.uuid4().hex[:8]
        client = create_test_client(self.db, f"rep-{suffix}")
        self.db.flush()
        app = Application(
            client_id=client.id,
            name="report-app",
            base_url="https://1.1.1.1/",
            environment="production",
            status="active",
        )
        self.db.add(app)
        self.db.flush()
        self.db.commit()
        self.client_id = client.id
        self.app_id = app.id

    def tearDown(self):
        self.db.rollback()
        self.db.close()


class TestReportingService(ReportTestBase):

    def test_empty_report(self):
        report = svc.generate_client_report(self.db, self.client_id, days=30)
        self.assertEqual(len(report["applications"]), 1)
        app_rep = report["applications"][0]
        self.assertEqual(app_rep["uptime_pct"], 0.0)
        self.assertEqual(app_rep["total_checks"], 0)
        self.assertEqual(len(app_rep["incidents"]), 0)

    def test_uptime_and_latency_calculation(self):
        now = datetime.now(timezone.utc)
        
        # 3 checks: 2 up, 1 down
        checks = [
            CheckResult(application_id=self.app_id, checked_at=now, status="up", latency_ms=100),
            CheckResult(application_id=self.app_id, checked_at=now, status="up", latency_ms=200),
            CheckResult(application_id=self.app_id, checked_at=now, status="down", latency_ms=None),
        ]
        self.db.add_all(checks)
        self.db.commit()

        report = svc.generate_client_report(self.db, self.client_id, days=30)
        app_rep = report["applications"][0]
        
        self.assertEqual(app_rep["total_checks"], 3)
        self.assertEqual(app_rep["up_checks"], 2)
        self.assertAlmostEqual(app_rep["uptime_pct"], 66.67, places=1)
        self.assertEqual(app_rep["avg_latency"], 150) # (100+200)/2

    def test_incident_duration(self):
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=60)
        
        # 1 resolved incident (45 mins), 1 open incident (started 20 mins ago)
        inc1 = Incident(application_id=self.app_id, status="resolved", started_at=start, resolved_at=start + timedelta(minutes=45))
        inc2 = Incident(application_id=self.app_id, status="open", started_at=now - timedelta(minutes=20))
        self.db.add_all([inc1, inc2])
        self.db.commit()

        report = svc.generate_client_report(self.db, self.client_id, days=30)
        app_rep = report["applications"][0]
        
        self.assertEqual(len(app_rep["incidents"]), 2)
        # Total downtime should be ~65 mins
        self.assertAlmostEqual(app_rep["total_downtime_minutes"], 65.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
