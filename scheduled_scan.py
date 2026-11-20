"""Stage 5B: unattended *local* Batch Scan for Windows Task Scheduler.

Uses the exact Stage 5A first-seen database. Never edits jobpilot.db,
profile.json, candidate_profile.json, or sources.csv. Does not submit applications,
email anyone, or infer that a missing posting was removed by its employer.

  python scheduled_scan.py --check     # checks readiness; no network / writes
  python scheduled_scan.py --run       # performs one scan and writes local reports
  python scheduled_scan.py --status    # displays latest successful scan; no network

Windows Task Scheduler can run the accompanying run_jobpilot_daily.cmd daily.
"""

import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import NamedTemporaryFile
import os

from application_tracking import SavedJobIndex, already_applied, HistoricalWarningIndex
from collectors import batch_fetch
from discovery_history import job_fingerprint, new_fingerprints, record_scan
from scorer import score_job
from opportunity_review import build_review_reports, save_review_reports

ROOT = Path(__file__).resolve().parent
REPORT_DIR = "monitoring_reports"
COLUMNS = ["company", "title", "location", "score", "role_family", "eligibility",
           "eligibility_note", "recommendation", "saved_status", "url", "posted_at",
           "original_published_at", "original_publication_verified",
           "historical_application_warning"]


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(root):
    """Load the same approved evidence and enabled company sources as the UI."""
    root = Path(root)
    source_path = root / "sources.csv"
    profile_path = root / "profile.json"
    candidate_path = root / "candidate_profile.json"
    for path in (source_path, profile_path, candidate_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required JobPilot file missing: {path.name}")
    with source_path.open(newline="", encoding="utf-8-sig") as f:
        all_sources = list(csv.DictReader(f))
    sources = []
    for s in all_sources:
        if str(s.get("enabled", "True")).strip().lower() not in {"true", "1", "yes", "on"}:
            continue
        ats = s.get("ats", "").strip()
        if ats not in {"Greenhouse", "Lever", "Workday"}:
            continue

        token = s.get("token_or_site", "").strip()
        company = s.get("company", "").strip()

        if not token:
            continue

        if ats == "Workday":
            from stage8_workday_multi_employer_adapter import EMPLOYERS

            employer = EMPLOYERS.get(token)

            if employer is None or company != employer.company:
                raise ValueError(
                    f"Unapproved Workday source configuration: {company} ({token})"
                )

        sources.append({
            "ats": ats,
            "company": company,
            "token_or_site": token,
        })
    if not sources:
        raise ValueError("No enabled Greenhouse/Lever company sources found in sources.csv")
    profile = _json(profile_path)
    candidate = _json(candidate_path)
    if not str(candidate.get("status", "")).startswith("approved_by_user"):
        raise ValueError("candidate_profile.json must be approved; refusing unattended scans using unverified evidence")
    matching_profile = dict(profile)
    matching_profile["candidate_profile"] = candidate
    matching_profile["citizenship_status"] = (
        "us_citizen" if candidate.get("preferences", {}).get("citizenship") else "unknown"
    )
    return sources, matching_profile


def load_saved_jobs_read_only(root):
    """Read applied statuses; opening the SQLite database must NEVER create or edit it."""
    path = Path(root) / "jobpilot.db"
    if not path.is_file():
        raise FileNotFoundError("jobpilot.db missing; stopping rather than losing applied-job exclusions")
    # URI mode=ro prevents an unintended new database if path is wrong.
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM jobs").fetchall()]



def load_historical_warnings_read_only(root):
    """Load imported ledger for advisory warnings only, without DB writes."""
    path = Path(root) / "jobpilot.db"
    if not path.is_file():
        raise FileNotFoundError("jobpilot.db missing")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             ("rabbitresume_unmatched_history",)).fetchone()
        if not exists:
            return []  # Backward compatibility for test fixtures and older DBs.
        return [dict(row) for row in con.execute(
            "SELECT company, title, status, reason FROM rabbitresume_unmatched_history").fetchall()]


