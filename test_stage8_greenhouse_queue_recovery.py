"""Regression for interruption before Greenhouse queue persistence."""

import unittest
from unittest.mock import patch

from scheduled_scan import run_scan
from test_scheduled_scan import ScheduledScanTests, listing, fake_score


class GreenhouseQueueRecoveryTests(unittest.TestCase):

    def setUp(self):
        fixture = ScheduledScanTests(
            "test_no_writes_from_settings_and_readiness"
        )
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.root = fixture.root
        self.new = listing(30)

    def collector(self, sources, max_age_days, verify_first_published):
        return [listing(10), self.new], []

    def test_interrupted_queue_recovers_first_seen_candidate(self):
        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            side_effect=RuntimeError("Simulated interruption"),
        ):
            first = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        self.assertEqual(first["first_seen_by_jobpilot_count"], 1)
        self.assertIn(
            "Simulated interruption",
            first["greenhouse_verification_error"],
        )

        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            return_value={
                "verification_attempts": 0,
                "verified": 0,
                "notification_matches": 0,
            },
        ) as bridge:
            second = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        self.assertEqual(second["first_seen_by_jobpilot_count"], 0)
        bridge.assert_called_once()

        recovered = bridge.call_args.kwargs["first_seen_jobs"]

        self.assertEqual(
            [job["source_id"] for job in recovered],
            [self.new["source_id"]],
            "Previously discovered candidate was permanently lost",
        )


if __name__ == "__main__":
    unittest.main()
