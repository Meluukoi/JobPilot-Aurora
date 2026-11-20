"""Offline recovery-to-email integration test."""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import email_notifications as email
from scheduled_scan import run_scan
from stage8_greenhouse_queue import initialize, enqueue
from stage8_greenhouse_handoff import ready_for_handoff
from test_scheduled_scan import (
    ScheduledScanTests, listing, fake_score,
)


class RecoveryToEmailTests(unittest.TestCase):

    def setUp(self):
        fixture = ScheduledScanTests(
            "test_no_writes_from_settings_and_readiness"
        )
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)

        self.root = fixture.root
        self.job = listing(30)
        self.queue = (
            self.root / "stage8_greenhouse_verification_queue.db"
        )
        self.folder = self.root / "monitoring_reports"

        self.email_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "starttls",
            "smtp_user": "sender@example.com",
            "sender": "sender@example.com",
            "recipient": "recipient@example.com",
            "start_after_scan_id": 0,
        }

    @staticmethod
    def outage_collector(
        sources, max_age_days, verify_first_published
    ):
        return [], ["Simulated collection outage"]

    @staticmethod
    def recovery_collector(
        sources, max_age_days, verify_first_published
    ):
        return [listing(10)], []

    def test_outage_restart_smtp_failure_and_exactly_once_state(self):
        now = datetime.now(timezone.utc)

        initialize(self.queue)
        enqueue(
            self.queue,
            self.job,
            now=now - timedelta(days=1),
        )

        response = Mock()
        response.ok = True
        response.json.return_value = {
            "first_published": (
                now - timedelta(days=3)
            ).isoformat()
        }

        # All HTTP activity is mocked. Collection uses injected
        # functions and does not access the network.
        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=response,
        ) as http:

            with self.assertRaisesRegex(
                RuntimeError, "No listings collected"
            ):
                run_scan(
                    self.root,
                    collector=self.outage_collector,
                    scorer=fake_score,
                    verify_greenhouse=True,
                )

            self.assertEqual(http.call_count, 1)
            self.assertEqual(len(ready_for_handoff(self.queue)), 1)

            # No completed scan means no eligible email archive.
            self.assertEqual(
                email.pending_jobs(
                    self.root, config=self.email_config
                ),
                [],
            )

            # Restart: finalize the recovered job without
            # repeating the original publication HTTP request.
            result = run_scan(
                self.root,
                collector=self.recovery_collector,
                scorer=fake_score,
                verify_greenhouse=True,
            )

            self.assertEqual(http.call_count, 1)

        self.assertEqual(
            result["greenhouse_verification"]["notification_matches"],
            1,
        )
        self.assertEqual(
            ready_for_handoff(self.queue),
            [],
        )

        archives = sorted(
            self.folder.glob("first_seen_matches_scan_*.csv")
        )
        self.assertEqual(len(archives), 1)

        with archives[0].open(
            newline="", encoding="utf-8-sig"
        ) as file:
            rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], self.job["url"])
        self.assertEqual(
            rows[0]["original_publication_verified"],
            "True",
        )

        pending = email.pending_jobs(
            self.root, config=self.email_config
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][1]["url"], self.job["url"])

        state_path = self.folder / email.STATE_NAME

        # Simulate failed SMTP. No success state may be recorded.
        failed_sender = Mock(
            side_effect=RuntimeError("Simulated SMTP outage")
        )

        with self.assertRaisesRegex(
            RuntimeError, "Simulated SMTP outage"
        ):
            email.deliver(
                self.root,
                sender=failed_sender,
                config=self.email_config,
            )

        self.assertEqual(failed_sender.call_count, 1)
        self.assertFalse(state_path.exists())

        self.assertEqual(
            len(email.pending_jobs(
                self.root, config=self.email_config
            )),
            1,
        )

        # Retry succeeds; delivery state advances once.
        successful_sender = Mock()

        delivered = email.deliver(
            self.root,
            sender=successful_sender,
            config=self.email_config,
        )

        self.assertEqual(delivered, 1)
        self.assertEqual(successful_sender.call_count, 1)
        self.assertTrue(state_path.exists())

        state = email.load_state(self.root)
        self.assertEqual(len(state["sent_ids"]), 1)

        message = successful_sender.call_args.args[1]
        self.assertIn(self.job["url"], message.get_content())

        # Restart/repeat after delivery must not email again.
        self.assertEqual(
            email.deliver(
                self.root,
                sender=successful_sender,
                config=self.email_config,
            ),
            0,
        )

        self.assertEqual(successful_sender.call_count, 1)
        self.assertEqual(
            len(email.load_state(self.root)["sent_ids"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
