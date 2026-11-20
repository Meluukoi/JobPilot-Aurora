"""Stage 5A tests: python -m unittest -v test_discovery_history.py"""
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from discovery_history import job_fingerprint, new_fingerprints, record_scan


def gh(jid, title="Customer Success Specialist", url=None):
    return dict(source="Greenhouse", source_id=str(jid), company="Lawmatics",
                title=title, location="San Diego, CA",
                url=url or f"https://job-boards.greenhouse.io/lawmatics/jobs/{jid}")


def lever(jid, board="mycompany"):
    return dict(source="Lever", source_id=jid, company="MyCompany",
                title="IT Support", location="Remote",
                url=f"https://jobs.lever.co/{board}/{jid}")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "history.db"

    def test_first_scan_is_baseline_not_a_new_posting_alert(self):
        a, b = gh(100), gh(200, "IT Support")
        r = record_scan([a, b, a], path=self.path)
        self.assertTrue(r["baseline"])
        self.assertEqual(r["new_count"], 0)
        self.assertEqual(r["recorded_count"], 2)
        self.assertEqual(new_fingerprints(r["scan_id"], self.path), set())
        self.assertTrue(self.path.exists())

    def test_subsequent_scan_flags_only_new_ats_ids(self):
        a, b = gh(100), gh(200)
        record_scan([a], path=self.path)
        next_run = record_scan([a, b], path=self.path)
        self.assertFalse(next_run["baseline"])
        self.assertEqual(next_run["new_count"], 1)
        self.assertEqual(next_run["known_count"], 1)
        self.assertEqual(new_fingerprints(next_run["scan_id"], self.path), {job_fingerprint(b)})
        with closing(sqlite3.connect(self.path)) as con:
            count = con.execute("SELECT times_seen FROM discovery_jobs WHERE fingerprint=?",
                                (job_fingerprint(a),)).fetchone()[0]
        self.assertEqual(count, 2)

    def test_same_posting_url_changes_are_not_new(self):
        a = gh(100, url="https://boards.greenhouse.io/lawmatics/jobs/100?utm_source=x")
        record_scan([a], path=self.path)
        b = gh(100, title="Renamed", url="https://job-boards.greenhouse.io/lawmatics/jobs/100?gh_src=y")
        result = record_scan([b], path=self.path)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(job_fingerprint(a), job_fingerprint(b))

    def test_no_inference_of_closed_jobs_after_partial_error(self):
        a,b = gh(100),gh(200)
        record_scan([a,b], path=self.path)
        result = record_scan([a], source_errors=["timeout"], path=self.path)
        self.assertEqual(result["source_error_count"], 1)
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute("SELECT fingerprint FROM discovery_jobs").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(result["new_count"], 0)

    def test_unidentifiable_records_do_not_create_fake_matches(self):
        a = gh(100)
        bad = {"source":"Manual", "company":"ABC", "title":"Analyst", "url":""}
        result = record_scan([a, bad], path=self.path)
        self.assertEqual(result["skipped_count"], 1)
        self.assertIsNone(job_fingerprint(bad))

    def test_zero_results_does_not_establish_baseline(self):
        with self.assertRaisesRegex(ValueError, "No identifiable"):
            record_scan([], source_errors=["timeout"], path=self.path)
        self.assertFalse(self.path.exists())
        self.assertTrue(record_scan([gh(100)], path=self.path)["baseline"])

    def test_lever_job_recognized_across_scans(self):
        lid = "49c5fc11-58d4-4780-8c3b-6ef010122b22"
        a = lever(lid)
        record_scan([a], path=self.path)
        b = lever(lid)
        b["url"] += "?lever-source=LinkedIn"
        self.assertEqual(record_scan([b],path=self.path)["new_count"], 0)

    def test_history_is_independent_of_application_database(self):
        record_scan([gh(100)], path=self.path)
        self.assertFalse((Path(self.tmp.name) / "jobpilot.db").exists())


if __name__ == "__main__":
    unittest.main()
