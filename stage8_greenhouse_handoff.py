"""Durable Greenhouse verification handoff.

Tracks transfer of verified candidates to downstream processing.
Does not send email or alter discovery history.
"""

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

from stage8_greenhouse_queue import _identity


def _connect(path):
    return sqlite3.connect(path, timeout=10)


def _initialize(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS greenhouse_handoffs (
            source TEXT NOT NULL,
            company TEXT NOT NULL,
            source_id TEXT NOT NULL,
            handed_off_at TEXT NOT NULL,
            PRIMARY KEY (source, company, source_id)
        )
    """)


def ready_for_handoff(path, limit=100):
    """Return verified candidates not yet handed off."""
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")

    with closing(_connect(path)) as connection:
        with connection:
            _initialize(connection)

        rows = connection.execute("""
            SELECT c.payload, c.verified_published_at
            FROM candidates AS c
            LEFT JOIN greenhouse_handoffs AS h
              ON c.source = h.source
             AND c.company = h.company
             AND c.source_id = h.source_id
            WHERE c.status = 'verified'
              AND c.verified_published_at IS NOT NULL
              AND h.source_id IS NULL
            ORDER BY c.created_at, c.company, c.source_id
            LIMIT ?
        """, (limit,)).fetchall()

    results = []

    for payload, published_at in rows:
        job = json.loads(payload)
        job["original_published_at"] = published_at
        job["original_publication_verified"] = True
        results.append(job)

    return results


def mark_handed_off(path, job):
    """Atomically mark one verified candidate as transferred."""
    identity = _identity(job)
    timestamp = datetime.now(timezone.utc).isoformat()

    with closing(_connect(path)) as connection:
        with connection:
            _initialize(connection)

            cursor = connection.execute("""
                INSERT OR IGNORE INTO greenhouse_handoffs (
                    source, company, source_id, handed_off_at
                )
                SELECT source, company, source_id, ?
                FROM candidates
                WHERE source = ?
                  AND company = ?
                  AND source_id = ?
                  AND status = 'verified'
                  AND verified_published_at IS NOT NULL
            """, (timestamp, *identity))

            return cursor.rowcount == 1
