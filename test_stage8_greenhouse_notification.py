"""Offline tests for Greenhouse verified-candidate notification screening."""

import unittest
from datetime import datetime, timedelta, timezone

from stage8_greenhouse_notification import build_notification_matches


class GreenhouseNotificationTests(unittest.TestCase):

    def setUp(self):
        self.fresh = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()
        self.stale = (
            datetime.now(timezone.utc) - timedelta(days=35)
        ).isoformat()

    def job(self, job_id="123", published=None, verified=True):
        return {
            "source": "Greenhouse",
            "source_id": job_id,
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": f"https://example.org/jobs/{job_id}",
            "original_published_at": published or self.fresh,
            "original_publication_verified": verified,
        }

    @staticmethod
    def scorer(job, profile):
        blocked = "London" in job["location"]
        return {
            "score": 80,
            "matched_family": "IT Support",
            "geo_status": "blocked" if blocked else "local",
            "geo_reason": "Test geography",
            "recommendation": "BLOCKED" if blocked else "APPLY",
        }

    def matches(self, jobs, saved_rows=()):
        return build_notification_matches(
            jobs,
            profile={},
            saved_rows=list(saved_rows),
            scorer=self.scorer,
        )

    def test_verified_fresh_candidate_eligible(self):
        self.assertEqual(len(self.matches([self.job()])), 1)

    def test_stale_candidate_excluded(self):
        self.assertEqual(
            self.matches([self.job(published=self.stale)]),
            [],
        )

    def test_unverified_candidate_excluded(self):
        self.assertEqual(
            self.matches([self.job(verified=False)]),
            [],
        )

    def test_blocked_geography_excluded(self):
        job = self.job()
        job["location"] = "London"
        self.assertEqual(self.matches([job]), [])

    def test_applied_candidate_excluded(self):
        job = self.job()
        saved = dict(job, status="Applied")
        self.assertEqual(self.matches([job], [saved]), [])

    def test_duplicate_candidate_not_repeated(self):
        job = self.job()
        self.assertEqual(
            len(self.matches([job, dict(job)])),
            1,
        )


if __name__ == "__main__":
    unittest.main()
