import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = "http://localhost:8080"


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
