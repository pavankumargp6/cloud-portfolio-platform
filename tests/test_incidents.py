"""Unit tests for app.services.incidents — requires DB, runs in container.

    docker compose exec -T api python3 -m unittest tests.test_incidents -v
"""

import unittest
import uuid

try:
    from app.db import SessionLocal
    from app.models.application import Application
    from app.models.client import Client
    from app.models.incident import Incident, IncidentEvent
    from app.services import incidents as svc
    from tests.support.factories import create_test_client
    _READY = True
    _IMPORT_ERROR = None
except ImportError as exc:
    _READY = False
    _IMPORT_ERROR = exc


class IncidentTestBase(unittest.TestCase):
    def setUp(self):
        if not _READY:
            self.skipTest(f"requires container: {_IMPORT_ERROR}")
        self.db = SessionLocal()
        # Wipe incidents from prior tests so state is deterministic.
        self.db.query(IncidentEvent).delete()
        self.db.query(Incident).delete()
        self.db.commit()

        suffix = uuid.uuid4().hex[:8]
        client = create_test_client(self.db, f"inc-{suffix}")
        self.db.flush()
        app = Application(
            client_id=client.id,
            name="app",
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
            self.db.query(IncidentEvent).delete()
            self.db.query(Incident).delete()
            self.db.commit()
        finally:
            try:
                self.db.close()
            except Exception:
                pass


class TestOpenIncident(IncidentTestBase):

    def test_open_creates_incident_and_event(self):
        inc = svc.open_incident(self.db, self.app_id, reason="manual test")
        self.assertEqual(inc.status, "open")
        self.assertEqual(inc.application_id, self.app_id)
        self.assertIsNotNone(inc.started_at)
        events = self.db.query(IncidentEvent).filter_by(incident_id=inc.id).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "opened")
        self.assertIn("manual test", events[0].details)

    def test_open_twice_raises_already_open(self):
        first = svc.open_incident(self.db, self.app_id)
        with self.assertRaises(svc.AlreadyOpenError) as ctx:
            svc.open_incident(self.db, self.app_id)
        self.assertEqual(ctx.exception.existing_incident_id, first.id)


class TestAcknowledge(IncidentTestBase):

    def test_acknowledge_open_incident(self):
        inc = svc.open_incident(self.db, self.app_id)
        ack = svc.acknowledge_incident(self.db, inc.id, by="alice")
        self.assertEqual(ack.status, "acknowledged")
        self.assertEqual(ack.acknowledged_by, "alice")
        self.assertIsNotNone(ack.acknowledged_at)
        events = self.db.query(IncidentEvent).filter_by(incident_id=inc.id).order_by(IncidentEvent.id).all()
        self.assertEqual([e.event_type for e in events], ["opened", "acknowledged"])

    def test_acknowledge_already_acknowledged_raises(self):
        inc = svc.open_incident(self.db, self.app_id)
        svc.acknowledge_incident(self.db, inc.id)
        with self.assertRaises(svc.InvalidIncidentStateError):
            svc.acknowledge_incident(self.db, inc.id)

    def test_acknowledge_resolved_raises(self):
        inc = svc.open_incident(self.db, self.app_id)
        svc.resolve_incident(self.db, inc.id)
        with self.assertRaises(svc.InvalidIncidentStateError):
            svc.acknowledge_incident(self.db, inc.id)

    def test_acknowledge_missing_raises(self):
        with self.assertRaises(svc.IncidentNotFoundError):
            svc.acknowledge_incident(self.db, 999999999)


class TestResolve(IncidentTestBase):

    def test_resolve_open_with_notes(self):
        inc = svc.open_incident(self.db, self.app_id)
        res = svc.resolve_incident(self.db, inc.id, by="alice", notes="restarted db")
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.resolved_by, "alice")
        self.assertEqual(res.resolution_notes, "restarted db")
        self.assertIsNotNone(res.resolved_at)
        events = self.db.query(IncidentEvent).filter_by(incident_id=inc.id).order_by(IncidentEvent.id).all()
        self.assertEqual([e.event_type for e in events], ["opened", "resolved"])

    def test_resolve_acknowledged(self):
        inc = svc.open_incident(self.db, self.app_id)
        svc.acknowledge_incident(self.db, inc.id)
        res = svc.resolve_incident(self.db, inc.id)
        self.assertEqual(res.status, "resolved")

    def test_resolve_twice_raises(self):
        inc = svc.open_incident(self.db, self.app_id)
        svc.resolve_incident(self.db, inc.id)
        with self.assertRaises(svc.InvalidIncidentStateError):
            svc.resolve_incident(self.db, inc.id)

    def test_resolve_missing_raises(self):
        with self.assertRaises(svc.IncidentNotFoundError):
            svc.resolve_incident(self.db, 999999999)


