"""Offline tests for Greenhouse scanner-to-verification integration."""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from stage8_greenhouse_scan_bridge import process_greenhouse_notifications
from stage8_greenhouse_queue import due_candidates


class GreenhouseScanBridgeTests(unittest.TestCase):

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        self.now = datetime.now(timezone.utc)
        self.published = (
            self.now - timedelta(days=3)
        ).isoformat()

        self.sources = [{
            "ats": "Greenhouse",
            "company": "Example",
            "token_or_site": "example",
        }]

        self.job = {
            "source": "Greenhouse",
            "source_id": "12345",
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": "https://example.org/jobs/12345",
            "description": "Technical troubleshooting",
            "posted_at": "Unknown",
        }

    @staticmethod
    def scorer(job, profile):
        return {
            "score": 80,
            "matched_family": "IT Support",
            "geo_status": "local",
            "geo_reason": "Test geography",
            "recommendation": "APPLY",
        }

    def process(self, jobs, scan_id=12, saved_rows=(), now=None):
        return process_greenhouse_notifications(
            root=self.root,
            sources=self.sources,
            first_seen_jobs=jobs,
            profile={},
            saved_rows=list(saved_rows),
            scorer=self.scorer,
            scan_id=scan_id,
            max_requests=2,
            now=now or self.now,
        )

    def archived_rows(self):
        folder = self.root / "monitoring_reports"
        rows = []

        for archive in sorted(
            folder.glob("first_seen_matches_scan_*.csv")
        ):
            with archive.open(
                newline="", encoding="utf-8-sig"
            ) as file:
                rows.extend(csv.DictReader(file))

        return rows

    def test_verified_candidate_reaches_archive(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "first_published": self.published
        }

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ) as request:
            self.process([self.job])

        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(self.archived_rows()), 1)

    def test_failed_verification_remains_pending(self):
        response = Mock()
        response.ok = False
        response.status_code = 503

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ):
            self.process([self.job])

        self.assertEqual(self.archived_rows(), [])

        queue = (
            self.root /
            "stage8_greenhouse_verification_queue.db"
        )

        later = self.now + timedelta(days=2)

        self.assertEqual(
            len(due_candidates(queue, now=later)),
            1,
        )

    def test_retry_does_not_alert_applied_job(self):
        failure = Mock()
        failure.ok = False

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=failure,
        ):
            self.process([self.job])

        success = Mock()
        success.ok = True
        success.json.return_value = {
            "first_published": self.published
        }

        applied = dict(self.job, status="Applied")

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=success,
        ):
            self.process(
                [],
                scan_id=13,
                saved_rows=[applied],
                now=self.now + timedelta(days=2),
            )

        self.assertEqual(self.archived_rows(), [])

    def test_repeated_scan_does_not_duplicate_archive(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "first_published": self.published
        }

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ) as request:
            self.process([self.job], scan_id=12)
            self.process([], scan_id=13)

        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(self.archived_rows()), 1)


if __name__ == "__main__":
    unittest.main()
