"""Stage 8: OFFLINE, temporary-database Workday queue -> existing JobPilot screening integration.

Run from JobPilot_v0.2 - Copy:
    python -B stage8_workday_discovery_integration_test.py

Required sibling files: stage8_workday_queue_prototype.py, discovery_history.py,
scheduled_scan.py, scorer.py, opportunity_review.py and their existing dependencies.
No network, SMTP, production database, existing report, or source-catalog operations.
All SQLite paths are freshly created inside TemporaryDirectory().

Scope: demonstrates recovery and existing PURE screening/report builders, NOT a
production-integrated Workday adapter, persistent sent-email queue, or exactly-once
alert delivery. Calls into the existing production modules but no main entrypoint.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from stage8_workday_queue_prototype import (
    SimulatedCrash, connect, fail_detail, finish_detail, handoff_once,
    initialize, states, upsert_index,
)
from discovery_history import job_fingerprint, new_fingerprints, record_scan
from scheduled_scan import select_relevant_first_seen
from opportunity_review import build_review_reports
from scorer import score_job
from alert_policy import email_eligible


def job(req, title, location, suffix="", description=""):
    tail = f"_{req}{suffix}"
    path = f"/job/USA---Example-CA/{title.replace(' ', '-')}{tail}"
    return {
        "source": "Workday", "source_id": req, "company": "CrowdStrike",
        "title": title, "location": location,
        "url": "https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers" + path,
        "external_path": path, "description": description, "posted_at": "Unknown",
    }


def queue_job(queue_path, key):
    with closing(connect(queue_path)) as con:
        record = con.execute("SELECT job_json FROM workday_queue WHERE job_key=?", (key,)).fetchone()
    assert record
    return json.loads(record["job_json"])


def handoff_state(queue_path, key):
    with closing(connect(queue_path)) as con:
        row = con.execute("SELECT state, first_scan_id FROM handoff WHERE job_key=?", (key,)).fetchone()
    return dict(row) if row else None


def history_counts(history_path):
    with closing(sqlite3.connect(history_path)) as con:
        return (con.execute("SELECT COUNT(*) FROM discovery_jobs").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM discovery_scans").fetchone()[0])


def main():
    # Minimal synthetic profile: real scorer and review functions, no private profile reads.
    profile = {
        "target_role_families": {
            "IT Support": ["it support", "technical support"],
            "Customer/Client Support": ["customer support", "client support"],
        },
        "skills": ["troubleshooting", "ticketing"],
        "degree_status": "no_bachelors",
        "candidate_profile": {
            "status": "approved_by_user_test_fixture",
            "preferences": {"relocation": {"minimum_annual_salary_usd": 75000}},
        },
    }
    with TemporaryDirectory(prefix="jobpilot_stage8_workday_integration_") as td:
        root = Path(td)
        queue = root / "isolated_workday_queue.db"
        history = root / "isolated_discovery_history.db"
        initialize(queue)
        baseline = job("R00001", "Baseline Support", "San Diego, CA", description="Baseline fixture")
        assert record_scan([baseline], path=history)["baseline"] is True
        assert history_counts(history) == (1, 1)

        local = job("R30448", "IT Support Technician", "San Diego, CA", "-1")
        remote = job("R30496", "Community/Player Support Specialist", "USA - Remote", "-1")
        key_local, key_remote = upsert_index(queue, local), upsert_index(queue, remote)
        assert key_local != key_remote
        assert history_counts(history) == (1, 1)
        # The same ID survives a changed title/path before enrichment.
        local_new_path = job("R30448", "IT Support Specialist", "San Diego, CA")
        assert upsert_index(queue, local_new_path) == key_local
        assert states(queue, key_local)[0]["status"] == "pending"
        assert history_counts(history) == (1, 1)
        print("PASS 1: Pending Workday records survive repeat indexing and do not enter discovery history")

        fail_detail(queue, key_local, "temporary HTTP 503")
        assert states(queue, key_local)[0]["attempts"] == 1
        try:
            handoff_once(queue, history, key_local)
            raise AssertionError("Pending record reached history")
        except ValueError:
            pass
        finish_detail(queue, key_local,
                      "Help desk IT support: troubleshoot user laptops, document ticketing incidents, "
                      "and work with internal teams. Customer technical support and troubleshooting.")
        finish_detail(queue, key_remote,
                      "Assist players and community members with account support, customer service, "
                      "ticketing, troubleshooting and technical issues through documented support cases.")
        assert all(states(queue, key)[0]["status"] == "ready" for key in (key_local, key_remote))
        assert history_counts(history) == (1, 1)
        print("PASS 2: Temporary detail failure is retryable; only enriched records are eligible for handoff")

        try:
            handoff_once(queue, history, key_local, crash_at="after_history")
            raise AssertionError("Crash hook not triggered")
        except SimulatedCrash:
            pass
        assert handoff_state(queue, key_local)["state"] == "await_history"
        assert history_counts(history) == (2, 2)
        # Restart: the existing history row must be recovered, not inserted again.
        assert handoff_once(queue, history, key_local) == "candidate_acknowledged"
        event = handoff_state(queue, key_local)
        assert event["first_scan_id"] == 2
        assert history_counts(history) == (2, 2)
        print("PASS 3: Crash after history commit recovers the staged first-seen reference without another history row")

        try:
            handoff_once(queue, history, key_remote, crash_at="after_candidate")
            raise AssertionError("Candidate crash hook not triggered")
        except SimulatedCrash:
            pass
        assert handoff_state(queue, key_remote)["state"] == "candidate_ready"
        assert handoff_once(queue, history, key_remote) == "candidate_acknowledged"
        assert handoff_once(queue, history, key_remote) == "already_acknowledged"
        assert history_counts(history) == (3, 3)
        print("PASS 4: Repeated handoff and post-staging crash do not duplicate discovery-job records")

        # Actual existing scorer, first-seen selector and broad review builder.
        screened = []
        for key in (key_local, key_remote):
            item = queue_job(queue, key)
            event = handoff_state(queue, key)
            assert job_fingerprint(item) in new_fingerprints(event["first_scan_id"], path=history)
            score = score_job(item, profile)
            selected = select_relevant_first_seen(
                [(item, profile)], {job_fingerprint(item)}, [],
                min_score=0, scorer=score_job,
            )
            review, audit, counts = build_review_reports(
                [item], profile, [], {job_fingerprint(item)}, scorer=score_job,
            )
            assert len(audit) == 1 and counts["collected"] == 1
            screened.append((item, score, selected, review))
        assert screened[0][1]["geo_status"] == "local", screened[0][1]
        assert screened[1][1]["geo_status"] == "review", screened[1][1]
        assert screened[0][2], "Local IT Support job should enter first-seen selector at diagnostic threshold"
        assert email_eligible(screened[0][2][0]) is True
        assert not any(email_eligible(x) for x in screened[1][2])
        print("PASS 5: Existing scorer and review builder process ready Workday records; uncertain remote remains non-email-eligible")

        # Real history uses URL fingerprints for Workday today. This is a known
        # integration blocker, not something this isolated prototype fixes.
        changed = queue_job(queue, key_local).copy()
        changed["url"] = changed["url"].replace("IT-Support-Technician", "IT-Support-Technician-Updated")
        assert job_fingerprint(changed) != job_fingerprint(queue_job(queue, key_local))
        assert upsert_index(queue, job("R30448", "Another Retitled Posting", "San Diego, CA", "-1")) == key_local
        print("KNOWN GAP: Existing JobPilot history uses Workday URL identity; same requisition with changed URL would produce a different fingerprint")
        print("KNOWN GAP: Queue self-test does not yet implement retry aging, meaningful description quality checks, or real delivery acknowledgment")
        print("SUCCESS: Offline queue-to-existing-scoring integration and interruption recovery verified within the stated scope")
        print("No network, SMTP, original project database/report writes, or collector/configuration changes.")


if __name__ == "__main__":
    main()
