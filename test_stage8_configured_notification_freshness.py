"""Offline tests for configured notification freshness."""

import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from stage8_greenhouse_archive import archive_verified_matches
from email_notifications import pending_jobs
from scheduled_scan import COLUMNS


def posting(hours_old, job_id):
    return {
        "company": "Example",
        "title": "IT Support Specialist",
        "location": "San Diego, CA",
        "score": 80,
        "role_family": "IT Support",
        "eligibility": "local",
        "recommendation": "APPLY",
        "url": f"https://example.org/jobs/{job_id}",
        "original_published_at": (
            datetime.now(timezone.utc)
            - timedelta(hours=hours_old)
        ).isoformat(),
        "original_publication_verified": True,
    }


class ConfiguredNotificationFreshnessTests(unittest.TestCase):

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        (self.root / "jobpilot_policy.json").write_text(
            json.dumps({
                "publication_freshness_hours": 24
            }),
            encoding="utf-8",
        )

    def test_archive_uses_configured_twenty_four_hours(self):
        archive_verified_matches(
            self.root,
            12,
            [posting(25, 12345)],
        )

        archives = list(
            (self.root / "monitoring_reports").glob(
                "first_seen_matches_scan_*.csv"
            )
        ) if (self.root / "monitoring_reports").exists() else []

        self.assertEqual(
            archives,
            [],
            "25-hour-old posting entered a 24-hour archive",
        )

    def test_email_queue_uses_configured_twenty_four_hours(self):
        folder = self.root / "monitoring_reports"
        folder.mkdir()

        archive = folder / "first_seen_matches_scan_000012.csv"

        with archive.open(
            "w", newline="", encoding="utf-8-sig"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=COLUMNS)
            writer.writeheader()

            for row in (
                posting(25, 12345),
                posting(23, 67890),
            ):
                writer.writerow({
                    field: row.get(field, "")
                    for field in COLUMNS
                })

        pending = pending_jobs(
            self.root,
            config={"start_after_scan_id": 0},
            state={"sent_ids": []},
        )

        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0][1]["url"],
            "https://example.org/jobs/67890",
        )


if __name__ == "__main__":
    unittest.main()
