"""Read-only recovery of recently discovered Greenhouse candidates."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from discovery_history import job_fingerprint


def recoverable_fingerprints(history_path, jobs, days=14, now=None):
    """Recover non-baseline discoveries still present in current results."""
    if not 1 <= days <= 90:
        raise ValueError("Invalid recovery window")

    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=days)).isoformat(
        timespec="seconds"
    )

    history_path = Path(history_path)

    if not history_path.is_file():
        raise FileNotFoundError(history_path)

    uri = history_path.resolve().as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as connection:
        rows = connection.execute(
            """
            SELECT j.fingerprint
            FROM discovery_jobs AS j
            JOIN discovery_scans AS s
              ON s.id = j.first_scan_id
            WHERE j.source = 'Greenhouse'
              AND j.first_seen_utc >= ?
              AND s.baseline = 0
            """,
            (cutoff,),
        ).fetchall()

    eligible = {row[0] for row in rows}

    return {
        fingerprint
        for job in jobs
        if job.get("source") == "Greenhouse"
        if (fingerprint := job_fingerprint(job)) in eligible
    }
