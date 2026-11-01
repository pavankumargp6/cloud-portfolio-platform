import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = "http://localhost:8080"

def api_request(path, method="GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return exc.code, (json.loads(body) if body else None)



def http_get(path):
    req = urllib.request.Request(BASE_URL + path)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def http_post_form(path, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.geturl()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        exc.close()
        return exc.code, body, None


def api_json(path):
    with urllib.request.urlopen(BASE_URL + path, timeout=5) as resp:
        return json.loads(resp.read())


def find_client_by_name(name):
    """Page through /clients to find a client by name. Size-independent."""
    offset = 0
    while True:
        page = api_json(f"/clients?limit=100&offset={offset}")
        for client in page["items"]:
            if client["name"] == name:
                return client
        offset += page["limit"]
        if offset >= page["total"]:
            return None


class TestDashboard(unittest.TestCase):

    def test_existing_json_endpoints_preserved(self):
        status, body = http_get("/")
        self.assertEqual(status, 200)
        self.assertIn("Cloud Portfolio API is running", body)

        status, body = http_get("/health")
        self.assertEqual(status, 200)
        self.assertIn("connected", body)

        status, body = http_get("/notes")
        self.assertEqual(status, 200)
        self.assertIn("notes", body)

    def test_dashboard_home_renders(self):
        status, body = http_get("/dashboard/")
        self.assertEqual(status, 200)
        self.assertIn("Development prototype", body)
        self.assertIn("Clients", body)
        self.assertIn("Applications", body)
        self.assertIn("Applications by environment", body)

    def test_clients_list_page(self):
        status, body = http_get("/dashboard/clients")
        self.assertEqual(status, 200)
        self.assertIn("Add client", body)
        self.assertIn("<table", body)
        self.assertIn("Development prototype", body)

    def test_client_detail_page(self):
        name = f"detail-{uuid.uuid4().hex[:8]}"
        status, _, _ = http_post_form(
            "/dashboard/clients",
            {"name": name, "contact_email": "", "notes": ""},
        )
        self.assertEqual(status, 200)
        client = find_client_by_name(name)
        self.assertIsNotNone(client, f"created client {name!r} not found in API")

        status, body = http_get(f"/dashboard/clients/{client['id']}")
        self.assertEqual(status, 200)
        self.assertIn(name, body)
        self.assertIn("Add application", body)
        self.assertIn("Development prototype", body)

    def test_client_detail_404(self):
        status, _, _ = http_post_form(
            "/dashboard/clients",
            {"name": f"x-{uuid.uuid4().hex[:8]}", "contact_email": "", "notes": ""},
        )
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            http_get("/dashboard/clients/999999999")
        self.assertEqual(ctx.exception.code, 404)

    def test_create_client_form(self):
        name = f"dash-{uuid.uuid4().hex[:8]}"
        status, _, final_url = http_post_form(
            "/dashboard/clients",
            {"name": name, "contact_email": "ops@example.com", "notes": "via form"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(final_url.endswith("/dashboard/clients"))
        client = find_client_by_name(name)
        self.assertIsNotNone(client)
        self.assertEqual(client["contact_email"], "ops@example.com")

    def test_create_client_invalid_returns_422(self):
        status, body, _ = http_post_form(
            "/dashboard/clients", {"name": "", "contact_email": "", "notes": ""}
        )
        self.assertEqual(status, 422)
        self.assertIn("alert-error", body)
        # Page-level error should appear exactly once, not per row
        self.assertEqual(body.count('alert alert-error'), 1,
                         "page-level error leaked into client rows")

    def test_create_application_form(self):
        name = f"appclient-{uuid.uuid4().hex[:8]}"
        http_post_form(
            "/dashboard/clients",
            {"name": name, "contact_email": "", "notes": ""},
        )
        client = find_client_by_name(name)
        self.assertIsNotNone(client)

        status, _, final_url = http_post_form(
            f"/dashboard/clients/{client['id']}/applications",
            {
                "name": "web",
                "base_url": "https://example.com",
                "environment": "production",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn(f"/dashboard/clients/{client['id']}", final_url)
        apps = api_json(f"/clients/{client['id']}/applications")
        self.assertEqual(apps["total"], 1)
        self.assertEqual(apps["items"][0]["name"], "web")

    def test_invalid_application_url_returns_422(self):
        name = f"badurl-{uuid.uuid4().hex[:8]}"
        http_post_form(
            "/dashboard/clients",
            {"name": name, "contact_email": "", "notes": ""},
        )
        client = find_client_by_name(name)
        self.assertIsNotNone(client)

        status, body, _ = http_post_form(
            f"/dashboard/clients/{client['id']}/applications",
            {
                "name": "bad",
                "base_url": "not-a-url",
                "environment": "production",
                "status": "active",
                "description": "",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("alert-error", body)


if __name__ == "__main__":
    unittest.main()


# =============================================================================
# Phase 8.3 — Monitoring Dashboard UI Tests
# =============================================================================

class TestMonitoringDashboard(unittest.TestCase):
    def setUp(self):
        # Create a test client and app for each test
        self.client_name = f"mon-ui-{uuid.uuid4().hex[:8]}"
        status, client = api_request("/clients", "POST", {"name": self.client_name})
        self.assertEqual(status, 201)
        self.client_id = client["id"]

        self.app_name = f"app-{uuid.uuid4().hex[:8]}"
        status, app_obj = api_request(
            f"/clients/{self.client_id}/applications",
            "POST",
            {
                "name": self.app_name,
                "base_url": "https://1.1.1.1/",
                "environment": "production",
            },
        )
        self.assertEqual(status, 201)
        self.app_id = app_obj["id"]

    def test_client_detail_shows_monitoring_panel(self):
        # Get the client detail page
        status, html = http_get(f"/dashboard/clients/{self.client_id}")
        self.assertEqual(status, 200)
        # Assert the panel ID is present
        self.assertIn(f'id="monitoring-panel-{self.app_id}"', html)
        # Assert the default state (disabled)
        self.assertIn("Monitoring: UNKNOWN", html)
        self.assertIn("Enable Monitoring", html)

    def test_enable_monitoring_via_dashboard(self):
        # Enable via the dashboard HTMX endpoint
        status, html, _ = http_post_form(
            f"/dashboard/applications/{self.app_id}/monitoring/enable", {}
        )
        self.assertEqual(status, 200)
        # The returned fragment should now show "Disable Monitoring"
        self.assertIn("Disable Monitoring", html)

        # Verify via the JSON API that it actually persisted
        status, body = api_request(f"/applications/{self.app_id}/monitoring")
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        self.assertIsNotNone(body["next_check_at"])

    def test_monitoring_checks_page_renders(self):
        # Hit the new checks page
        status, html = http_get(f"/dashboard/applications/{self.app_id}/checks")
        self.assertEqual(status, 200)
        self.assertIn(f"Check History: {self.app_name}", html)
        self.assertIn("No checks yet.", html)

    def test_run_now_cooldown_error_renders(self):
        # Enable monitoring first
        api_request(f"/applications/{self.app_id}/monitoring", "PATCH", {"enabled": True})
        
        # Hit run-now immediately (within the 5s grace period)
        status, html, _ = http_post_form(
            f"/dashboard/applications/{self.app_id}/monitoring/run-now", {}
        )
        self.assertEqual(status, 200)
        # It should render the error in the panel
        self.assertIn("Cooldown active. Retry in", html)

    def test_ssrf_block_renders_error_in_panel(self):
        # Create an app with a private URL (bypassing M1 validation? M1 allows 127.0.0.1)
        status, app_obj = api_request(
            f"/clients/{self.client_id}/applications",
            "POST",
            {
                "name": "bad-app",
                "base_url": "http://127.0.0.1/",
                "environment": "production",
            },
        )
        self.assertEqual(status, 201)
        bad_app_id = app_obj["id"]

        # Try to enable monitoring for it via the dashboard
        status, html, _ = http_post_form(
            f"/dashboard/applications/{bad_app_id}/monitoring/enable", {}
        )
        self.assertEqual(status, 200)  # HTMX route returns 200 with error fragment
        # The panel should show the SSRF error
        self.assertIn("blocked_ip", html)


class TestReportingDashboard(unittest.TestCase):
    def setUp(self):
        self.client_name = f"report-ui-{uuid.uuid4().hex[:8]}"
        status, client = api_request("/clients", "POST", {"name": self.client_name})
        self.assertEqual(status, 201)
        self.client_id = client["id"]

        # Create at least one application so the report has data to render
        status, app_obj = api_request(
            f"/clients/{self.client_id}/applications",
            "POST",
            {
                "name": "report-app",
                "base_url": "https://1.1.1.1/",
                "environment": "production",
            },
        )
        self.assertEqual(status, 201)

    def test_report_page_renders(self):
        status, html = http_get(f"/dashboard/clients/{self.client_id}/report")
        self.assertEqual(status, 200)
        self.assertIn("Reliability Report", html)
        self.assertIn(self.client_name, html)
        self.assertIn("Uptime", html)
