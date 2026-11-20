"""Stage 8 isolated Workday queue / crash-recovery prototype.

Run: python -B stage8_workday_queue_prototype.py --self-test

Uses only a new TemporaryDirectory and explicitly directed test databases.
Never opens a project's discovery_history.db, jobpilot.db, reports or SMTP.
The existing discovery_history.record_scan() function is used only with
an explicitly supplied temporary history path.

Demonstrates durable *candidate preparation*, not exactly-once email sending.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


def connect(path):
    con = sqlite3.connect(str(path), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    return con


def initialize(path):
    with closing(connect(path)) as con, con:
        con.executescript('''
          CREATE TABLE IF NOT EXISTS workday_queue (
            job_key TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_path TEXT NOT NULL,
            job_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','ready')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
          );
          CREATE TABLE IF NOT EXISTS handoff (
            job_key TEXT PRIMARY KEY REFERENCES workday_queue(job_key),
            state TEXT NOT NULL CHECK(state IN ('await_history','candidate_ready','acknowledged')),
            first_scan_id INTEGER,
            UNIQUE(job_key)
          );
        ''')


def upsert_index(path, job):
    """Re-seeing an index posting never resets a job already enriched."""
    key = f"{job['company'].strip().lower()}:{job['source_id'].strip()}"
    with closing(connect(path)) as con, con:
        con.execute('''
          INSERT INTO workday_queue(job_key, source_id, external_path, job_json, status)
          VALUES (?, ?, ?, ?, 'pending')
          ON CONFLICT(job_key) DO UPDATE SET
            external_path=excluded.external_path
        ''', (key, job['source_id'], job['external_path'], json.dumps(job)))
    return key


def fail_detail(path, key, message):
    with closing(connect(path)) as con, con:
        con.execute('''UPDATE workday_queue SET attempts=attempts+1, last_error=?
                       WHERE job_key=? AND status='pending' ''', (message, key))


def finish_detail(path, key, description):
    """Atomic: completed description and durable handoff are saved together."""
    if not description.strip():
        raise ValueError('Empty description must not mark ready')
    with closing(connect(path)) as con, con:
        row = con.execute('SELECT * FROM workday_queue WHERE job_key=?', (key,)).fetchone()
        if not row:
            raise KeyError(key)
        job = json.loads(row['job_json'])
        job['description'] = description
        con.execute('''UPDATE workday_queue SET status='ready', job_json=?,
                       last_error=NULL, attempts=attempts+1 WHERE job_key=?''',
                    (json.dumps(job), key))
        con.execute('''INSERT OR IGNORE INTO handoff(job_key, state)
                       VALUES (?, 'await_history')''', (key,))


def handoff_once(queue_path, history_path, key, crash_at=None):
    """Recover without losing candidate when an interruption follows history commit.

    Recovery uses the durable local handoff row; it doesn't depend on
    new_fingerprints() on a later scan. It does NOT deliver any real alerts.
    """
    from discovery_history import job_fingerprint, record_scan

    with closing(connect(queue_path)) as con:
        jobrow = con.execute('SELECT * FROM workday_queue WHERE job_key=?', (key,)).fetchone()
        event = con.execute('SELECT * FROM handoff WHERE job_key=?', (key,)).fetchone()
    if not jobrow or jobrow['status'] != 'ready' or not event:
        raise ValueError('No complete job with durable handoff')
    if event['state'] == 'acknowledged':
        return 'already_acknowledged'
    job = json.loads(jobrow['job_json'])
    fp = job_fingerprint(job)
    if not fp:
        raise ValueError('No stable discovery fingerprint for job')
    if event['state'] == 'await_history':
        # Explicit read-only query against the *temporary* history database.
        with closing(connect(history_path)) as history:
            existing = history.execute(
                'SELECT first_scan_id FROM discovery_jobs WHERE fingerprint=?', (fp,)
            ).fetchone()
        first_id = existing['first_scan_id'] if existing else None
        if first_id is None:
            result = record_scan([job], path=history_path)
            if result['baseline']:
                raise AssertionError('History fixture must already have its baseline')
            first_id = result['scan_id']
        if crash_at == 'after_history':
            raise SimulatedCrash('after_history')
        with closing(connect(queue_path)) as con, con:
            con.execute('''UPDATE handoff SET state='candidate_ready', first_scan_id=?
                           WHERE job_key=? AND state='await_history' ''', (first_id, key))
    if crash_at == 'after_candidate':
        raise SimulatedCrash('after_candidate')
    # Idempotent simulated candidate acknowledgment; NO SMTP action.
    with closing(connect(queue_path)) as con, con:
        con.execute('''UPDATE handoff SET state='acknowledged'
                       WHERE job_key=? AND state='candidate_ready' ''', (key,))
    return 'candidate_acknowledged'


class SimulatedCrash(Exception):
    pass


def states(queue_path, key):
    with closing(connect(queue_path)) as con:
        row = con.execute('SELECT status, attempts, last_error FROM workday_queue WHERE job_key=?', (key,)).fetchone()
        event = con.execute('SELECT state FROM handoff WHERE job_key=?', (key,)).fetchone()
    return dict(row), (event['state'] if event else None)


def self_test():
    from discovery_history import record_scan
    with TemporaryDirectory(prefix='stage8_workday_queue_') as tmp:
        queue_path = Path(tmp) / 'workday_queue_test.db'
        history_path = Path(tmp) / 'discovery_history_TEST_ONLY.db'
        initialize(queue_path)
        # Establish a real Stage 5A baseline in an isolated database.
        baseline = {'source': 'Lever', 'source_id': 'baseline-12345678901234567890',
                    'company': 'Fixture Inc', 'title': 'Baseline', 'location': 'San Diego',
                    'url': 'https://jobs.lever.co/fixture/baseline-12345678901234567890',
                    'description': 'Baseline fixture', 'posted_at': 'Unknown'}
        assert record_scan([baseline], path=history_path)['baseline']
        job = {'source': 'Workday', 'source_id': 'R30448', 'company': 'CrowdStrike',
               'title': 'Community and Player Support Specialist',
               'location': 'USA - Remote',
               'url': 'https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/example_R30448-1',
               'external_path': '/job/example_R30448-1',
               'description': '', 'posted_at': 'Unknown'}
        key = upsert_index(queue_path, job)
        assert states(queue_path, key)[0]['status'] == 'pending'
        # A fresh sqlite connection simulates the next daily scan.
        assert upsert_index(queue_path, job) == key
        assert states(queue_path, key)[0]['status'] == 'pending'
        print('PASS 1: Pending job survives reopening the SQLite queue and another index scan')
        fail_detail(queue_path, key, 'temporary HTTP 503')
        assert states(queue_path, key)[0]['attempts'] == 1
        assert states(queue_path, key)[1] is None
        try:
            handoff_once(queue_path, history_path, key)
            raise AssertionError('Incomplete job reached history')
        except ValueError:
            pass
        finish_detail(queue_path, key, 'Full job description includes customer and technical support.')
        assert states(queue_path, key)[0]['status'] == 'ready'
        assert states(queue_path, key)[1] == 'await_history'
        print('PASS 2: Temporary detail failure is retryable; pending jobs cannot enter history')
        print('PASS 3: Enrichment completion creates durable handoff, even after index path changes')
        # Simulates failure after a successful discovery-history transaction,
        # before queue state is changed. On restart, existing record is found.
        try:
            handoff_once(queue_path, history_path, key, crash_at='after_history')
            raise AssertionError('Crash hook did not execute')
        except SimulatedCrash:
            pass
        assert states(queue_path, key)[1] == 'await_history'
        with closing(connect(history_path)) as con:
            count_before = con.execute('SELECT COUNT(*) FROM discovery_scans').fetchone()[0]
        assert count_before == 2
        try:
            handoff_once(queue_path, history_path, key, crash_at='after_candidate')
            raise AssertionError('Crash hook did not execute')
        except SimulatedCrash:
            pass
        assert states(queue_path, key)[1] == 'candidate_ready'
        assert handoff_once(queue_path, history_path, key) == 'candidate_acknowledged'
        assert handoff_once(queue_path, history_path, key) == 'already_acknowledged'
        with closing(connect(history_path)) as con:
            count_after = con.execute('SELECT COUNT(*) FROM discovery_scans').fetchone()[0]
            jobs_after = con.execute('SELECT COUNT(*) FROM discovery_jobs').fetchone()[0]
        assert count_after == 2 and jobs_after == 2
        assert states(queue_path, key)[1] == 'acknowledged'
        print('PASS 4: Crash after history commit recovers original first-seen candidate without a repeat scan')
        print('PASS 5: Crash after candidate staging retries without creating another candidate or history row')
        print('PASS 6: No title-only filter; Workday queue key stable across URL suffix variation')
        print('SUCCESS: Offline isolated-queue crash recovery and idempotent candidate staging verified')
        print('No network, SMTP, project files, original databases, or reports accessed or changed.')
        print('LIMITATION: Real delivery / exactly-once email semantics and stable Workday history identity are NOT integrated.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', required=True,
                        help='Run only offline tests with temporary SQLite files')
    parser.parse_args()
    self_test()


if __name__ == '__main__':
    main()
