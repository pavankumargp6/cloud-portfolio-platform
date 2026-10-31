"""Worker, scheduler, and retention tests.

These require sqlalchemy. They SKIP on the host (no packages) and RUN
inside the api container via:
    docker compose exec api python3 -m unittest tests.test_monitoring_worker -v
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    from app.db import SessionLocal
    from app.models.application import Application
    from app.models.check_result import CheckResult
    from app.models.client import Client
    from app.models.monitoring import ApplicationHealthState, ApplicationMonitoring
    from app.monitoring.probe import ProbeOutcome
    from app.monitoring.retention import _reset_prune_state, maybe_prune
    from app.monitoring.scheduler import pick_due, record_outcome, reschedule
    from app.worker.loop import run_once
    _READY = True
    _IMPORT_ERROR = None
except ImportError as exc:
    _READY = False
    _IMPORT_ERROR = exc


class WorkerTestBase(unittest.TestCase):
    def setUp(self):
        if not _READY:
            self.skipTest(f"requires container (sqlalchemy missing on host): {_IMPORT_ERROR}")
        self.db = SessionLocal()

    def tearDown(self):
        try:
            self.db.rollback()
        finally:
            try:
                self.db.close()
            except Exception:
                pass

    def _create_enabled_app(
        self, interval=300, threshold=3, next_offset=-60, status="active"
    ):
        suffix = uuid.uuid4().hex[:8]
        client = Client(name=f"w-{suffix}")
        self.db.add(client)
        self.db.flush()
        app = Application(
            client_id=client.id,
            name="app",
            base_url="https://1.1.1.1/",
            environment="production",
            status=status,
        )
        self.db.add(app)
        self.db.flush()
        mon = ApplicationMonitoring(
            application_id=app.id,
            enabled=True,
            interval_seconds=interval,
            failure_threshold=threshold,
            next_check_at=datetime.now(timezone.utc)
            + timedelta(seconds=next_offset),
        )
        self.db.add(mon)
        self.db.flush()
        return app, mon


class TestPickDue(WorkerTestBase):

    def setUp(self):
        super().setUp()
        # Clear any enabled monitoring rows from prior tests so pick_due
        # sees only what this test creates.
        self.db.query(ApplicationMonitoring).filter_by(enabled=True).delete()
        self.db.commit()

    def test_picks_due_app(self):
        app, _ = self._create_enabled_app(next_offset=-60)
        self.db.commit()
        rows = pick_due(self.db, limit=20)
        ids = [r[1].id for r in rows]
        self.assertIn(app.id, ids)

    def test_skips_future(self):
        app, _ = self._create_enabled_app(next_offset=3600)
        self.db.commit()
        rows = pick_due(self.db, limit=20)
        ids = [r[1].id for r in rows]
        self.assertNotIn(app.id, ids)

    def test_skips_disabled(self):
        app, mon = self._create_enabled_app(next_offset=-60)
        mon.enabled = False
        self.db.commit()
        rows = pick_due(self.db, limit=20)
        ids = [r[1].id for r in rows]
        self.assertNotIn(app.id, ids)

    def test_skips_archived_app(self):
        app, _ = self._create_enabled_app(next_offset=-60, status="archived")
        self.db.commit()
        rows = pick_due(self.db, limit=20)
        ids = [r[1].id for r in rows]
        self.assertNotIn(app.id, ids)


class TestRecordOutcome(WorkerTestBase):

    def test_success_creates_check_and_up(self):
        app, _ = self._create_enabled_app()
        self.db.commit()
        record_outcome(
            self.db, app.id, ProbeOutcome(status="up", http_status=200, latency_ms=42)
        )
        self.db.commit()
        checks = (
            self.db.query(CheckResult)
            .filter_by(application_id=app.id)
            .order_by(CheckResult.id.desc())
            .all()
        )
        self.assertGreaterEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "up")
        self.assertEqual(checks[0].http_status, 200)

        state = self.db.get(ApplicationHealthState, app.id)
        self.assertEqual(state.current_status, "up")
        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNotNone(state.last_success_at)

    def test_three_failures_down_with_callback(self):
        app, _ = self._create_enabled_app(threshold=3)
        self.db.commit()
        fail = ProbeOutcome(status="down", failure_reason="connect_refused")

        record_outcome(self.db, app.id, fail)
        self.db.commit()
        record_outcome(self.db, app.id, fail)
        self.db.commit()
        state = self.db.get(ApplicationHealthState, app.id)
        self.assertEqual(state.current_status, "unknown")
        self.assertEqual(state.consecutive_failures, 2)

        transitions = []
        record_outcome(
            self.db, app.id, fail,
            on_transition=lambda f, t: transitions.append((f, t)),
        )
        self.db.commit()
        state = self.db.get(ApplicationHealthState, app.id)
        self.assertEqual(state.current_status, "down")
        self.assertEqual(transitions, [("unknown", "down")])

    def test_down_then_success_goes_up(self):
        app, _ = self._create_enabled_app(threshold=1)
        self.db.commit()
        record_outcome(self.db, app.id, ProbeOutcome(status="down"))
        self.db.commit()
        self.assertEqual(
            self.db.get(ApplicationHealthState, app.id).current_status, "down"
        )
        transitions = []
        record_outcome(
            self.db, app.id, ProbeOutcome(status="up"),
            on_transition=lambda f, t: transitions.append((f, t)),
        )
        self.db.commit()
        self.assertEqual(
            self.db.get(ApplicationHealthState, app.id).current_status, "up"
        )
        self.assertEqual(transitions, [("down", "up")])

    def test_blocked_ssrf_does_not_move_state(self):
        app, _ = self._create_enabled_app()
        self.db.commit()
        record_outcome(self.db, app.id, ProbeOutcome(status="up"))
        self.db.commit()
        record_outcome(
            self.db, app.id,
            ProbeOutcome(status="blocked_ssrf", failure_reason="blocked_ip"),
        )
        self.db.commit()
        state = self.db.get(ApplicationHealthState, app.id)
        self.assertEqual(state.current_status, "up")
        self.assertEqual(state.consecutive_failures, 0)


class TestReschedule(WorkerTestBase):

    def test_sets_next_check(self):
        app, _ = self._create_enabled_app(interval=120)
        self.db.commit()
        when = datetime.now(timezone.utc)
        reschedule(self.db, app.id, checked_at=when)
        self.db.commit()
        mon = self.db.get(ApplicationMonitoring, app.id)
        self.assertIsNotNone(mon.last_checked_at)
        self.assertIsNotNone(mon.next_check_at)
        delta = (mon.next_check_at - mon.last_checked_at).total_seconds()
        self.assertAlmostEqual(delta, 120, delta=1)


class TestRunOnce(WorkerTestBase):

    def setUp(self):
        super().setUp()
        self.db.query(ApplicationMonitoring).filter_by(enabled=True).delete()
        self.db.commit()

    def test_orchestrates_one_iteration(self):
        app, _ = self._create_enabled_app(next_offset=-60, interval=180)
        self.db.commit()

        called_with = []
        def fake_probe(url):
            called_with.append(url)
            return ProbeOutcome(status="up", http_status=200, latency_ms=15)

        worked = run_once(self.db, probe_fn=fake_probe)
        self.assertTrue(worked)
        self.assertEqual(called_with, ["https://1.1.1.1/"])

        checks = self.db.query(CheckResult).filter_by(application_id=app.id).all()
        self.assertEqual(len(checks), 1)
        state = self.db.get(ApplicationHealthState, app.id)
        self.assertEqual(state.current_status, "up")
        mon = self.db.get(ApplicationMonitoring, app.id)
        self.assertIsNotNone(mon.last_checked_at)

    def test_returns_false_when_nothing_due(self):
        self._create_enabled_app(next_offset=3600)
        self.db.commit()
        worked = run_once(self.db, probe_fn=lambda _url: None)
        self.assertFalse(worked)


class TestRetention(WorkerTestBase):

    def test_prunes_old_keeps_recent(self):
        app, _ = self._create_enabled_app()
        self.db.commit()

        old = CheckResult(
            application_id=app.id,
            checked_at=datetime.now(timezone.utc) - timedelta(days=40),
            status="up",
        )
        recent = CheckResult(application_id=app.id, status="up")
        self.db.add(old)
        self.db.add(recent)
        self.db.commit()
        old_id, recent_id = old.id, recent.id

        _reset_prune_state()
        deleted = maybe_prune(self.db, retention_days=30, prune_interval_hours=0)
        self.assertGreaterEqual(deleted, 1)

        remaining = {
            r.id
            for r in self.db.query(CheckResult).filter_by(application_id=app.id).all()
        }
        self.assertNotIn(old_id, remaining)
        self.assertIn(recent_id, remaining)

    def test_throttle(self):
        _reset_prune_state()
        # First call runs (returns >= 0)
        n1 = maybe_prune(self.db, retention_days=30, prune_interval_hours=24)
        # Second call within same window returns 0 (throttled)
        n2 = maybe_prune(self.db, retention_days=30, prune_interval_hours=24)
        self.assertEqual(n2, 0)
        self.assertGreaterEqual(n1, 0)


if __name__ == "__main__":
    unittest.main()
