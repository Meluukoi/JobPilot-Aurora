"""Hourly publication freshness regression tests."""

import unittest
from datetime import datetime, timedelta, timezone

from alert_policy import email_eligible


def posting(hours_old):
    return {
        "eligibility": "local",
        "original_publication_verified": True,
        "original_published_at": (
            datetime.now(timezone.utc)
            - timedelta(hours=hours_old)
        ).isoformat(),
    }


class HourlyAlertPolicyTests(unittest.TestCase):

    def test_24_hour_window_accepts_23_hour_posting(self):
        self.assertTrue(
            email_eligible(posting(23), freshness_hours=24)
        )

    def test_24_hour_window_rejects_25_hour_posting(self):
        self.assertFalse(
            email_eligible(posting(25), freshness_hours=24)
        )

    def test_default_still_allows_three_day_posting(self):
        self.assertTrue(email_eligible(posting(72)))

    def test_default_rejects_fifteen_day_posting(self):
        self.assertFalse(email_eligible(posting(360)))

    def test_invalid_hour_limit_rejected(self):
        for value in (0, 337, True, 24.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    email_eligible(
                        posting(2), freshness_hours=value
                    )

    def test_unverified_posting_still_blocked(self):
        row = posting(2)
        row["original_publication_verified"] = False
        self.assertFalse(
            email_eligible(row, freshness_hours=24)
        )


if __name__ == "__main__":
    unittest.main()
