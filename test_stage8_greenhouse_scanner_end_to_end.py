"""Offline scanner integration tests; no external services."""

import unittest
from unittest.mock import patch

from scheduled_scan import run_scan
from test_scheduled_scan import (
    ScheduledScanTests,
    listing,
    fake_score,
)


class GreenhouseScannerIntegrationTests(unittest.TestCase):

    def setUp(self):
        ScheduledScanTests.setUp(self)
        self.new = listing(30)
        self.applied = listing(20)

    def collector(self, sources, max_age_days, verify_first_published):
        self.assertFalse(verify_first_published)
        return [self.first, self.new, self.applied], []

    def test_default_never_invokes_bridge(self):
        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications"
        ) as bridge:
            result = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
            )

        bridge.assert_not_called()
        self.assertEqual(result["first_seen_by_jobpilot_count"], 2)

    def test_optin_invokes_bridge_for_screened_new_jobs(self):
        response = {
            "verification_attempts": 1,
            "verified": 1,
            "notification_matches": 1,
        }

        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            return_value=response,
        ) as bridge:
            result = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        bridge.assert_called_once()
        arguments = bridge.call_args.kwargs

        self.assertEqual(arguments["first_seen_jobs"], [self.new])
        self.assertEqual(arguments["max_requests"], 10)
        self.assertEqual(arguments["min_score"], 45)
        self.assertEqual(result["greenhouse_verification"], response)

    def test_optin_processes_queue_with_no_new_jobs(self):
        run_scan(
            self.root,
            collector=self.collector,
            scorer=fake_score,
        )

        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            return_value={
                "verification_attempts": 0,
                "verified": 0,
                "notification_matches": 0,
            },
        ) as bridge:
            result = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        bridge.assert_called_once()
        self.assertEqual(
            bridge.call_args.kwargs["first_seen_jobs"],
            [self.new],
        )
        self.assertEqual(result["first_seen_by_jobpilot_count"], 0)

    def test_bridge_failure_preserves_normal_scan(self):
        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            side_effect=RuntimeError("Simulated verification failure"),
        ):
            result = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        self.assertIn(
            "Simulated verification failure",
            result["greenhouse_verification_error"],
        )
        self.assertEqual(result["candidate_matches_for_review"], 1)
        self.assertTrue(
            (
                self.root
                / "monitoring_reports"
                / "last_successful_scan.json"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
