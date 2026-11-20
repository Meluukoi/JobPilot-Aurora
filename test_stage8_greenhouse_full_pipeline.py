"""Real scanner-to-archive integration with mocked HTTP."""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from scheduled_scan import run_scan
from test_scheduled_scan import (
    ScheduledScanTests, listing, fake_score,
)


class GreenhouseFullPipelineTests(unittest.TestCase):

    def setUp(self):
        ScheduledScanTests.setUp(self)
        self.new = listing(30)
        self.published = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()

    def collector(self, sources, max_age_days, verify_first_published):
        self.assertFalse(verify_first_published)
        return [self.first, self.new], []

    def archived_verified_rows(self):
        folder = self.root / "monitoring_reports"
        rows = []

        for path in sorted(
            folder.glob("first_seen_matches_scan_*.csv")
        ):
            with path.open(
                newline="", encoding="utf-8-sig"
            ) as file:
                rows.extend(
                    row for row in csv.DictReader(file)
                    if row.get("original_publication_verified") == "True"
                )

        return rows

    def test_complete_verification_and_duplicate_prevention(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "first_published": self.published,
        }

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ) as http:
            first = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

            second = run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

        self.assertEqual(first["first_seen_by_jobpilot_count"], 1)
        self.assertEqual(second["first_seen_by_jobpilot_count"], 0)
        self.assertEqual(http.call_count, 1)

        rows = self.archived_verified_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], self.new["url"])

        self.assertEqual(
            first["greenhouse_verification"]["notification_matches"],
            1,
        )
        self.assertEqual(
            second["greenhouse_verification"]["notification_matches"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
