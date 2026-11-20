"""Persistent Greenhouse publication-verification queue.

Separate from JobPilot discovery history and Workday persistence.
No network, email, or application database operations.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


def _utc(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("now must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _identity(job):
    source = str(job.get("source") or "")
    company = str(job.get("company") or "")
    source_id = str(job.get("source_id") or "")

    if not source or not company or not source_id:
        raise ValueError("Candidate identity is incomplete")

    return source, company, source_id


def _connect(path):
    path = Path(path)
    return sqlite3.connect(path, timeout=10)


def initialize(path):
    """Create the independent verification queue if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(_connect(path)) as connection:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    source TEXT NOT NULL,
                    company TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    verified_published_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, company, source_id)
                )
            """)


def enqueue(path, job, now=None):
    """Insert a candidate without resetting existing retry state."""
    identity = _identity(job)
    timestamp = _utc(now).isoformat()
    payload = json.dumps(job, ensure_ascii=False, sort_keys=True)

    with closing(_connect(path)) as connection:
        with connection:
            connection.execute("""
                INSERT OR IGNORE INTO candidates (
                    source, company, source_id, payload,
                    status, attempts, next_attempt_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            """, (*identity, payload, timestamp, timestamp, timestamp))


def due_candidates(path, now=None, limit=10):
    """Return pending candidates due for verification, within budget."""
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")

    timestamp = _utc(now).isoformat()

    with closing(_connect(path)) as connection:
        rows = connection.execute("""
            SELECT payload
            FROM candidates
            WHERE status = 'pending'
              AND next_attempt_at <= ?
            ORDER BY next_attempt_at, created_at,
                     company, source_id
            LIMIT ?
        """, (timestamp, limit)).fetchall()

    return [json.loads(row[0]) for row in rows]


def mark_verified(path, job, published_at, now=None):
    """Complete verification and permanently stop retrying this identity."""
    identity = _identity(job)
    timestamp = _utc(now).isoformat()

    if not isinstance(published_at, str) or not published_at.strip():
        raise ValueError("Verified publication timestamp is required")

    try:
        publication = datetime.fromisoformat(
            published_at.strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("Invalid publication timestamp") from exc

    if publication.tzinfo is None or publication.utcoffset() is None:
        raise ValueError("Publication timestamp must be timezone-aware")

    with closing(_connect(path)) as connection:
        with connection:
            cursor = connection.execute("""
                UPDATE candidates
                SET status = 'verified',
                    verified_published_at = ?,
                    updated_at = ?
                WHERE source = ? AND company = ? AND source_id = ?
                  AND status = 'pending'
            """, (published_at, timestamp, *identity))

    return cursor.rowcount == 1


def mark_failed(path, job, now=None):
    """Schedule a bounded exponential-backoff retry."""
    identity = _identity(job)
    current = _utc(now)

    with closing(_connect(path)) as connection:
        with connection:
            row = connection.execute("""
                SELECT attempts
                FROM candidates
                WHERE source = ? AND company = ? AND source_id = ?
                  AND status = 'pending'
            """, identity).fetchone()

            if row is None:
                return False

            attempts = row[0] + 1
            delay_hours = min(2 ** (attempts - 1), 48)
            next_attempt = (
                current + timedelta(hours=delay_hours)
            ).isoformat()

            connection.execute("""
                UPDATE candidates
                SET attempts = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE source = ? AND company = ? AND source_id = ?
                  AND status = 'pending'
            """, (
                attempts, next_attempt, current.isoformat(),
                *identity,
            ))

    return True
