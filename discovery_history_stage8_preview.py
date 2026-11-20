"""Persistent, local-only 'first seen by JobPilot' records for Batch Scan.

This is NOT employer publication history. A job can be old when JobPilot first
notices it. No attempts are made to infer closings from missing scan results:
partial source failures and Greenhouse age filters make that unsafe.

This module uses a separate SQLite database, never jobpilot.db, and never
changes an application or the user's approved profile/source files.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from application_tracking import exact_keys
from stage8_workday_identity import workday_key

HISTORY_PATH = Path(__file__).parent / "discovery_history.db"


def job_fingerprint(job):
    """Return a stable identifier, or None when the posting is unidentifiable.

    Uses the same conservative ATS/URL identity rules as Stage 4; never guesses
    identity from a company+title pair, since multiple openings can share both.
    """
    # Workday identity is strictly employer/tenant/board/requisition-based.
    # Never fall back to URL or company/title if the Workday record is untrusted.
    if str(job.get("source", "")).strip().casefold() == "workday":
        identity = workday_key(job)
        if identity is None:
            return None
        canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    keys = exact_keys(job)
    if not keys:
        return None
    # Canonical ATS identity is preferred to a marketing or tracking URL.
    identity = next((key for key in keys if key[0] in ("gh", "lever")), None)
    if identity is None:
        identity = next((key for key in keys if key[0] == "lever_source"), keys[0])
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect(path):
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _init_tables(con):
    con.execute("""
    CREATE TABLE IF NOT EXISTS discovery_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scanned_at_utc TEXT NOT NULL,
        baseline INTEGER NOT NULL,
        discovered_count INTEGER NOT NULL,
        previously_seen_count INTEGER NOT NULL,
        recorded_count INTEGER NOT NULL,
        skipped_count INTEGER NOT NULL,
        source_error_count INTEGER NOT NULL
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS discovery_jobs (
        fingerprint TEXT PRIMARY KEY,
        source TEXT,
        source_id TEXT,
        company TEXT,
        title TEXT,
        location TEXT,
        url TEXT,
        first_seen_utc TEXT NOT NULL,
        last_seen_utc TEXT NOT NULL,
        first_scan_id INTEGER NOT NULL,
        last_scan_id INTEGER NOT NULL,
        times_seen INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(first_scan_id) REFERENCES discovery_scans(id)
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_discovery_first_scan ON discovery_jobs(first_scan_id)")


def record_scan(jobs, source_errors=(), path=HISTORY_PATH):
    """Atomically record one *completed* Batch Scan, returning run statistics.

    First completed scan becomes the baseline; it cannot prove listings are
    newly posted or newly available. Duplicate occurrences in the same scan
    are recorded only once. Missing jobs are never marked closed.
    """
    distinct = {}
    skipped_count = 0
    for job in jobs:
        fp = job_fingerprint(job)
        if fp:
            distinct.setdefault(fp, job)
        else:
            skipped_count += 1

    # A zero-result/fully failed scan must never erase or establish history.
    if not distinct:
        raise ValueError("No identifiable listings in this scan; history was not changed.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as con, con:
        _init_tables(con)
        is_baseline = not con.execute("SELECT 1 FROM discovery_scans LIMIT 1").fetchone()
        existing = {
            row[0]
            for batch in _chunks(list(distinct), 400)
            for row in con.execute(
                f"SELECT fingerprint FROM discovery_jobs WHERE fingerprint IN ({','.join('?' for _ in batch)})",
                batch,
            ).fetchall()
        }
        # The scan row and all per-job updates commit together.
        new_count = len(distinct) - len(existing)
        recorded_new = 0 if is_baseline else new_count
        cursor = con.execute(
            """INSERT INTO discovery_scans (
                scanned_at_utc, baseline, discovered_count, previously_seen_count,
                recorded_count, skipped_count, source_error_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, int(is_baseline), recorded_new, len(existing), len(distinct),
             skipped_count, len(source_errors)),
        )
        scan_id = cursor.lastrowid
        for fp, job in distinct.items():
            con.execute("""
            INSERT INTO discovery_jobs (
                fingerprint, source, source_id, company, title, location, url,
                first_seen_utc, last_seen_utc, first_scan_id, last_scan_id, times_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(fingerprint) DO UPDATE SET
                source=excluded.source,
                source_id=excluded.source_id,
                company=excluded.company,
                title=excluded.title,
                location=excluded.location,
                url=excluded.url,
                last_seen_utc=excluded.last_seen_utc,
                last_scan_id=excluded.last_scan_id,
                times_seen=discovery_jobs.times_seen+1
            """, (
                fp, job.get("source"), job.get("source_id"), job.get("company"),
                job.get("title"), job.get("location"), job.get("url"),
                now, now, scan_id, scan_id,
            ))
        return {
            "scan_id": scan_id,
            "baseline": is_baseline,
            "new_count": recorded_new,
            "known_count": len(existing),
            "recorded_count": len(distinct),
            "skipped_count": skipped_count,
            "source_error_count": len(source_errors),
        }


def _chunks(items, n):
    for start in range(0, len(items), n):
        yield items[start:start + n]


def new_fingerprints(scan_id, path=HISTORY_PATH):
    """Identify first-seen listings from a non-baseline scan; read-only."""
    with closing(_connect(path)) as con, con:
        _init_tables(con)
        run = con.execute("SELECT baseline FROM discovery_scans WHERE id=?", (scan_id,)).fetchone()
        if not run or run[0]:
            return set()
        return {row[0] for row in con.execute(
            "SELECT fingerprint FROM discovery_jobs WHERE first_scan_id=?", (scan_id,)
        ).fetchall()}
