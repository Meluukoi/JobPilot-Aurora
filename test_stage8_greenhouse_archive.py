"""Offline tests for crash-safe Greenhouse notification archiving."""

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stage8_greenhouse_archive import archive_verified_matches


class GreenhouseArchiveTests(unittest.TestCase):

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def job(self, job_id="123"):
        return {
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "score": 80,
            "role_family": "IT Support",
            "eligibility": "local",
            "url": f"https://example.org/jobs/{job_id}",
            "original_published_at": "2026-11-12T00:00:00+00:00",
            "original_publication_verified": True,
        }

    def archives(self):
        folder = self.root / "monitoring_reports"
        return sorted(folder.glob("first_seen_matches_scan_*.csv"))

    def archived_rows(self):
        result = []
        for path in self.archives():
            with path.open(newline="", encoding="utf-8-sig") as file:
                result.extend(csv.DictReader(file))
        return result

    def test_verified_candidate_archived(self):
        archive_verified_matches(self.root, 12, [self.job()])
        rows = self.archived_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_publication_verified"], "True")

    def test_repeated_handoff_does_not_duplicate(self):
        archive_verified_matches(self.root, 12, [self.job()])
        archive_verified_matches(self.root, 12, [self.job()])
        self.assertEqual(len(self.archived_rows()), 1)

    def test_later_scan_does_not_duplicate_old_archive(self):
        archive_verified_matches(self.root, 12, [self.job()])
        archive_verified_matches(self.root, 13, [self.job()])
        self.assertEqual(len(self.archived_rows()), 1)

    def test_distinct_candidates_preserved(self):
        archive_verified_matches(
            self.root, 12, [self.job("123"), self.job("456")]
        )
        self.assertEqual(len(self.archived_rows()), 2)

    def test_empty_handoff_creates_no_archive(self):
        archive_verified_matches(self.root, 12, [])
        self.assertEqual(self.archives(), [])

    def test_existing_archive_not_lost(self):
        archive_verified_matches(self.root, 12, [self.job("123")])
        archive_verified_matches(self.root, 12, [self.job("456")])
        self.assertEqual(
            {row["url"] for row in self.archived_rows()},
            {
                "https://example.org/jobs/123",
                "https://example.org/jobs/456",
            },
        )


if __name__ == "__main__":
    unittest.main()