def check_history(root):
    """Stage 5A must be installed and have an established scan baseline."""
    path = Path(root) / "discovery_history.db"
    if not path.is_file():
        raise FileNotFoundError("discovery_history.db missing; first establish a Stage 5A baseline in Batch Scan")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as con:
        if not con.execute("SELECT 1 FROM discovery_scans LIMIT 1").fetchone():
            raise ValueError("Discovery database has no scan baseline; use Batch Scan first")
    return path


def _csv_text(value):
    """Avoid interpreting untrusted employer-supplied titles as Excel formulas."""
    s = str(value or "")
    if s.lstrip().startswith(("=", "+", "-", "@")) or s[:1] in {"\t", "\r", "\n"}:
        return "'" + s
    return s


def select_relevant_first_seen(jobs, new_ids, saved_rows, min_score=45, scorer=score_job, historical_rows=()):
    """Choose first-seen, target-family jobs worth reviewing, never auto-apply.

    A geographic 'review' result stays unverified (US remote != CA eligible).
    A score is a heuristic, not a qualification guarantee.
    """
    saved = SavedJobIndex(saved_rows)
    historical_index = HistoricalWarningIndex(historical_rows)
    selected = []
    for job, matching_profile in jobs:
        fp = job_fingerprint(job)
        if fp is None or fp not in new_ids:
            continue
        result = scorer(job, matching_profile)
        if result["score"] < min_score:
            continue
        family = result.get("matched_family") or "Other"
        geo = result.get("geo_status", "review")
        if family == "Other" or geo == "blocked":
            continue
        existing, _ = saved.lookup(job)
        if existing and already_applied(existing.get("status")):
            continue
        selected.append({
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "score": result["score"],
            "role_family": family,
            "eligibility": geo,
            "eligibility_note": result.get("geo_reason", ""),
            "recommendation": result.get("recommendation", ""),
            "saved_status": existing.get("status", "") if existing else "Untracked",
            "url": job.get("url", ""),
            "posted_at": job.get("posted_at", ""),
            "original_published_at": job.get("original_published_at", ""),
            "original_publication_verified": job.get(
                "original_publication_verified", False
            ),
            "historical_application_warning": historical_index.warning(job),
        })
    # The highest heuristic fit comes first; still requires human review.
    return sorted(selected, key=lambda j: (-j["score"], str(j["company"]), str(j["title"])))