class TestHandleTransition(IncidentTestBase):

    def test_up_to_down_opens_incident(self):
        svc.handle_transition(self.db, self.app_id, "up", "down")
        open_incidents = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="open"
        ).all()
        self.assertEqual(len(open_incidents), 1)

    def test_unknown_to_down_opens_incident(self):
        svc.handle_transition(self.db, self.app_id, "unknown", "down")
        open_incidents = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="open"
        ).all()
        self.assertEqual(len(open_incidents), 1)

    def test_down_again_does_not_double_open(self):
        svc.handle_transition(self.db, self.app_id, "up", "down")
        svc.handle_transition(self.db, self.app_id, "down", "down")
        open_incidents = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="open"
        ).all()
        self.assertEqual(len(open_incidents), 1)

    def test_down_to_up_resolves_open(self):
        svc.handle_transition(self.db, self.app_id, "up", "down")
        svc.handle_transition(self.db, self.app_id, "down", "up")
        open_incidents = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="open"
        ).all()
        self.assertEqual(len(open_incidents), 0)
        resolved = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="resolved"
        ).all()
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].resolved_by, "system")

    def test_down_to_up_with_no_incident_is_noop(self):
        # Can happen if someone manually cleared incidents, or on first deploy.
        svc.handle_transition(self.db, self.app_id, "down", "up")
        all_incidents = self.db.query(Incident).filter_by(
            application_id=self.app_id
        ).all()
        self.assertEqual(len(all_incidents), 0)

    def test_down_to_up_resolves_acknowledged(self):
        svc.handle_transition(self.db, self.app_id, "up", "down")
        inc = self.db.query(Incident).filter_by(
            application_id=self.app_id, status="open"
        ).one()
        svc.acknowledge_incident(self.db, inc.id, by="alice")
        svc.handle_transition(self.db, self.app_id, "down", "up")
        self.assertEqual(
            self.db.get(Incident, inc.id).status, "resolved"
        )


class TestListIncidents(IncidentTestBase):

    def test_list_empty(self):
        rows, total = svc.list_incidents(self.db, application_id=self.app_id)
        self.assertEqual(rows, [])
        self.assertEqual(total, 0)

    def test_list_by_application(self):
        svc.open_incident(self.db, self.app_id)
        svc.resolve_incident(
            self.db,
            self.db.query(Incident).one().id,
        )
        svc.open_incident(self.db, self.app_id)
        rows, total = svc.list_incidents(self.db, application_id=self.app_id)
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)

    def test_list_filter_by_status(self):
        svc.handle_transition(self.db, self.app_id, "up", "down")
        svc.handle_transition(self.db, self.app_id, "down", "up")
        svc.handle_transition(self.db, self.app_id, "up", "down")
        open_rows, open_total = svc.list_incidents(
            self.db, application_id=self.app_id, status="open"
        )
        resolved_rows, resolved_total = svc.list_incidents(
            self.db, application_id=self.app_id, status="resolved"
        )
        self.assertEqual(open_total, 1)
        self.assertEqual(resolved_total, 1)


if __name__ == "__main__":
    unittest.main()


class TestWorkerIncidentIntegration(IncidentTestBase):
    """Verifies the worker's on_transition hook actually creates and resolves incidents."""

    def setUp(self):
        super().setUp()
        from app.models.monitoring import ApplicationMonitoring
        from datetime import datetime, timezone, timedelta

        # Enable monitoring with threshold 1 for immediate transitions
        mon = ApplicationMonitoring(
            application_id=self.app_id,
            enabled=True,
            interval_seconds=300,
            failure_threshold=1,
            next_check_at=datetime.now(timezone.utc) - timedelta(seconds=60)
        )
        self.db.add(mon)
        self.db.commit()

    def test_worker_down_then_up_transitions_incident(self):
        from app.worker.loop import run_once
        from app.monitoring.probe import ProbeOutcome
        from app.models.monitoring import ApplicationMonitoring
        from datetime import datetime, timezone, timedelta

        # 1. Fake probe: down
        def fake_probe_down(url):
            return ProbeOutcome(status="down", failure_reason="timeout")

        worked = run_once(self.db, probe_fn=fake_probe_down)
        self.assertTrue(worked)

        # Verify incident opened
        incidents = self.db.query(Incident).filter_by(application_id=self.app_id).all()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].status, "open")

        # 2. Advance time and fake probe: up
        mon = self.db.get(ApplicationMonitoring, self.app_id)
        mon.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        self.db.commit()

        def fake_probe_up(url):
            return ProbeOutcome(status="up")

        worked = run_once(self.db, probe_fn=fake_probe_up)
        self.assertTrue(worked)

        # Verify incident resolved
        incidents = self.db.query(Incident).filter_by(application_id=self.app_id).all()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].status, "resolved")
        self.assertEqual(incidents[0].resolved_by, "system")


