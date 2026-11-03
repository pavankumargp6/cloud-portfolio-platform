"""Dashboard UI tests — Phase 13.2.

Authentication is required. Helpers use the shared auth client, which
logs in as the test user and auto-injects `_csrf_token` on form POSTs.
Test bodies are unchanged from Phase 13.1 except for three banner-text
assertions — the banner was changed in Patch 6c from
"Development prototype — single agency, no authentication, synthetic
data only" to "Phase 13.2 — single-agency scope; multi-tenant isolation
is Phase 13.3" because the old text was no longer accurate. Assertions
updated accordingly.
"""
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tests.support.auth_client import AuthClient, api_request, BASE_URL


# --- shared auth client -----------------------------------------------------

_client: AuthClient | None = None

def _get_client() -> AuthClient:
    global _client
    if _client is None:
        _client = AuthClient()
    _client._login()  # idempotent — ensures session + csrf are populated
    return _client


# --- helpers (drop-in replacements for the pre-13.2 versions) --------------

def http_get(path):
    """Authenticated GET — returns (status, html_text). Raises on 4xx/5xx."""
    client = _get_client()
    req = urllib.request.Request(BASE_URL + path)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(client.jar)
    )
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def http_post_form(path, data):
    """Authenticated form POST with CSRF — returns (status, html_text, final_url)."""
    client = _get_client()
    payload = dict(data)
    payload.setdefault("_csrf_token", client.csrf)
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(client.jar)
    )
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.geturl()
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8")
        exc.close()
        return exc.code, body_text, None


def api_json(path):
    """Authenticated JSON GET — returns parsed body. Caller checks response shape."""
    status, body = api_request(path)
    return body


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


# --- tests -----------------------------------------------------------------

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
        self.assertIn("Phase 13.2", body)
        self.assertIn("Clients", body)
        self.assertIn("Applications", body)
        self.assertIn("Applications by environment", body)

    def test_clients_list_page(self):
        status, body = http_get("/dashboard/clients")
        self.assertEqual(status, 200)
        self.assertIn("Add client", body)
        self.assertIn("<table", body)
        self.assertIn("Phase 13.2", body)

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
        self.assertIn("Phase 13.2", body)

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

    def test_create_client_form_assigns_current_user_agency(self):
        """Dashboard client creation must assign the current user's agency.

        Regression guard for CI run #40 (NOT NULL violation) AND for the
        Phase 13.2 contract that agency is derived from the auth session,
        not from a fallback resolver.
        """
        name = f"dash-agency-{uuid.uuid4().hex[:8]}"
        status, _, _ = http_post_form(
            "/dashboard/clients",
            {"name": name, "contact_email": "", "notes": ""},
        )
        self.assertEqual(status, 200)

        client = find_client_by_name(name)
        self.assertIsNotNone(client, "Dashboard did not create the client")
        self.assertIn("agency_id", client)
        self.assertIsInstance(client["agency_id"], int)
        self.assertGreater(client["agency_id"], 0)

        # Compare against a reference client created via the JSON API path.
        # Both use current_user.agency_id, so they must match.
        ref_name = f"ref-agency-{uuid.uuid4().hex[:8]}"
        ref_status, ref_client = api_request(
            "/clients", "POST", {"name": ref_name}
        )
        self.assertEqual(ref_status, 201, ref_client)
        self.assertEqual(
            client["agency_id"],
            ref_client["agency_id"],
            "Dashboard-created client has a different agency than the "
            "API path (both must use current_user.agency_id)",
        )

    def test_create_client_invalid_returns_422(self):
        status, body, _ = http_post_form(
            "/dashboard/clients", {"name": "", "contact_email": "", "notes": ""}
        )
        self.assertEqual(status, 422)
        self.assertIn("alert-error", body)
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


class TestMonitoringDashboard(unittest.TestCase):
    def setUp(self):
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
        status, html = http_get(f"/dashboard/clients/{self.client_id}")
        self.assertEqual(status, 200)
        self.assertIn(f'id="monitoring-panel-{self.app_id}"', html)
        self.assertIn("Monitoring: UNKNOWN", html)
        self.assertIn("Enable Monitoring", html)

    def test_enable_monitoring_via_dashboard(self):
        status, html, _ = http_post_form(
            f"/dashboard/applications/{self.app_id}/monitoring/enable", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("Disable Monitoring", html)

        status, body = api_request(f"/applications/{self.app_id}/monitoring")
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        self.assertIsNotNone(body["next_check_at"])

    def test_monitoring_checks_page_renders(self):
        status, html = http_get(f"/dashboard/applications/{self.app_id}/checks")
        self.assertEqual(status, 200)
        self.assertIn(f"Check History: {self.app_name}", html)
        self.assertIn("No checks yet.", html)

    def test_run_now_cooldown_error_renders(self):
        api_request(f"/applications/{self.app_id}/monitoring", "PATCH", {"enabled": True})
        status, html, _ = http_post_form(
            f"/dashboard/applications/{self.app_id}/monitoring/run-now", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("Cooldown active. Retry in", html)

    def test_ssrf_block_renders_error_in_panel(self):
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

        status, html, _ = http_post_form(
            f"/dashboard/applications/{bad_app_id}/monitoring/enable", {}
        )
        self.assertEqual(status, 200)
        self.assertIn("blocked_ip", html)


class TestReportingDashboard(unittest.TestCase):
    def setUp(self):
        self.client_name = f"report-ui-{uuid.uuid4().hex[:8]}"
        status, client = api_request("/clients", "POST", {"name": self.client_name})
        self.assertEqual(status, 201)
        self.client_id = client["id"]

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


if __name__ == "__main__":
    unittest.main()
