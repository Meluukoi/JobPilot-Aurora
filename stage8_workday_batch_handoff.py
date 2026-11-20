"""Workday batch handoff preparation and recovery.

Testing-copy integration component. No network or SMTP operations.
The caller must use a single scanner writer; cross-process concurrency
and email delivery acknowledgment are not implemented here.
"""

from contextlib import closing
import json
import sqlite3
from pathlib import Path

from discovery_history import job_fingerprint
from stage8_workday_adapter_queue_bridge import (
    open_db,
    ready_for_history,
)


def _history_connection(history_path):
    path = Path(history_path)
    if not path.is_file():
        raise FileNotFoundError(f"History database missing: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _history_row(history_path, fingerprint):
    with closing(_history_connection(history_path)) as con:
        return con.execute(
            "SELECT first_scan_id FROM discovery_jobs WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()


def prepare_batch(queue_path, history_path):
    """Persist history intent before normal batch discovery recording.

    Returns ready Workday job dictionaries for inclusion in the
    existing scanner's collection. Does not write discovery history.
    """
    ready = ready_for_history(queue_path)
    prepared = []

    for item in ready:
        job = item["job"]
        fingerprint = job_fingerprint(job)

        if not fingerprint:
            raise ValueError("Untrusted Workday discovery fingerprint")

        existing = _history_row(history_path, fingerprint)

        prepared.append((
            item["identity"],
            fingerprint,
            job,
            existing["first_scan_id"] if existing else None,
        ))

    with closing(open_db(queue_path)) as con:
        with con:
            for key, fingerprint, job, previous_scan_id in prepared:
                prior = con.execute(
                    "SELECT fingerprint FROM history_intents WHERE identity=?",
                    (key,),
                ).fetchone()

                if prior is not None:
                    if prior["fingerprint"] != fingerprint:
                        raise ValueError(
                            "Workday fingerprint changed after durable intent"
                        )
                    continue

                con.execute(
                    """INSERT INTO history_intents
                       (identity, fingerprint, was_already_present, first_scan_id)
                       VALUES (?, ?, ?, ?)""",
                    (
                        key,
                        fingerprint,
                        int(previous_scan_id is not None),
                        previous_scan_id,
                    ),
                )

    return [job for _, _, job, _ in prepared]


def finalize_batch(queue_path, history_path):
    """Recover and stage completed handoffs without sending alerts.

    Jobs whose history record is still missing remain awaiting history.
    Acknowledged means staged locally, not emailed or delivered.
    """
    with closing(open_db(queue_path)) as con:
        rows = con.execute(
            """SELECT h.identity, i.fingerprint, i.was_already_present,
                      j.job_json
               FROM handoffs AS h
               JOIN history_intents AS i ON i.identity=h.identity
               JOIN jobs AS j ON j.identity=h.identity
               WHERE h.state IN ('await_history','candidate_ready')
                 AND j.state='ready'
               ORDER BY j.first_seen, h.identity"""
        ).fetchall()

    completed = 0
    waiting = 0
    staged = 0

    for row in rows:
        job = json.loads(row["job_json"])
        fingerprint = job_fingerprint(job)

        if fingerprint != row["fingerprint"]:
            raise ValueError("Workday handoff identity changed")

        found = _history_row(history_path, fingerprint)

        if found is None:
            waiting += 1
            continue

        scan_id = found["first_scan_id"]

        with closing(open_db(queue_path)) as con:
            with con:
                if not row["was_already_present"]:
                    result = con.execute(
                        """INSERT OR IGNORE INTO review_outbox
                           (identity, fingerprint, job_json)
                           VALUES (?, ?, ?)""",
                        (row["identity"], fingerprint, json.dumps(job)),
                    )
                    staged += max(0, result.rowcount)

                con.execute(
                    """UPDATE history_intents SET first_scan_id=?
                       WHERE identity=?""",
                    (scan_id, row["identity"]),
                )
                con.execute(
                    """UPDATE handoffs SET state='acknowledged'
                       WHERE identity=?""",
                    (row["identity"],),
                )

        completed += 1

    return {
        "completed": completed,
        "waiting_for_history": waiting,
        "new_review_records_staged": staged,
    }
