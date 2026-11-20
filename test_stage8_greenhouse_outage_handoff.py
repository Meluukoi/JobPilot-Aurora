"""Offline recovery from collection outage to durable notification."""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from scheduled_scan import run_scan
from test_scheduled_scan import ScheduledScanTests, listing, fake_score
from stage8_greenhouse_queue import initialize, enqueue
from stage8_greenhouse_handoff import ready_for_handoff


class GreenhouseOutageHandoffTests(unittest.TestCase):

    def setUp(self):
        fixture = ScheduledScanTests(
            "test_no_writes_from_settings_and_readiness"
        )
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.root = fixture.root
        self.job = listing(30)
        self.queue = (
            self.root / "stage8_greenhouse_verification_queue.db"
        )

    @staticmethod
    def empty_collector(sources, max_age_days, verify_first_published):
        return [], ["Simulated collection outage"]

    @staticmethod
    def recovered_collector(sources, max_age_days, verify_first_published):
        return [listing(10)], []

    def test_outage_verification_then_successful_handoff(self):
        now = datetime.now(timezone.utc)

        initialize(self.queue)
        enqueue(
            self.queue,
            self.job,
            now=now - timedelta(days=1),
        )

        response = Mock()
        response.ok = True
        response.json.return_value = {
            "first_published": (
                now - timedelta(days=3)
            ).isoformat()
        }

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ) as request:

            # Outage: verification may complete, but no successful
            # discovery scan ID is available for notification archiving.
            with self.assertRaisesRegex(
                RuntimeError, "No listings collected"
            ):
                run_scan(
                    self.root,
                    collector=self.empty_collector,
                    scorer=fake_score,
                    verify_greenhouse=True,
                )

            self.assertEqual(request.call_count, 1)

            self.assertFalse(
                (
                    self.root / "monitoring_reports"
                    / "last_successful_scan.json"
                ).exists()
            )

            self.assertEqual(
                len(ready_for_handoff(self.queue)), 1
            )

            folder = self.root / "monitoring_reports"

            self.assertEqual(
                list(folder.glob("first_seen_matches_scan_*.csv"))
                if folder.exists() else [],
                [],
            )

            # A subsequent successful scan may finalize the
            # already-verified job without requesting verification again.
            result = run_scan(
                self.root,
                collector=self.recovered_collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

            self.assertEqual(request.call_count, 1)

        verification = result["greenhouse_verification"]

        self.assertEqual(verification["verification_attempts"], 0)
        self.assertEqual(verification["notification_matches"], 1)
        self.assertEqual(verification["handed_off"], 1)

        archives = sorted(
            folder.glob("first_seen_matches_scan_*.csv")
        )

        rows = []

        for archive in archives:
            with archive.open(
                newline="", encoding="utf-8-sig"
            ) as file:
                rows.extend(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], self.job["url"])
        self.assertEqual(
            rows[0]["original_publication_verified"], "True"
        )

        self.assertEqual(ready_for_handoff(self.queue), [])


if __name__ == "__main__":
    unittest.main()