class TestIncidentDashboardIntegration(IncidentTestBase):
    """Dashboard endpoints for incidents — Phase 13.2. Requires auth.

    Runs inside the api container. All HTTP requests go through the
    container-side auth client (localhost:8000, direct-DB user seeding).
    """

    def test_incident_panel_renders(self):
        from tests.support.container_auth import api_request, get_html

        status, client = api_request(
            "/clients", "POST", {"name": f"dash-{uuid.uuid4().hex[:6]}"}
        )
        self.assertEqual(status, 201, client)
        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "dash-app", "base_url": "https://1.1.1.1/",
             "environment": "production"},
        )
        self.assertEqual(status, 201, app)

        svc.open_incident(self.db, app["id"], reason="Test incident")
        self.db.commit()

        status, html = get_html(f"/dashboard/clients/{client['id']}")
        self.assertEqual(status, 200)
        self.assertIn("Recent Incidents", html)
        self.assertIn("OPEN", html)
        self.assertIn("Acknowledge", html)
        self.assertIn("Resolve", html)

    def test_acknowledge_incident_via_dashboard(self):
        from tests.support.container_auth import api_request, post_form

        status, client = api_request(
            "/clients", "POST", {"name": f"dash-{uuid.uuid4().hex[:6]}"}
        )
        self.assertEqual(status, 201, client)
        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "dash-app", "base_url": "https://1.1.1.1/",
             "environment": "production"},
        )
        self.assertEqual(status, 201, app)

        inc = svc.open_incident(self.db, app["id"], reason="Test incident")
        self.db.commit()
        inc_id = inc.id

        # Dashboard POST — require_dashboard_user enforces CSRF via post_form helper.
        # We don't check the status (it's a 303 redirect that post_form doesn't
        # follow); we verify DB state instead.
        post_form(f"/dashboard/incidents/{inc_id}/acknowledge", {})

        self.db.expire_all()
        updated_inc = self.db.get(Incident, inc_id)
        self.assertEqual(updated_inc.status, "acknowledged")
        # Phase 13.2: actor is the authenticated user's email, not a hardcoded
        # "Dashboard User" string. The container auth client seeds
        # container-test@example.com.
        self.assertEqual(updated_inc.acknowledged_by, "container-test@example.com")


class TestNotifications(IncidentTestBase):

    def test_open_incident_sends_email(self):
        from app.models.notification import NotificationLog
        
        inc = svc.open_incident(self.db, self.app_id, reason="Manual")
        self.db.commit()

        logs = self.db.query(NotificationLog).filter_by(incident_id=inc.id).all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].status, "sent")
        self.assertEqual(logs[0].notification_type, "incident_opened")

    def test_resolve_incident_sends_email(self):
        from app.models.notification import NotificationLog
        
        inc = svc.open_incident(self.db, self.app_id, reason="Manual")
        self.db.commit()
        
        svc.resolve_incident(self.db, inc.id, by="Test", notes="Fixed")
        self.db.commit()

        logs = self.db.query(NotificationLog).filter_by(incident_id=inc.id).all()
        self.assertEqual(len(logs), 2) # One for open, one for resolve
        self.assertEqual(logs[1].status, "sent")
        self.assertEqual(logs[1].notification_type, "incident_resolved")


class TestNotificationDashboardIntegration(IncidentTestBase):
    """Notification logs on the dashboard — Phase 13.2. Requires auth."""

    def test_notification_log_renders_in_incident_table(self):
        from tests.support.container_auth import api_request, get_html

        status, client = api_request(
            "/clients", "POST", {"name": f"notif-dash-{uuid.uuid4().hex[:6]}"}
        )
        self.assertEqual(status, 201, client)
        status, app = api_request(
            f"/clients/{client['id']}/applications", "POST",
            {"name": "notif-app", "base_url": "https://1.1.1.1/",
             "environment": "production"},
        )
        self.assertEqual(status, 201, app)

        # open_incident triggers notification log automatically
        svc.open_incident(self.db, app["id"], reason="Test incident")
        self.db.commit()

        status, html = get_html(f"/dashboard/clients/{client['id']}")
        self.assertEqual(status, 200)
        self.assertIn("Alerts Sent", html)
        self.assertIn("ops@agency.local", html)
        self.assertIn("\u2713", html)  # green checkmark

