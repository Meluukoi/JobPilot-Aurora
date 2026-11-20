import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from collectors import fetch_greenhouse


class GreenhousePublicationTests(unittest.TestCase):

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.updated = self.now.isoformat()
        self.fresh = (self.now - timedelta(days=3)).isoformat()
        self.stale = (self.now - timedelta(days=35)).isoformat()

    def response(self, payload=None, status=200):
        response = Mock()
        response.status_code = status
        response.ok = status == 200
        response.json.return_value = payload or {}
        response.raise_for_status.return_value = None
        return response

    def listing(self):
        return {
            "id": 12345,
            "title": "IT Support Specialist",
            "updated_at": self.updated,
            "location": {"name": "San Diego, CA"},
            "absolute_url": "https://example.org/jobs/12345",
            "content": "Technical troubleshooting",
        }

    def fetch(self, published=None, verify=True, detail_status=200):
        board = self.response({"jobs": [self.listing()]})
        detail = self.response(
            {"first_published": published},
            status=detail_status,
        )

        with patch("collectors.requests.get", side_effect=[board, detail]) as get:
            jobs = fetch_greenhouse(
                "example",
                "Example Company",
                verify_first_published=verify,
            )

        return jobs, get.call_count

    def test_verified_recent_original_publication(self):
        jobs, calls = self.fetch(self.fresh)
        self.assertEqual(calls, 2)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["original_published_at"], self.fresh)
        self.assertIs(jobs[0]["original_publication_verified"], True)

    def test_stale_original_publication_retained(self):
        jobs, _ = self.fetch(self.stale)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["original_published_at"], self.stale)
        self.assertIs(jobs[0]["original_publication_verified"], True)

    def test_missing_original_publication_unverified(self):
        jobs, _ = self.fetch(None)
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0]["original_publication_verified"])

    def test_failed_detail_verification_retains_job(self):
        jobs, _ = self.fetch(None, detail_status=503)
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0]["original_publication_verified"])

    def test_fast_collection_does_not_claim_verification(self):
        jobs, calls = self.fetch(self.fresh, verify=False)
        self.assertEqual(calls, 1)
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0]["original_publication_verified"])


if __name__ == "__main__":
    unittest.main()
