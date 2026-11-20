"""Offline Greenhouse verification recovery regression."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from stage8_greenhouse_queue import (
    initialize, enqueue, due_candidates,
    mark_failed, mark_verified,
)
from stage8_greenhouse_verification import verify_candidates


class GreenhouseRetryIntegrationTests(unittest.TestCase):

    def test_failure_restart_retry_success(self):
        with TemporaryDirectory() as directory:
            queue = Path(directory) / "verification.db"
            now = datetime(2026, 11, 12, tzinfo=timezone.utc)
            published = (now - timedelta(days=3)).isoformat()

            job = {
                "source": "Greenhouse",
                "source_id": "12345",
                "company": "Example",
                "title": "IT Support Specialist",
                "location": "San Diego, CA",
                "url": "https://example.org/jobs/12345",
                "original_publication_verified": False,
            }

            sources = [{
                "ats": "Greenhouse",
                "company": "Example",
                "token_or_site": "example",
            }]

            initialize(queue)
            enqueue(queue, job, now=now)
            enqueue(queue, job, now=now)

            due = due_candidates(queue, now=now)
            self.assertEqual(len(due), 1)

            failure = Mock()
            failure.ok = False
            failure.status_code = 503

            with patch(
                "stage8_greenhouse_verification.requests.get",
                return_value=failure,
            ) as request:
                result = verify_candidates(
                    due, sources, max_requests=1
                )
                self.assertEqual(request.call_count, 1)

            self.assertFalse(
                result[0]["original_publication_verified"]
            )

            self.assertTrue(mark_failed(queue, job, now=now))
            self.assertEqual(due_candidates(queue, now=now), [])

            # Simulate a later process restarting the queue.
            initialize(queue)
            retry_time = now + timedelta(days=2)
            due = due_candidates(queue, now=retry_time)
            self.assertEqual(len(due), 1)

            success = Mock()
            success.ok = True
            success.json.return_value = {
                "first_published": published
            }

            with patch(
                "stage8_greenhouse_verification.requests.get",
                return_value=success,
            ) as request:
                result = verify_candidates(
                    due, sources, max_requests=1
                )
                self.assertEqual(request.call_count, 1)

            self.assertTrue(
                result[0]["original_publication_verified"]
            )
            self.assertEqual(
                result[0]["original_published_at"], published
            )

            self.assertTrue(
                mark_verified(queue, job, published, now=retry_time)
            )

            initialize(queue)
            self.assertEqual(
                due_candidates(
                    queue, now=retry_time + timedelta(days=3)
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
