"""Offline email batch configuration tests."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import email_notifications


class ConfiguredEmailBatchTests(unittest.TestCase):

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_delivery(self, batch_size=None):
        if batch_size is not None:
            (self.root / "jobpilot_policy.json").write_text(
                json.dumps({"email_batch_size": batch_size}),
                encoding="utf-8",
            )

        jobs = [
            (f"id-{i}", {"title": f"Job {i}"})
            for i in range(8)
        ]

        state = {"sent_ids": [], "last_sent_at_utc": None}
        sent_batches = []

        def sender(config, message):
            sent_batches.append(message)

        with (
            patch.object(
                email_notifications,
                "pending_jobs",
                return_value=jobs,
            ),
            patch.object(
                email_notifications,
                "load_state",
                return_value=state,
            ),
            patch.object(
                email_notifications,
                "make_email",
                side_effect=lambda config, batch: list(batch),
            ),
            patch.object(email_notifications, "_atomic_json"),
        ):
            count = email_notifications.deliver(
                root=self.root,
                sender=sender,
                config={"start_after_scan_id": 0},
            )

        return count, sent_batches, state

    def test_configured_limit_of_five(self):
        count, batches, state = self.run_delivery(5)
        self.assertEqual(count, 5)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 5)
        self.assertEqual(len(state["sent_ids"]), 5)

    def test_missing_policy_preserves_limit_of_twenty(self):
        count, batches, state = self.run_delivery()
        self.assertEqual(count, 8)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 8)
        self.assertEqual(len(state["sent_ids"]), 8)

    def test_invalid_limit_rejected_before_sending(self):
        with self.assertRaises(ValueError):
            self.run_delivery(21)


if __name__ == "__main__":
    unittest.main()
