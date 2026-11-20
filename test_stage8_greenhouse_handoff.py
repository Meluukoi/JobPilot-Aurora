"""Offline tests for durable Greenhouse verification handoff."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from stage8_greenhouse_queue import (
    initialize,
    enqueue,
    mark_verified,
)
from stage8_greenhouse_handoff import (
    ready_for_handoff,
    mark_handed_off,
)


class GreenhouseHandoffTests(unittest.TestCase):

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        self.queue = Path(temporary.name) / "verification.db"
        self.now = datetime(2026, 11, 12, tzinfo=timezone.utc)

        initialize(self.queue)

        self.job = {
            "source": "Greenhouse",
            "source_id": "12345",
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": "https://example.org/jobs/12345",
        }

        self.published = (
            self.now - timedelta(days=3)
        ).isoformat()

    def verified_job(self):
        enqueue(self.queue, self.job, now=self.now)
        mark_verified(
            self.queue,
            self.job,
            self.published,
            now=self.now,
        )

    def test_pending_verification_not_ready(self):
        enqueue(self.queue, self.job, now=self.now)

        self.assertEqual(
            ready_for_handoff(self.queue),
            [],
        )

    def test_verified_candidate_ready(self):
        self.verified_job()

        ready = ready_for_handoff(self.queue)

        self.assertEqual(len(ready), 1)
        self.assertEqual(
            ready[0]["original_published_at"],
            self.published,
        )
        self.assertIs(
            ready[0]["original_publication_verified"],
            True,
        )

    def test_verified_candidate_survives_restart(self):
        self.verified_job()

        initialize(self.queue)

        self.assertEqual(
            len(ready_for_handoff(self.queue)),
            1,
        )

    def test_completed_handoff_not_repeated(self):
        self.verified_job()

        self.assertTrue(
            mark_handed_off(self.queue, self.job)
        )

        self.assertEqual(
            ready_for_handoff(self.queue),
            [],
        )

        self.assertFalse(
            mark_handed_off(self.queue, self.job)
        )

    def test_unverified_candidate_cannot_be_handed_off(self):
        enqueue(self.queue, self.job, now=self.now)

        self.assertFalse(
            mark_handed_off(self.queue, self.job)
        )

    def test_handoff_preserves_original_identity(self):
        self.verified_job()

        ready = ready_for_handoff(self.queue)

        self.assertEqual(
            ready[0]["source_id"],
            self.job["source_id"],
        )
        self.assertEqual(
            ready[0]["url"],
            self.job["url"],
        )


if __name__ == "__main__":
    unittest.main()
