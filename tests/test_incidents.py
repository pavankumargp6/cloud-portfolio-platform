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
        client = Client(name=f"inc-{suffix}")
        self.db.add(client)
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
    """Tests the dashboard endpoints for incidents (requires API to be running)."""
    
    def test_incident_panel_renders(self):
        import urllib.request
        import uuid

        # 1. Create a client and app via API
        import json
        BASE_URL = "http://localhost:8000"
        
        def api_post(path, data):
            req = urllib.request.Request(
                BASE_URL + path,
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())

        client = api_post("/clients", {"name": f"dash-{uuid.uuid4().hex[:6]}"})
        app = api_post(f"/clients/{client['id']}/applications", {
            "name": "dash-app",
            "base_url": "https://1.1.1.1/",
            "environment": "production"
        })
        
        # 2. Open incident directly in DB
        svc.open_incident(self.db, app["id"], reason="Test incident")
        self.db.commit()

        # 3. Fetch dashboard HTML
        req = urllib.request.Request(f"{BASE_URL}/dashboard/clients/{client['id']}")
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode()
            
        self.assertIn("Recent Incidents", html)
        self.assertIn("OPEN", html)
        self.assertIn("Acknowledge", html)
        self.assertIn("Resolve", html)

    def test_acknowledge_incident_via_dashboard(self):
        import urllib.request
        import urllib.parse
        import uuid
        import json
        BASE_URL = "http://localhost:8000"
        
        def api_post(path, data, form=False):
            headers = {}
            if form:
                req_data = urllib.parse.urlencode(data).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                req_data = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
            
            req = urllib.request.Request(
                BASE_URL + path,
                data=req_data,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                return resp.status

        # 1. Create client/app
        req = urllib.request.Request(
            BASE_URL + "/clients",
            data=json.dumps({"name": f"dash-{uuid.uuid4().hex[:6]}"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            client = json.loads(resp.read())
            
        req = urllib.request.Request(
            BASE_URL + f"/clients/{client['id']}/applications",
            data=json.dumps({
                "name": "dash-app",
                "base_url": "https://1.1.1.1/",
                "environment": "production"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            app = json.loads(resp.read())

        # 2. Open incident
        inc = svc.open_incident(self.db, app["id"], reason="Test incident")
        self.db.commit()
        inc_id = inc.id

        # 3. Hit dashboard acknowledge route
        status = api_post(f"/dashboard/incidents/{inc_id}/acknowledge", {})
        # Note: FastAPI RedirectResponse follows automatically in urllib if it's a GET, 
        # but this is a POST, so we get 200 (or 303/302). We don't check exact status,
        # just that it didn't crash.

        # 4. Verify DB state
        self.db.expire_all()
        updated_inc = self.db.get(Incident, inc_id)
        self.assertEqual(updated_inc.status, "acknowledged")
        self.assertEqual(updated_inc.acknowledged_by, "Dashboard User")
