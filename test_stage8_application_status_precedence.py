"""Exact saved-job status precedence regression tests."""

import unittest

from application_tracking import (
    SavedJobIndex,
    already_applied,
    HistoricalWarningIndex,
)


def listing(job_id, status=None):
    record = {
        "source": "Greenhouse",
        "source_id": str(job_id),
        "company": "Example",
        "title": "IT Support Specialist",
        "url": (
            "https://job-boards.greenhouse.io/"
            f"example/jobs/{job_id}"
        ),
    }
    if status is not None:
        record["status"] = status
    return record


class ApplicationStatusPrecedenceTests(unittest.TestCase):

    def test_saved_before_rejected(self):
        target = listing(12345)
        records = [
            listing(12345, "Saved"),
            listing(12345, "Rejected"),
        ]
        match, _ = SavedJobIndex(records).lookup(target)
        self.assertEqual(match["status"], "Rejected")

    def test_rejected_before_saved(self):
        target = listing(12345)
        records = [
            listing(12345, "Rejected"),
            listing(12345, "Saved"),
        ]
        match, _ = SavedJobIndex(records).lookup(target)
        self.assertEqual(match["status"], "Rejected")

    def test_all_handled_statuses_override_saved(self):
        for status in (
            "Applied", "Interview", "Rejected",
            "Offer", "Archived",
        ):
            with self.subTest(status=status):
                records = [
                    listing(12345, "Saved"),
                    listing(12345, status),
                ]
                match, _ = SavedJobIndex(records).lookup(
                    listing(12345)
                )
                self.assertTrue(already_applied(match["status"]))

    def test_different_requisition_not_blocked(self):
        records = [listing(67890, "Rejected")]
        match, _ = SavedJobIndex(records).lookup(
            listing(12345)
        )
        self.assertIsNone(match)

    def test_saved_only_remains_saved(self):
        records = [listing(12345, "Saved")]
        match, _ = SavedJobIndex(records).lookup(
            listing(12345)
        )
        self.assertEqual(match["status"], "Saved")

    def test_uncertain_history_remains_advisory(self):
        history = HistoricalWarningIndex([{
            "company": "Example",
            "title": "IT Support Specialist",
            "status": "Rejected",
        }])
        warning = history.warning(listing(12345))
        self.assertIn("Possible previous application", warning)
        self.assertIn("do not auto-exclude", warning)


if __name__ == "__main__":
    unittest.main()
