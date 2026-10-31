import json
import unittest
import urllib.error
import urllib.request
import uuid

BASE_URL = "http://localhost:8080"


def api_request(path, method="GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, (json.loads(body) if body else None)


class TestApplications(unittest.TestCase):
    def setUp(self):
        status, client = api_request(
            "/clients", "POST", {"name": f"c-{uuid.uuid4().hex[:8]}"}
        )
        self.assertEqual(status, 201, client)
        self.client = client

    def _create_app(self, **overrides):
        payload = {
            "name": "app",
            "base_url": "https://example.com",
            "environment": "production",
        }
        payload.update(overrides)
        return api_request(
            f"/clients/{self.client['id']}/applications", "POST", payload
        )

    def test_create_and_fetch(self):
        status, application = self._create_app()
        self.assertEqual(status, 201, application)
        self.assertEqual(application["status"], "active")
        status, fetched = api_request(f"/applications/{application['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["id"], application["id"])

    def test_create_under_missing_client_404(self):
        status, _ = api_request(
            "/clients/999999999/applications",
            "POST",
            {
                "name": "x",
                "base_url": "https://example.com",
                "environment": "production",
            },
        )
        self.assertEqual(status, 404)

    def test_invalid_url_422(self):
        for bad in ("notaurl", "ftp://example.com", "http://", "javascript:alert(1)"):
            status, _ = self._create_app(base_url=bad)
            self.assertEqual(status, 422, bad)

    def test_invalid_enum_422(self):
        status, _ = self._create_app(environment="productionn")
        self.assertEqual(status, 422)
        status, _ = self._create_app(status="broken")
        self.assertEqual(status, 422)

    def test_unknown_field_422(self):
        status, _ = self._create_app(bogus=1)
        self.assertEqual(status, 422)

    def test_list_by_client_pagination(self):
        for _ in range(3):
            self.assertEqual(self._create_app()[0], 201)
        status, page = api_request(
            f"/clients/{self.client['id']}/applications?limit=2&offset=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(page["limit"], 2)
        self.assertLessEqual(len(page["items"]), 2)
        self.assertGreaterEqual(page["total"], 3)

    def test_list_all_pagination(self):
        status, page = api_request("/applications?limit=1&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(page["limit"], 1)
        self.assertLessEqual(len(page["items"]), 1)

    def test_patch_partial_update(self):
        _, application = self._create_app()
        status, updated = api_request(
            f"/applications/{application['id']}", "PATCH", {"status": "maintenance"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "maintenance")
        self.assertEqual(updated["name"], application["name"])

    def test_patch_rejects_null_required(self):
        _, application = self._create_app()
        for field in ("name", "base_url", "environment", "status"):
            status, _ = api_request(
                f"/applications/{application['id']}", "PATCH", {field: None}
            )
            self.assertEqual(status, 422, field)

    def test_patch_null_clears_description(self):
        _, application = self._create_app(description="d")
        status, updated = api_request(
            f"/applications/{application['id']}", "PATCH", {"description": None}
        )
        self.assertEqual(status, 200)
        self.assertIsNone(updated["description"])

    def test_delete_application(self):
        _, application = self._create_app()
        status, _ = api_request(f"/applications/{application['id']}", "DELETE")
        self.assertEqual(status, 204)
        status, _ = api_request(f"/applications/{application['id']}")
        self.assertEqual(status, 404)

    def test_delete_missing_application_404(self):
        status, _ = api_request("/applications/999999999", "DELETE")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
