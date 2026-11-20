"""Cross-module regression for historical application exclusions."""

import unittest
from datetime import datetime, timedelta, timezone

from scheduled_scan import select_relevant_first_seen
from stage8_greenhouse_notification import build_notification_matches
from discovery_history import job_fingerprint


def listing(job_id, status=None):
    row = {
        "source": "Greenhouse",
        "source_id": str(job_id),
        "company": "Example",
        "title": "IT Support Specialist",
        "location": "San Diego, CA",
        "url": f"https://job-boards.greenhouse.io/example/jobs/{job_id}",
        "original_published_at": (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat(),
        "original_publication_verified": True,
    }
    if status is not None:
        row["status"] = status
    return row


def scorer(job, profile):
    return {
        "score": 80,
        "matched_family": "IT Support",
        "geo_status": "local",
        "geo_reason": "San Diego",
        "recommendation": "APPLY",
    }


class ReconciliationIntegrationTests(unittest.TestCase):

    def test_scanner_excludes_conflicting_exact_records(self):
        target = listing(12345)
        saved = [
            listing(12345, "Saved"),
            listing(12345, "Rejected"),
        ]

        selected = select_relevant_first_seen(
            [(target, {})],
            {job_fingerprint(target)},
            saved,
            scorer=scorer,
        )

        self.assertEqual(selected, [])

    def test_notification_excludes_conflicting_exact_records(self):
        target = listing(12345)
        saved = [
            listing(12345, "Saved"),
            listing(12345, "Applied"),
        ]

        matches = build_notification_matches(
            [target],
            profile={},
            saved_rows=saved,
            scorer=scorer,
        )

        self.assertEqual(matches, [])

    def test_distinct_requisition_remains_eligible(self):
        target = listing(12345)
        saved = [listing(67890, "Rejected")]

        matches = build_notification_matches(
            [target],
            profile={},
            saved_rows=saved,
            scorer=scorer,
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["url"], target["url"])

    def test_uncertain_history_is_warning_not_exclusion(self):
        target = listing(12345)
        history = [{
            "company": "Example",
            "title": "IT Support Specialist",
            "status": "Rejected",
        }]

        matches = build_notification_matches(
            [target],
            profile={},
            saved_rows=[],
            scorer=scorer,
            historical_rows=history,
        )

        self.assertEqual(len(matches), 1)
        self.assertIn(
            "Possible previous application",
            matches[0]["historical_application_warning"],
        )


if __name__ == "__main__":
    unittest.main()
