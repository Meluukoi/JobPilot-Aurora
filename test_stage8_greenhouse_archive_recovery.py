"""Crash recovery between Greenhouse archive writing and handoff completion."""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from stage8_greenhouse_queue import initialize, enqueue, mark_verified
from stage8_greenhouse_handoff import ready_for_handoff, mark_handed_off
from stage8_greenhouse_archive import archive_verified_matches
from stage8_greenhouse_notification import build_notification_matches


class GreenhouseArchiveRecoveryTests(unittest.TestCase):

    @staticmethod
    def scorer(job, profile):
        return {
            "score": 80,
            "matched_family": "IT Support",
            "geo_status": "local",
            "geo_reason": "Test location",
            "recommendation": "APPLY",
        }

    def test_restart_after_archive_before_handoff(self):
        now = datetime.now(timezone.utc)
        published = (now - timedelta(days=3)).isoformat()

        job = {
            "source": "Greenhouse",
            "source_id": "12345",
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": "https://example.org/jobs/12345",
        }

        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "verification.db"

            initialize(queue)
            enqueue(queue, job, now=now)
            self.assertTrue(mark_verified(
                queue, job, published, now=now
            ))

            matches = build_notification_matches(
                ready_for_handoff(queue),
                profile={},
                saved_rows=[],
                scorer=self.scorer,
            )

            self.assertEqual(len(matches), 1)
            archive_verified_matches(root, 12, matches)

            # Simulate interruption before mark_handed_off().
            initialize(queue)
            self.assertEqual(len(ready_for_handoff(queue)), 1)

            retry_matches = build_notification_matches(
                ready_for_handoff(queue),
                profile={},
                saved_rows=[],
                scorer=self.scorer,
            )

            archive_verified_matches(root, 13, retry_matches)

            archives = sorted(
                (root / "monitoring_reports").glob(
                    "first_seen_matches_scan_*.csv"
                )
            )

            rows = []
            for archive in archives:
                with archive.open(
                    newline="", encoding="utf-8-sig"
                ) as file:
                    rows.extend(csv.DictReader(file))

            self.assertEqual(len(rows), 1)
            self.assertEqual(
                rows[0]["original_publication_verified"],
                "True",
            )

            self.assertTrue(mark_handed_off(queue, job))
            self.assertEqual(ready_for_handoff(queue), [])
            self.assertFalse(mark_handed_off(queue, job))


if __name__ == "__main__":
    unittest.main()
