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


class TestClients(unittest.TestCase):
    def _create_client(self):
        name = f"client-{uuid.uuid4().hex[:8]}"
        status, created = api_request("/clients", "POST", {"name": name})
        self.assertEqual(status, 201, created)
        return created

    def test_create_and_get(self):
        created = self._create_client()
        status, fetched = api_request(f"/clients/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["id"], created["id"])
        self.assertEqual(fetched["name"], created["name"])

    def test_list_pagination_shape(self):
        status, page = api_request("/clients?limit=5&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(page["limit"], 5)
        self.assertEqual(page["offset"], 0)
        self.assertLessEqual(len(page["items"]), 5)
        self.assertIn("total", page)

    def test_pagination_rejects_bad_values(self):
        status, _ = api_request("/clients?limit=101")
        self.assertEqual(status, 422)
        status, _ = api_request("/clients?offset=-1")
        self.assertEqual(status, 422)

    def test_patch_partial_update(self):
        created = self._create_client()
        status, updated = api_request(
            f"/clients/{created['id']}", "PATCH", {"notes": "hello"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["notes"], "hello")
        self.assertEqual(updated["name"], created["name"])

    def test_patch_null_clears_nullable(self):
        status, created = api_request(
            "/clients", "POST", {"name": "x", "notes": "y"}
        )
        self.assertEqual(status, 201)
        status, updated = api_request(
            f"/clients/{created['id']}", "PATCH", {"notes": None}
        )
        self.assertEqual(status, 200)
        self.assertIsNone(updated["notes"])

    def test_patch_rejects_null_required(self):
        created = self._create_client()
        status, _ = api_request(
            f"/clients/{created['id']}", "PATCH", {"name": None}
        )
        self.assertEqual(status, 422)

    def test_patch_rejects_unknown_field(self):
        created = self._create_client()
        status, _ = api_request(
            f"/clients/{created['id']}", "PATCH", {"bogus": 1}
        )
        self.assertEqual(status, 422)

    def test_get_missing_client_404(self):
        status, _ = api_request("/clients/999999999")
        self.assertEqual(status, 404)

    def test_delete_client_without_applications(self):
        created = self._create_client()
        status, _ = api_request(f"/clients/{created['id']}", "DELETE")
        self.assertEqual(status, 204)
        status, _ = api_request(f"/clients/{created['id']}")
        self.assertEqual(status, 404)

    def test_delete_client_with_applications_conflict(self):
        created = self._create_client()
        status, app_obj = api_request(
            f"/clients/{created['id']}/applications",
            "POST",
            {
                "name": "app",
                "base_url": "https://example.com",
                "environment": "production",
            },
        )
        self.assertEqual(status, 201, app_obj)
        status, _ = api_request(f"/clients/{created['id']}", "DELETE")
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
