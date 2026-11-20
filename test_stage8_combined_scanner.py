import unittest
from pathlib import Path
from unittest.mock import patch

import scheduled_scan


LEGACY = {
    "source": "Greenhouse",
    "source_id": "12345",
    "company": "Test Company",
    "title": "IT Support Specialist",
    "location": "San Diego, CA",
    "url": "https://boards.greenhouse.io/test/jobs/12345",
}

WORKDAY = {
    "source": "Workday",
    "source_id": "R30431",
    "company": "CrowdStrike",
    "title": "Technical Support Associate",
    "location": "USA - Remote",
    "url": (
        "https://crowdstrike.wd5.myworkdayjobs.com/"
        "crowdstrikecareers/job/USA---Remote/"
        "Technical-Support-Associate_R30431-1"
    ),
    "description": "Responsibilities include technical support. " * 5,
    "posted_at": "Unknown",
}

SOURCES = [
    {"ats": "Greenhouse", "company": "Test Company",
     "token_or_site": "test"},
    {"ats": "Workday", "company": "CrowdStrike",
     "token_or_site": "crowdstrike"},
]


class CombinedScannerTests(unittest.TestCase):

    def run_case(
        self, opt_in, fail_stage=None, diagnostic_writer=None,
        prepared_jobs=None,
    ):
        events = []
        captured = {}

        def collect_legacy(sources, **kwargs):
            self.assertEqual([s["ats"] for s in sources], ["Greenhouse"])
            return [LEGACY], []

        def collect_workday(sources, queue_path):
            events.append("collect_workday")
            return [{"employer": "crowdstrike", "indexed": 1}]

        def prepare(queue_path, history_path):
            events.append("prepare")
            return [WORKDAY] if prepared_jobs is None else prepared_jobs

        def record(jobs, **kwargs):
            events.append("record")
            captured["history"] = list(jobs)
            if fail_stage == "history":
                raise RuntimeError("Simulated history failure")
            return {
                "scan_id": 10,
                "known_count": 0,
                "new_count": len(jobs),
            }

        def finalize(queue_path, history_path):
            events.append("finalize")
            if fail_stage == "finalize":
                raise RuntimeError("Simulated finalization failure")
            return {
                "completed": 1,
                "waiting_for_history": 0,
                "new_review_records_staged": 1,
            }

        def save(root, summary, listings):
            captured["summary"] = dict(summary)
            captured["report_listings"] = list(listings)

        def review(jobs, *args, **kwargs):
            captured["review_jobs"] = list(jobs)
            return [], [], {
                "review": 0,
                "stretch_review": 0,
                "collected": len(jobs),
            }

        patches = [
            patch.object(scheduled_scan, "load_settings",
                         return_value=(SOURCES, {})),
            patch.object(scheduled_scan, "load_saved_jobs_read_only",
                         return_value=[]),
            patch.object(scheduled_scan, "load_historical_warnings_read_only",
                         return_value=[]),
            patch.object(scheduled_scan, "check_history",
                         return_value=Path("unused_history.db")),
            patch.object(scheduled_scan, "record_scan", side_effect=record),
            patch.object(
                scheduled_scan,
                "new_fingerprints",
                side_effect=lambda *args, **kwargs: {
                    scheduled_scan.job_fingerprint(job)
                    for job in captured["history"]
                },
            ),
            patch.object(scheduled_scan, "select_relevant_first_seen",
                         return_value=[]),
            patch.object(scheduled_scan, "save_reports", side_effect=save),
            patch.object(scheduled_scan, "build_review_reports",
                         side_effect=review),
            patch.object(scheduled_scan, "save_review_reports"),
            patch.object(
                scheduled_scan,
                "_atomic_bytes",
                side_effect=diagnostic_writer,
            ),
            patch(
                "stage8_workday_persistent_collector.collect_configured_workday",
                side_effect=collect_workday,
            ),
            patch(
                "stage8_workday_persistent_collector.prepare_configured_workday_history",
                side_effect=prepare,
            ),
            patch(
                "stage8_workday_batch_handoff.finalize_batch",
                side_effect=finalize,
            ),
        ]

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = scheduled_scan.run_scan(
                root=Path("."),
                collector=collect_legacy,
                collect_workday=opt_in,
            )

        return events, captured, result

    def test_opt_in_combines_history_before_finalization(self):
        events, captured, result = self.run_case(True)

        self.assertEqual(
            events,
            ["collect_workday", "prepare", "record", "finalize"],
        )
        self.assertEqual(
            {j["source"] for j in captured["history"]},
            {"Greenhouse", "Workday"},
        )

    def test_opt_in_keeps_legacy_review_isolated(self):
        events, captured, result = self.run_case(True)

        self.assertEqual(captured["review_jobs"], [LEGACY])
        self.assertEqual(captured["report_listings"], [])

    def test_default_scan_excludes_workday(self):
        events, captured, result = self.run_case(False)

        self.assertEqual(events, ["record"])
        self.assertEqual(captured["history"], [LEGACY])
        self.assertEqual(captured["review_jobs"], [LEGACY])


    def test_history_failure_prevents_finalization(self):
        with self.assertRaisesRegex(
            RuntimeError, "Simulated history failure"
        ):
            self.run_case(True, fail_stage="history")

    def test_finalization_failure_preserves_legacy_scan(self):
        events, captured, summary = self.run_case(
            True, fail_stage="finalize"
        )

        self.assertEqual(
            events,
            ["collect_workday", "prepare", "record", "finalize"],
        )
        self.assertEqual(len(captured["history"]), 2)
        self.assertEqual(captured["review_jobs"], [LEGACY])
        self.assertEqual(summary["collected_count"], 1)
        self.assertIn(
            "Simulated finalization failure",
            summary["workday_collection_error"],
        )
        self.assertNotIn("workday_handoff", summary)


    def test_next_scan_finalizes_pending_handoff_without_new_jobs(self):
        # Simulates an earlier scan that recorded history but failed
        # before acknowledging its Workday handoff.
        first_events, first_captured, first_summary = self.run_case(
            True, fail_stage="finalize"
        )
        self.assertIn("finalize", first_events)

        # On the following scan, the same requisition has no new
        # preparation work, but its pending handoff still needs recovery.
        second_events, second_captured, second_summary = self.run_case(
            True, prepared_jobs=[]
        )

        self.assertEqual(
            second_events,
            ["collect_workday", "prepare", "record", "finalize"],
        )
        self.assertEqual(second_captured["history"], [LEGACY])
        self.assertEqual(second_captured["review_jobs"], [LEGACY])
        self.assertIn("workday_handoff", second_summary)


if __name__ == "__main__":
    unittest.main()
