import json
import unittest
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
        BASE_URL + path,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


class TestCloudPortfolioAPI(unittest.TestCase):

    def test_health_endpoint(self):
        status, result = api_request("/health")

        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["database"], "connected")

    def test_create_and_retrieve_note(self):
        content = f"automated-test-{uuid.uuid4().hex}"

        status, created = api_request(
            "/notes",
            method="POST",
            payload={"content": content},
        )

        self.assertEqual(status, 200)
        self.assertEqual(created["content"], content)
        self.assertIn("id", created)

        status, result = api_request("/notes")

        self.assertEqual(status, 200)

        matching_notes = [
            note for note in result["notes"]
            if note["content"] == content
        ]

        self.assertTrue(matching_notes)


if __name__ == "__main__":
    unittest.main()
