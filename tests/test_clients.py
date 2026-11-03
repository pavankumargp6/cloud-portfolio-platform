"""Client API tests — Phase 13.2.

Authentication is now required. All requests go through
`tests.support.auth_client.api_request`, which logs in as the shared
test user (Default Agency) and injects the CSRF header on state-changing
methods. Test bodies are unchanged from Phase 13.1.
"""
import unittest
import uuid

from tests.support.auth_client import api_request


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


class TestClientAgencyContract(unittest.TestCase):
    """HTTP-level /clients contract — Phase 13.2 authentication + agency rules.

    Three independent guarantees:
      1. Anonymous POST /clients is rejected before schema validation (401).
      2. Authenticated POST with agency_id is rejected by schema (422).
      3. Authenticated POST without agency_id succeeds and uses
         current_user.agency_id (matches the reference client's agency_id).
    """

    def test_anonymous_post_rejected_401(self):
        from tests.support.auth_client import api_request_anon
        status, _ = api_request_anon(
            "/clients", "POST", {"name": "anon-attempt"}
        )
        self.assertEqual(status, 401)

    def test_authenticated_post_with_agency_id_rejected_422(self):
        # Any integer here — schema rejects it before any lookup
        status, _ = api_request(
            "/clients",
            "POST",
            {"name": "rejected-6d7", "agency_id": 999},
        )
        self.assertEqual(status, 422)

    def test_authenticated_post_without_agency_id_uses_current_user_agency(self):
        # Create a reference client via the same authenticated path
        ref_name = f"ref-{uuid.uuid4().hex[:8]}"
        status, ref = api_request("/clients", "POST", {"name": ref_name})
        self.assertEqual(status, 201, ref)
        self.assertIsInstance(ref["agency_id"], int)
        self.assertGreater(ref["agency_id"], 0)

        # The dashboard path (also authenticated) must land on the same agency
        from tests.support.auth_client import AuthClient
        client = AuthClient()
        client._login()
        import urllib.request
        import urllib.parse
        dash_name = f"dash-{uuid.uuid4().hex[:8]}"
        body = urllib.parse.urlencode(
            {"name": dash_name, "contact_email": "", "notes": "",
             "_csrf_token": client.csrf}
        ).encode()
        req = urllib.request.Request(
            "http://localhost:8080/dashboard/clients", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(client.jar)
        )
        with opener.open(req, timeout=5):
            pass

        # Find the dashboard-created client via the authenticated API
        found = None
        offset = 0
        while True:
            st, page = api_request(f"/clients?limit=100&offset={offset}")
            for c in page["items"]:
                if c["name"] == dash_name:
                    found = c
                    break
            if found is not None:
                break
            offset += page["limit"]
            if offset >= page["total"]:
                break
        self.assertIsNotNone(found, f"dashboard-created client {dash_name!r} not found")
        self.assertEqual(
            found["agency_id"],
            ref["agency_id"],
            "Dashboard-created client agency_id must match API path",
        )


if __name__ == "__main__":
    unittest.main()
