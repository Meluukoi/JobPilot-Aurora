"""Pending verification recovery after an empty collection."""

import unittest
from unittest.mock import patch

from scheduled_scan import run_scan
from test_scheduled_scan import ScheduledScanTests, fake_score


class EmptyCollectionRecoveryTests(unittest.TestCase):

    def setUp(self):
        fixture = ScheduledScanTests(
            "test_no_writes_from_settings_and_readiness"
        )
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.root = fixture.root

    @staticmethod
    def empty_collector(
        sources, max_age_days, verify_first_published
    ):
        return [], ["Simulated source outage"]

    def test_pending_verification_runs_without_new_listings(self):
        with patch(
            "stage8_greenhouse_scan_bridge.process_greenhouse_notifications",
            return_value={
                "verification_attempts": 1,
                "verified": 1,
                "notification_matches": 0,
            },
        ) as bridge:
            with self.assertRaisesRegex(
                RuntimeError,
                "No listings collected",
            ):
                run_scan(
                    self.root,
                    collector=self.empty_collector,
                    scorer=fake_score,
                    verify_greenhouse=True,
                )

        bridge.assert_called_once()
        self.assertEqual(
            bridge.call_args.kwargs["first_seen_jobs"], []
        )

        # An empty collection must not create a successful report.
        self.assertFalse(
            (
                self.root
                / "monitoring_reports"
                / "last_successful_scan.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
