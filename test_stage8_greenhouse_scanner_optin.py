"""Offline tests for explicit Greenhouse scanner opt-in."""

import unittest
from unittest.mock import patch

import scheduled_scan


class GreenhouseScannerOptInTests(unittest.TestCase):

    @staticmethod
    def summary():
        return {
            "scan_id": 1,
            "collected_count": 1,
            "first_seen_by_jobpilot_count": 1,
            "candidate_matches_for_review": 0,
            "source_error_count": 0,
        }

    def test_default_run_keeps_greenhouse_verification_disabled(self):
        with (
            patch(
                "scheduled_scan.run_scan",
                return_value=self.summary(),
            ) as run,
            patch("scheduled_scan._append_scan_log"),
        ):
            result = scheduled_scan.main(["--run"])

        self.assertEqual(result, 0)
        self.assertFalse(
            run.call_args.kwargs.get("verify_greenhouse", False)
        )

    def test_explicit_flag_enables_greenhouse_verification(self):
        with (
            patch(
                "scheduled_scan.run_scan",
                return_value=self.summary(),
            ) as run,
            patch("scheduled_scan._append_scan_log"),
        ):
            result = scheduled_scan.main([
                "--run",
                "--verify-greenhouse",
            ])

        self.assertEqual(result, 0)
        self.assertIs(
            run.call_args.kwargs["verify_greenhouse"],
            True,
        )


if __name__ == "__main__":
    unittest.main()
