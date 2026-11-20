"""Stage 5B offline tests: python -m unittest -v test_scheduled_scan.py"""
import csv
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from discovery_history import record_scan
from scheduled_scan import (load_settings, load_saved_jobs_read_only,
                            select_relevant_first_seen, run_scan, save_reports,
                            check_history)


def listing(job_id, title="IT Support Specialist", location="San Diego, CA"):
    return dict(source="Greenhouse", source_id=str(job_id), company="Lawmatics",
                title=title, location=location,
                url=f"https://job-boards.greenhouse.io/lawmatics/jobs/{job_id}",
                description="Customer support troubleshooting", posted_at="Unknown")


def fake_score(j, profile):
    if "Accountant" in j["title"]:
        return dict(score=0, matched_family="Other", geo_status="review",
                    recommendation="VERIFY", geo_reason="Not a target role")
    if "London" in j["location"]:
        return dict(score=70, matched_family="IT Support", geo_status="blocked",
                    recommendation="BLOCKED", geo_reason="Outside location")
    return dict(score=77, matched_family="IT Support", geo_status="local",
                recommendation="STRONG APPLY", geo_reason="San Diego area")


class ScheduledScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sources.csv").write_text(
            "enabled,ats,company,token_or_site\nTrue,Greenhouse,Lawmatics,lawmatics\n"
            "False,Greenhouse,Off Board,off\nTrue,Other,Ignored,other\n", encoding="utf-8")
        (self.root / "profile.json").write_text(json.dumps({"my_key": "my_value"}), encoding="utf-8")
        (self.root / "candidate_profile.json").write_text(json.dumps({
            "status": "approved_by_user_test", "preferences": {"citizenship": "us_citizen"}
        }), encoding="utf-8")
        with closing(sqlite3.connect(self.root / "jobpilot.db")) as con, con:
            con.execute("CREATE TABLE jobs (source TEXT, source_id TEXT, company TEXT, title TEXT, "
                        "location TEXT, url TEXT, status TEXT)")
            job = listing(20)
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (
                job["source"], job["source_id"], job["company"], job["title"],
                job["location"], job["url"], "Applied"))
        self.first = listing(10)
        record_scan([self.first], path=self.root / "discovery_history.db")

    def test_no_writes_from_settings_and_readiness(self):
        before = {n: (self.root / n).read_bytes() for n in
                  ("sources.csv", "profile.json", "candidate_profile.json", "jobpilot.db", "discovery_history.db")}
        sources, profile = load_settings(self.root)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["company"], "Lawmatics")
        self.assertEqual(profile["my_key"], "my_value")
        self.assertEqual(profile["citizenship_status"], "us_citizen")
        self.assertEqual(len(load_saved_jobs_read_only(self.root)), 1)
        self.assertTrue(check_history(self.root).exists())
        self.assertEqual(before, {n: (self.root / n).read_bytes() for n in before})
        self.assertFalse((self.root / "monitoring_reports").exists())

    def test_reject_unapproved_candidate(self):
        path = self.root / "candidate_profile.json"
        path.write_text(json.dumps({"status": "draft"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "approved"):
            load_settings(self.root)

    def test_missing_application_db_must_not_be_created(self):
        (self.root / "jobpilot.db").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "jobpilot.db"):
            load_saved_jobs_read_only(self.root)
        self.assertFalse((self.root / "jobpilot.db").exists())

    def test_history_db_is_required_and_never_created_implicitly(self):
        (self.root / "discovery_history.db").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "discovery_history.db"):
            check_history(self.root)
        self.assertFalse((self.root / "discovery_history.db").exists())

    def test_result_selection_excludes_applied_blocked_non_target_and_old(self):
        from discovery_history import job_fingerprint
        new = listing(30)
        applied = listing(20)
        blocked = listing(40, location="London")
        irrelevant = listing(50, title="Senior Staff Accountant")
        jobs = [(j, {}) for j in [new, self.first, applied, blocked, irrelevant]]
        fids = {job_fingerprint(j) for j in [new, applied, blocked, irrelevant]}
        selected = select_relevant_first_seen(jobs, fids, load_saved_jobs_read_only(self.root),
                                              scorer=fake_score)
        self.assertEqual([r["title"] for r in selected], ["IT Support Specialist"])
        self.assertEqual(selected[0]["url"], new["url"])
        self.assertEqual(selected[0]["saved_status"], "Untracked")

    def test_real_scan_update_and_repeat_does_not_realert(self):
        old = self.first
        new = listing(30)
        applied = listing(20)
        before = {n: hashlib.sha256((self.root / n).read_bytes()).hexdigest()
                  for n in ("sources.csv", "profile.json", "candidate_profile.json", "jobpilot.db")}
        calls = []
        def collector(sources, max_age_days, verify_first_published):
            calls.append((len(sources), max_age_days, verify_first_published))
            return [old, new, applied], ["One board timed out"]
        first = run_scan(self.root, collector=collector, scorer=fake_score)
        self.assertEqual(first["first_seen_by_jobpilot_count"], 2)
        self.assertEqual(first["candidate_matches_for_review"], 1)
        self.assertEqual(first["source_error_count"], 1)
        self.assertEqual(calls, [(1, 45, False)])
        with (self.root / "monitoring_reports" / "latest_first_seen_matches.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], new["url"])
        archive = self.root / "monitoring_reports" / f"first_seen_matches_scan_{first['scan_id']:06d}.csv"
        self.assertTrue(archive.exists())
        archived_before = archive.read_bytes()
        again = run_scan(self.root, collector=collector, scorer=fake_score)
        self.assertEqual(again["first_seen_by_jobpilot_count"], 0)
        self.assertEqual(again["candidate_matches_for_review"], 0)
        self.assertEqual(archived_before, archive.read_bytes())
        self.assertEqual(before, {n: hashlib.sha256((self.root / n).read_bytes()).hexdigest() for n in before})
        with (self.root / "monitoring_reports" / "latest_first_seen_matches.csv").open(encoding="utf-8-sig", newline="") as f:
            self.assertEqual(list(csv.DictReader(f)), [])

    def test_no_jobs_preserves_history_and_prior_successful_report(self):
        out = {"scan_id": 77, "first_seen_by_jobpilot_count": 1}
        save_reports(self.root, out, [{"company": "SafeCo", "title": "Support"}])
        hist = (self.root / "discovery_history.db").read_bytes()
        summary = (self.root / "monitoring_reports" / "last_successful_scan.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "No listings"):
            run_scan(self.root, collector=lambda *a, **kw: ([], ["network unavailable"]), scorer=fake_score)
        self.assertEqual(hist, (self.root / "discovery_history.db").read_bytes())
        self.assertEqual(summary, (self.root / "monitoring_reports" / "last_successful_scan.json").read_bytes())

    def test_untrusted_job_text_is_csv_formula_sanitized(self):
        save_reports(self.root, {"ok": True}, [{"company": "=1+2", "title": "\t=HYPERLINK(...)"}])
        with (self.root / "monitoring_reports" / "latest_first_seen_matches.csv").open(encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f))
        self.assertTrue(row["company"].startswith("'="))
        self.assertTrue(row["title"].startswith("'"))


if __name__ == "__main__":
    unittest.main()
