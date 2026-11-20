import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from stage8_greenhouse_queue import (
    initialize,
    enqueue,
    due_candidates,
    mark_verified,
    mark_failed,
)


class GreenhouseQueueTests(unittest.TestCase):

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "verification.db"
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        initialize(self.path)

    def candidate(self, job_id="123"):
        return {
            "source": "Greenhouse",
            "source_id": job_id,
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": f"https://example.org/jobs/{job_id}",
        }

    def test_enqueue_is_idempotent(self):
        enqueue(self.path, self.candidate(), now=self.now)
        enqueue(self.path, self.candidate(), now=self.now)
        self.assertEqual(
            len(due_candidates(self.path, now=self.now)), 1
        )

    def test_pending_survives_reinitialization(self):
        enqueue(self.path, self.candidate(), now=self.now)
        initialize(self.path)
        self.assertEqual(
            len(due_candidates(self.path, now=self.now)), 1
        )

    def test_due_selection_respects_budget(self):
        for number in range(10):
            enqueue(
                self.path,
                self.candidate(str(number)),
                now=self.now,
            )
        self.assertEqual(
            len(due_candidates(
                self.path, now=self.now, limit=2
            )),
            2,
        )

    def test_verified_candidate_not_retried(self):
        enqueue(self.path, self.candidate(), now=self.now)
        published = (
            self.now - timedelta(days=3)
        ).isoformat()

        mark_verified(
            self.path, self.candidate(),
            published, now=self.now,
        )

        self.assertEqual(
            due_candidates(self.path, now=self.now), []
        )

    def test_failed_candidate_retries_later(self):
        enqueue(self.path, self.candidate(), now=self.now)

        mark_failed(
            self.path, self.candidate(),
            now=self.now,
        )

        self.assertEqual(
            due_candidates(self.path, now=self.now), []
        )

        later = self.now + timedelta(days=2)

        self.assertEqual(
            len(due_candidates(self.path, now=later)), 1
        )

    def test_distinct_requisitions_remain_distinct(self):
        enqueue(self.path, self.candidate("123"), now=self.now)
        enqueue(self.path, self.candidate("456"), now=self.now)

        self.assertEqual(
            len(due_candidates(self.path, now=self.now)), 2
        )


if __name__ == "__main__":
    unittest.main()