def _atomic_bytes(destination, data):
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".tmp_", delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(data)
        os.replace(tmp_name, destination)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_reports(root, summary, listings):
    """Reports are snapshots of the latest completed scheduled scan (not job DB)."""
    import io
    folder = Path(root) / REPORT_DIR
    csv_out = io.StringIO(newline="")
    writer = csv.DictWriter(csv_out, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for listing in listings:
        writer.writerow({key: _csv_text(listing.get(key, "")) for key in COLUMNS})
    data = csv_out.getvalue().encode("utf-8-sig")
    # Durable archive: a later scheduled scan with zero matches must not erase
    # the user's only record of relevant first-seen job leads.
    if listings and summary.get("scan_id") is not None:
        archive = folder / f"first_seen_matches_scan_{int(summary['scan_id']):06d}.csv"
        if not archive.exists():
            _atomic_bytes(archive, data)
    # Keep previous successful report if a scan fails before reaching this point.
    _atomic_bytes(folder / "latest_first_seen_matches.csv", data)
    _atomic_bytes(folder / "last_successful_scan.json",
                  (json.dumps(summary, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def run_scan(root=ROOT, max_age_days=None, min_score=None, collector=batch_fetch,
             scorer=score_job, collect_workday=False, verify_greenhouse=False):
    """Perform one scheduled scan. No Streamlit dependency and no app DB writes."""
    root = Path(root)

    # Central policy supplies defaults; explicit values override it.
    # Validate before collection or database modifications.
    from jobpilot_config import load_policy
    policy = load_policy(root)

    if max_age_days is None:
        max_age_days = policy["discovery_window_days"]
    if min_score is None:
        min_score = policy["minimum_relevance_score"]

    if (
        type(max_age_days) is not int
        or not 1 <= max_age_days <= 365
        or type(min_score) is not int
        or not 0 <= min_score <= 100
    ):
        raise ValueError("Invalid discovery window or relevance score")

    sources, profile = load_settings(root)
    saved_rows = load_saved_jobs_read_only(root)
    historical_rows = load_historical_warnings_read_only(root)
    history_path = check_history(root)
    print(f"Enabled sources: {len(sources)}. Starting scheduled scan (Greenhouse update window {max_age_days} days).", flush=True)
    legacy_sources = [s for s in sources if s["ats"] in ("Greenhouse", "Lever")]
    workday_sources = [s for s in sources if s["ats"] == "Workday"]

    if not legacy_sources:
        raise RuntimeError(
            "No Greenhouse/Lever sources enabled; refusing to alter the existing scan workflow"
        )

    # Stage 8 policy is loaded only for explicitly enabled
    # Greenhouse verification. Invalid settings fail before any
    # discovery-history changes or network collection.
    greenhouse_policy = None
    if verify_greenhouse:
        from jobpilot_config import load_policy
        greenhouse_policy = load_policy(root)

    jobs, errors = collector(
        legacy_sources,
        max_age_days=max_age_days,
        verify_first_published=False,
    )
    if not jobs:
        # Continue pending verification after a collection outage,
        # but do not create a discovery scan or notification archive.
        if verify_greenhouse:
            try:
                from stage8_greenhouse_scan_bridge import (
                    process_greenhouse_notifications,
                )
                process_greenhouse_notifications(
                    root=root,
                    sources=legacy_sources,
                    first_seen_jobs=[],
                    profile=profile,
                    saved_rows=saved_rows,
                    scorer=scorer,
                    scan_id=None,
                    min_score=min_score,
                    historical_rows=historical_rows,
                    max_requests=greenhouse_policy["greenhouse_verification_requests"],
                )
            except Exception as exc:
                print(
                    f"WARNING: Greenhouse queue recovery failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        raise RuntimeError("No listings collected; keeping discovery history and previous report unchanged")
    # Workday collection is explicitly opt-in. Prepare durable intentions
    # before the shared discovery-history transaction.
    workday_queue = root / "stage8_workday_queue.db"
    workday_results = None
    workday_error = None
    workday_handoff = None
    workday_jobs = []
    workday_preparation_succeeded = False

    if collect_workday and workday_sources:
        from stage8_workday_persistent_collector import (
            collect_configured_workday,
            prepare_configured_workday_history,
        )

        try:
            workday_results = collect_configured_workday(
                workday_sources, workday_queue
            )
            workday_jobs = prepare_configured_workday_history(
                workday_queue, history_path
            )
            workday_preparation_succeeded = True
        except Exception as exc:
            workday_error = str(exc)
            workday_jobs = []
            print(
                f"WARNING: Workday preparation failed: {exc}",
                file=sys.stderr, flush=True,
            )

    # One history transaction. Legacy reporting remains separate.
    run = record_scan(
        jobs + workday_jobs,
        source_errors=errors,
        path=history_path,
    )

    # Recover pending handoffs from previous interrupted scans, even
    # when no Workday jobs were prepared during the current scan.
    # Never finalize after a failed collection/preparation attempt.
    if workday_preparation_succeeded:
        from stage8_workday_batch_handoff import finalize_batch

        try:
            workday_handoff = finalize_batch(
                workday_queue, history_path
            )
        except Exception as exc:
            workday_error = str(exc)
            print(
                f"WARNING: Workday handoff incomplete: {exc}",
                file=sys.stderr, flush=True,
            )

    fresh = new_fingerprints(run["scan_id"], path=history_path)
    legacy_fresh = {
        job_fingerprint(job) for job in jobs
    } & fresh
    matches = select_relevant_first_seen(((j, profile) for j in jobs), fresh, saved_rows,
                                         min_score=min_score, scorer=scorer, historical_rows=historical_rows)
    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan_id": run["scan_id"],
        "collected_count": len(jobs),
        "previously_seen_count": (
            len({job_fingerprint(job) for job in jobs if job_fingerprint(job)})
            - len(legacy_fresh)
        ),
        "first_seen_by_jobpilot_count": len(legacy_fresh),
        "candidate_matches_for_review": len(matches),
        "source_error_count": len(errors),
        "source_errors": errors,
        "minimum_score": min_score,
        "greenhouse_updated_within_days": max_age_days,
        "note": "First seen by JobPilot is not employer publication date. CA remote and qualifications require verification. No applications were submitted."
    }
    save_reports(root, summary, matches)

    # Stage 8 Greenhouse verification is explicitly opt-in.
    # Discovery history and normal first-seen reports are already durable.
    if verify_greenhouse:
        from stage8_greenhouse_scan_bridge import (
            process_greenhouse_notifications,
        )

        from stage8_greenhouse_recovery import (
            recoverable_fingerprints,
        )

        # Recover recently discovered candidates that were not
        # durably queued because of an earlier interrupted scan.
        recoverable = recoverable_fingerprints(
            history_path, jobs, days=greenhouse_policy["greenhouse_recovery_days"]
        )
        recovery_matches = select_relevant_first_seen(
            ((job, profile) for job in jobs),
            recoverable,
            saved_rows,
            min_score=min_score,
            scorer=scorer,
            historical_rows=historical_rows,
        )
        selected_urls = {
            str(row.get("url") or "").strip()
            for row in recovery_matches
            if row.get("url")
        }
        greenhouse_candidates = [
            job for job in jobs
            if job.get("source") == "Greenhouse"
            and str(job.get("url") or "").strip() in selected_urls
            and job_fingerprint(job) in recoverable
        ]

        try:
            greenhouse_result = process_greenhouse_notifications(
                root=root,
                sources=legacy_sources,
                first_seen_jobs=greenhouse_candidates,
                profile=profile,
                saved_rows=saved_rows,
                scorer=scorer,
                scan_id=run["scan_id"],
                min_score=min_score,
                historical_rows=historical_rows,
                max_requests=greenhouse_policy["greenhouse_verification_requests"],
            )
            summary["greenhouse_verification"] = greenhouse_result
            print(
                "Greenhouse verification: "
                f"{greenhouse_result['verification_attempts']} attempted, "
                f"{greenhouse_result['verified']} verified, "
                f"{greenhouse_result['notification_matches']} eligible.",
                flush=True,
            )
        except Exception as exc:
            summary["greenhouse_verification_error"] = str(exc)
            print(
                f"WARNING: Greenhouse verification incomplete: {exc}",
                file=sys.stderr,
                flush=True,
            )

    # Additive audit: older relevant openings and explicit screening explanations.
    # Never let an optional report failure interrupt the established 7 AM scan,
    # application-history protection, or first-seen Gmail delivery workflow.
    try:
        review, audit, counts = build_review_reports(
            jobs, profile, saved_rows, fresh, scorer=scorer, historical_rows=historical_rows
        )
        save_review_reports(root, review, audit, counts, scan_id=run["scan_id"])
        print(f"Full review report: {counts['review']} possible opportunities "
              f"(including {counts['stretch_review']} stretch reviews) among "
              f"{counts['collected']} collected listings.", flush=True)
    except Exception as report_exc:
        print(f"WARNING: optional full-review report failed: {report_exc}", file=sys.stderr, flush=True)
    print(f"Finished: {len(jobs)} collected, {run['known_count']} previously seen, "
          f"{run['new_count']} first seen, {len(matches)} candidates for review, "
          f"{len(errors)} source errors.", flush=True)
    print(f"Local report: {root / REPORT_DIR / 'latest_first_seen_matches.csv'}", flush=True)
    if collect_workday and workday_sources:
        # Workday remains excluded from legacy first-seen reports and email.
        workday_status = {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "requested_employers": [
                source["company"] for source in workday_sources
            ],
            "status": (
                "failed" if workday_results is None
                else "partial" if workday_error
                else "completed"
            ),
            "results": workday_results or [],
            "handoff": workday_handoff,
            "prepared_jobs": len(workday_jobs),
            "error": workday_error,
            "email_enabled": False,
        }

        if workday_results is not None:
            summary["workday_collection"] = workday_results

        if workday_handoff is not None:
            summary["workday_handoff"] = workday_handoff

        if workday_error:
            summary["workday_collection_error"] = workday_error

        destination = root / REPORT_DIR / "workday_collection_status.json"

        try:
            _atomic_bytes(
                destination,
                (
                    json.dumps(
                        workday_status,
                        indent=2,
                        ensure_ascii=False,
                    ) + "\n"
                ).encode("utf-8"),
            )
        except Exception as exc:
            print(
                f"WARNING: Workday diagnostic report failed: {exc}",
                file=sys.stderr, flush=True,
            )

    return summary


def _append_scan_log(root, message):
    """Append short local success/failure diagnostics for unattended tasks."""
    try:
        folder = Path(root) / REPORT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "scheduled_scan.log").open("a", encoding="utf-8") as log:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.write(f"{stamp} | {message}\n")
    except OSError:
        # Do not falsely mark a successful completed scan as failed if only
        # optional log writing is unavailable.
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="JobPilot v0.2 Stage 5B local scheduled scanning")
    opts = parser.add_mutually_exclusive_group(required=True)
    opts.add_argument("--check", action="store_true", help="check prerequisites without network or writes")
    opts.add_argument("--run", action="store_true", help="scan once and record first-seen discoveries")
    opts.add_argument("--status", action="store_true", help="display latest successful scan summary")
    parser.add_argument("--max-age-days", type=int, default=None)
    parser.add_argument("--min-score", type=int, default=None)
    parser.add_argument(
        "--collect-workday",
        action="store_true",
        help="Opt-in Workday queue collection; no Workday alerts",
    )
    parser.add_argument(
        "--verify-greenhouse",
        action="store_true",
        help="Opt-in Greenhouse original-publication verification",
    )
    args = parser.parse_args(argv)
    if (
        (args.max_age_days is not None
         and not 1 <= args.max_age_days <= 365)
        or (args.min_score is not None
            and not 0 <= args.min_score <= 100)
    ):
        parser.error("Invalid discovery window or relevance score")
    try:
        if args.check:
            sources, _ = load_settings(ROOT)
            saved = load_saved_jobs_read_only(ROOT)
            path = check_history(ROOT)
            print(f"Ready: {len(sources)} enabled sources; {len(saved)} saved applications; existing Stage 5A history at {path.name}.")
            print("No network requests made and no files modified.")
        elif args.status:
            path = ROOT / REPORT_DIR / "last_successful_scan.json"
            if not path.is_file():
                print("No scheduled scan report yet; run with --run when ready.")
            else:
                print(json.dumps(_json(path), indent=2, ensure_ascii=False))
        else:
            summary = run_scan(
                max_age_days=args.max_age_days,
                min_score=args.min_score,
                collect_workday=args.collect_workday,
                verify_greenhouse=args.verify_greenhouse,
            )
            _append_scan_log(ROOT, f"SUCCESS scan {summary['scan_id']}: "
                             f"{summary['collected_count']} collected; "
                             f"{summary['first_seen_by_jobpilot_count']} first seen; "
                             f"{summary['candidate_matches_for_review']} matches; "
                             f"{summary['source_error_count']} source errors")
        return 0
    except Exception as exc:
        print(f"Stage 5B scan FAILED: {exc}", file=sys.stderr)
        if args.run:
            _append_scan_log(ROOT, f"FAILED: {exc}")
        # Do not overwrite previous successful results with a failure.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
