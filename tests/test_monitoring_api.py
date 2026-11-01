"""HTTP integration tests for monitoring endpoints."""

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


def _create_app(base_url="https://1.1.1.1/"):
    client_name = f"mon-{uuid.uuid4().hex[:8]}"
    status, client = api_request("/clients", "POST", {"name": client_name})
    assert status == 201, client
    status, app_obj = api_request(
        f"/clients/{client['id']}/applications",
        "POST",
        {"name": "app", "base_url": base_url, "environment": "production"},
    )
    assert status == 201, app_obj
    return app_obj


class TestMonitoringRead(unittest.TestCase):

    def test_get_monitoring_404(self):
        status, _ = api_request("/applications/999999999/monitoring")
        self.assertEqual(status, 404)

    def test_get_monitoring_defaults(self):
        app = _create_app()
        status, body = api_request(f"/applications/{app['id']}/monitoring")
        self.assertEqual(status, 200)
        self.assertFalse(body["enabled"])
        self.assertEqual(body["interval_seconds"], 300)
        self.assertEqual(body["failure_threshold"], 3)
        self.assertEqual(body["current_status"], "unknown")
        self.assertEqual(body["consecutive_failures"], 0)
        self.assertIsNone(body["next_check_at"])
        self.assertFalse(body["cleartext_warning"])

    def test_get_monitoring_cleartext_flag(self):
        app = _create_app(base_url="http://example.com/")
        status, body = api_request(f"/applications/{app['id']}/monitoring")
        self.assertEqual(status, 200)
        self.assertTrue(body["cleartext_warning"])


class TestMonitoringPatch(unittest.TestCase):

    def test_patch_enable_happy_path(self):
        app = _create_app()
        status, body = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"enabled": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        self.assertIsNotNone(body["next_check_at"])

    def test_patch_enable_ssrf_loopback(self):
        app = _create_app(base_url="http://127.0.0.1/")
        status, body = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"enabled": True},
        )
        self.assertEqual(status, 422)
        detail = body.get("detail") or {}
        self.assertEqual(detail.get("code"), "blocked_ip")

    def test_patch_enable_ssrf_port(self):
        # base_url passes M1's URL check (scheme + netloc), port not allowed
        client_name = f"mon-{uuid.uuid4().hex[:8]}"
        _, client = api_request("/clients", "POST", {"name": client_name})
        status, app_obj = api_request(
            f"/clients/{client['id']}/applications",
            "POST",
            {
                "name": "weird",
                "base_url": "http://example.com:22/",
                "environment": "production",
            },
        )
        self.assertEqual(status, 201)
        status, body = api_request(
            f"/applications/{app_obj['id']}/monitoring",
            "PATCH",
            {"enabled": True},
        )
        self.assertEqual(status, 422)
        self.assertEqual((body.get("detail") or {}).get("code"), "blocked_port")

    def test_patch_interval_too_small(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"interval_seconds": 30},
        )
        self.assertEqual(status, 422)

    def test_patch_interval_too_large(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"interval_seconds": 100000},
        )
        self.assertEqual(status, 422)

    def test_patch_threshold_too_small(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"failure_threshold": 0},
        )
        self.assertEqual(status, 422)

    def test_patch_threshold_too_large(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"failure_threshold": 11},
        )
        self.assertEqual(status, 422)

    def test_patch_unknown_field(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"bogus": 1},
        )
        self.assertEqual(status, 422)

    def test_patch_explicit_null_rejected(self):
        app = _create_app()
        status, _ = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"interval_seconds": None},
        )
        self.assertEqual(status, 422)

    def test_patch_disable_clears_next_check(self):
        app = _create_app()
        api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"enabled": True},
        )
        status, body = api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"enabled": False},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["enabled"])
        self.assertIsNone(body["next_check_at"])


class TestMonitoringChecks(unittest.TestCase):

    def test_checks_list_empty(self):
        app = _create_app()
        status, body = api_request(f"/applications/{app['id']}/checks")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])
        self.assertEqual(body["limit"], 20)
        self.assertEqual(body["offset"], 0)

    def test_checks_list_404(self):
        status, _ = api_request("/applications/999999999/checks")
        self.assertEqual(status, 404)

    def test_checks_list_pagination_rejected(self):
        app = _create_app()
        status, _ = api_request(f"/applications/{app['id']}/checks?limit=200")
        self.assertEqual(status, 422)
        status, _ = api_request(f"/applications/{app['id']}/checks?offset=-1")
        self.assertEqual(status, 422)

    def test_checks_latest_empty(self):
        app = _create_app()
        status, body = api_request(f"/applications/{app['id']}/checks/latest")
        self.assertEqual(status, 200)
        self.assertEqual(body, [])


class TestMonitoringRunNow(unittest.TestCase):

    def test_run_now_404(self):
        status, _ = api_request(
            "/applications/999999999/checks/run-now", "POST"
        )
        self.assertEqual(status, 404)

    def test_run_now_409_when_disabled(self):
        app = _create_app()
        status, body = api_request(
            f"/applications/{app['id']}/checks/run-now", "POST"
        )
        self.assertEqual(status, 409)
        self.assertEqual((body.get("detail") or {}).get("code"), "monitoring_disabled")

    def test_run_now_429_when_queued(self):
        # Enabling sets next_check_at = now + 5s. run-now within that window
        # cannot queue another check — the queue guard blocks it.
        # Full 202-path coverage is in Phase 11 (worker + wait).
        app = _create_app()
        api_request(
            f"/applications/{app['id']}/monitoring",
            "PATCH",
            {"enabled": True},
        )
        status, body = api_request(
            f"/applications/{app['id']}/checks/run-now", "POST"
        )
        self.assertEqual(status, 429)
        detail = body.get("detail") or {}
        self.assertEqual(detail.get("code"), "cooldown_active")
        self.assertIn("retry_after", detail)
        self.assertGreater(detail["retry_after"], 0)


if __name__ == "__main__":
    unittest.main()
