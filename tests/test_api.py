"""Core API tests — Phase 13.2.

Health is public; /notes requires auth. Test bodies unchanged from
Phase 13.1 aside from the auth client swap.
"""
import unittest
import uuid

from tests.support.auth_client import api_request, api_request_anon


class TestCloudPortfolioAPI(unittest.TestCase):

    def test_health_endpoint(self):
        status, result = api_request_anon("/health")

        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["database"], "connected")

    def test_create_and_retrieve_note(self):
        content = f"automated-test-{uuid.uuid4().hex}"

        status, created = api_request(
            "/notes",
            "POST",
            {"content": content},
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
