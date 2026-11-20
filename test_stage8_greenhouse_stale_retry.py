"""Offline regression for stale Greenhouse verification retries."""

import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch

from stage8_greenhouse_scan_bridge import process_greenhouse_notifications
from stage8_greenhouse_handoff import ready_for_handoff


class GreenhouseStaleRetryTests(unittest.TestCase):

    def test_retry_verifies_but_does_not_alert_stale_job(self):
        current = datetime.now(timezone.utc)
        first_attempt = current - timedelta(days=2)
        original_publication = first_attempt - timedelta(days=13)

        job = {
            "source": "Greenhouse",
            "source_id": "12345",
            "company": "Example",
            "title": "IT Support Specialist",
            "location": "San Diego, CA",
            "url": "https://example.org/jobs/12345",
            "description": "Technical support and troubleshooting",
        }

        sources = [{
            "ats": "Greenhouse",
            "company": "Example",
            "token_or_site": "example",
        }]

        def scorer(job, profile):
            return {
                "score": 80,
                "matched_family": "IT Support",
                "geo_status": "local",
                "geo_reason": "San Diego",
                "recommendation": "APPLY",
            }

        with TemporaryDirectory() as directory:
            root = Path(directory)

            failure = Mock(ok=False, status_code=503)

            with patch(
                "stage8_greenhouse_verification.requests.get",
                return_value=failure,
            ):
                first = process_greenhouse_notifications(
                    root=root,
                    sources=sources,
                    first_seen_jobs=[job],
                    profile={},
                    saved_rows=[],
                    scorer=scorer,
                    scan_id=1,
                    max_requests=2,
                    now=first_attempt,
                )

            self.assertEqual(first["verification_failed"], 1)
            self.assertEqual(first["notification_matches"], 0)

            success = Mock(ok=True)
            success.json.return_value = {
                "first_published": original_publication.isoformat()
            }

            with patch(
                "stage8_greenhouse_verification.requests.get",
                return_value=success,
            ) as request:
                second = process_greenhouse_notifications(
                    root=root,
                    sources=sources,
                    first_seen_jobs=[],
                    profile={},
                    saved_rows=[],
                    scorer=scorer,
                    scan_id=2,
                    max_requests=2,
                    now=current,
                )

            self.assertEqual(request.call_count, 1)
            self.assertEqual(second["verified"], 1)
            self.assertEqual(second["notification_matches"], 0)
            self.assertEqual(second["handed_off"], 1)
            self.assertIsNone(second["archive_path"])

            queue = root / "stage8_greenhouse_verification_queue.db"
            self.assertEqual(ready_for_handoff(queue), [])

            reports = root / "monitoring_reports"

            if reports.exists():
                self.assertEqual(
                    list(reports.glob("first_seen_matches_scan_*.csv")),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
