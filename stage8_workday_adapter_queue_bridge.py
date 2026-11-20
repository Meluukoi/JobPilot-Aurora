"""Stage 8 offline-only Workday adapter -> SQLite queue bridge.

Never contacts the network or accesses any existing JobPilot database. Callers must
supply a disposable SQLite path and an already retrieved adapter preview.
No production collector, history, or scheduler integration is performed.
"""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import sqlite3
from stage8_workday_multi_employer_adapter import EMPLOYERS, meaningful_description
from stage8_workday_identity import workday_key
from stage8_workday_queue_safeguards import MAX_ATTEMPTS, BACKOFF_MINUTES

REGISTRY = {(e.tenant.casefold(), e.board.casefold()): ''.join(c for c in e.company.casefold() if c.isalnum()) for e in EMPLOYERS.values()}


def utcstamp(t):
    return t.astimezone(timezone.utc).isoformat()


def identity(job):
    key = workday_key(job, REGISTRY)
    if key is None:
        raise ValueError('Untrusted or inconsistent Workday identity')
    return json.dumps(key, separators=(',', ':'))


def open_db(path):
    con = sqlite3.connect(str(path), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def initialize(path):
    with closing(open_db(path)) as con, con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
            identity TEXT PRIMARY KEY, employer TEXT NOT NULL, job_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','ready','stalled')),
            attempts INTEGER NOT NULL DEFAULT 0, next_retry TEXT, last_error TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            missing_count INTEGER NOT NULL DEFAULT 0, missing_flag INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS handoffs (
            identity TEXT PRIMARY KEY REFERENCES jobs(identity),
            state TEXT NOT NULL CHECK(state IN ('await_history','candidate_ready','acknowledged'))
        );''')


def get(path, key):
    with closing(open_db(path)) as con:
        row = con.execute('SELECT * FROM jobs WHERE identity=?', (key,)).fetchone()
    return dict(row) if row else None


def counts(path):
    with closing(open_db(path)) as con:
        return {r['state']: r['n'] for r in con.execute('SELECT state,COUNT(*) n FROM jobs GROUP BY state')}


def import_preview(path, preview, now):
    """Persist index entries; no title filters; ready requires validated descriptions.

    Incomplete preview never establishes absence. One snapshot per employer only.
    Untrusted/colliding identities fail closed; no silent merges.
    """
    slug = preview['employer']
    if slug not in EMPLOYERS:
        raise ValueError('Employer not approved')
    company = EMPLOYERS[slug].company
    if not isinstance(preview.get('index'), list):
        raise ValueError('Malformed index')
    prepared = []
    keys = set()
    for entry in preview['index']:
        if entry.get('identity') is None or entry.get('error') and 'collision' in str(entry['error']).lower():
            raise ValueError('Untrusted or colliding identity in index')
        draft = {'source':'Workday','company':company,'source_id':entry['source_id'],
                 'title':entry['title'],'location':entry['location_summary'],
                 'url':entry['url'],'description':'','posted_at':'Unknown'}
        key = identity(draft)
        if key in keys:
            raise ValueError('Duplicate requisition in same index')
        keys.add(key)
        full = entry.get('job_record')
        if full is not None:
            if identity(full) != key or not meaningful_description(full.get('description','')) or not all(full.get(k) for k in ('title','location','url','source_id')):
                raise ValueError('Invalid ready job')
            prepared.append((key, full, True))
        else:
            prepared.append((key, draft, False))
    stamp = utcstamp(now)
    with closing(open_db(path)) as con, con:
        for key, record, ready in prepared:
            existing = con.execute('SELECT state FROM jobs WHERE identity=?', (key,)).fetchone()
            if existing is None:
                con.execute('''INSERT INTO jobs(identity,employer,job_json,state,first_seen,last_seen)
                               VALUES (?,?,?,?,?,?)''', (key, slug, json.dumps(record), 'ready' if ready else 'pending', stamp, stamp))
                if ready:
                    con.execute("INSERT INTO handoffs(identity,state) VALUES (?,'await_history')", (key,))
            elif existing['state'] == 'pending' and ready:
                con.execute('''UPDATE jobs SET job_json=?,state='ready',last_seen=?,last_error=NULL,next_retry=NULL,
                               missing_count=0,missing_flag=0 WHERE identity=?''', (json.dumps(record), stamp, key))
                con.execute("INSERT OR IGNORE INTO handoffs(identity,state) VALUES (?,'await_history')", (key,))
            else:
                # Preserve retries, stalled status and ready/handoff state on subsequent index scans.
                con.execute('UPDATE jobs SET last_seen=?,missing_count=0,missing_flag=0 WHERE identity=?', (stamp, key))
        if preview.get('index_complete') is True:
            # Employer scoped: a complete Booz scan cannot mark CrowdStrike jobs missing.
            for row in con.execute('SELECT identity FROM jobs WHERE employer=?', (slug,)).fetchall():
                if row['identity'] not in keys:
                    con.execute('''UPDATE jobs SET missing_count=missing_count+1,
                        missing_flag=CASE WHEN missing_count+1>=2 THEN 1 ELSE 0 END
                        WHERE identity=?''',(row['identity'],))
    return {'employer':slug,'indexed':len(prepared),'ready_in_preview':sum(ready for _,_,ready in prepared),
            'index_complete': preview.get('index_complete') is True}


def fail_enrichment(path,key,now,message):
    with closing(open_db(path)) as con, con:
        row=con.execute('SELECT state,attempts,next_retry FROM jobs WHERE identity=?',(key,)).fetchone()
        if row is None: raise KeyError(key)
        if row['state']!='pending' or row['attempts']>=MAX_ATTEMPTS or (row['next_retry'] and datetime.fromisoformat(row['next_retry'])>now):
            return False
        attempts=row['attempts']+1
        stalled=attempts>=MAX_ATTEMPTS
        retry=None if stalled else utcstamp(now+timedelta(minutes=BACKOFF_MINUTES[attempts-1]))
        con.execute('UPDATE jobs SET attempts=?,state=?,next_retry=?,last_error=? WHERE identity=?',
                    (attempts,'stalled' if stalled else 'pending',retry,str(message)[:250],key))
    return True


def pending_age(path, now, min_days=7):
    with closing(open_db(path)) as con:
        rows=con.execute("SELECT first_seen FROM jobs WHERE state='pending'").fetchall()
    return sum((now-datetime.fromisoformat(row['first_seen'])).total_seconds()>=min_days*86400 for row in rows)


def ready_for_history(path):
    """Read validated Workday jobs awaiting discovery-history handoff.

    Read-only. Pending, stalled, and acknowledged records are excluded.
    The caller is responsible for durable handoff and acknowledgment.
    """
    with closing(open_db(path)) as con:
        rows = con.execute("""
            SELECT j.identity, j.job_json
            FROM jobs AS j
            JOIN handoffs AS h ON h.identity = j.identity
            WHERE j.state = 'ready'
              AND h.state = 'await_history'
            ORDER BY j.first_seen, j.identity
        """).fetchall()

    result = []

    for row in rows:
        job = json.loads(row["job_json"])

        if identity(job) != row["identity"]:
            raise ValueError("Queue identity mismatch")

        if not meaningful_description(job.get("description", "")):
            raise ValueError("Queue contains incomplete ready job")

        if not all(job.get(k) for k in (
            "source_id", "company", "title", "location", "url"
        )):
            raise ValueError("Queue contains incomplete job fields")

        result.append({
            "identity": row["identity"],
            "job": job,
        })

    return result


def initialize_batch_handoff(path):
    """Initialize durable Workday history intents and review outbox.

    Operates only on the explicitly supplied queue database.
    Does not modify discovery history or deliver notifications.
    Safe to invoke repeatedly.
    """
    with closing(open_db(path)) as con:
        with con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS history_intents (
                    identity TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    was_already_present INTEGER NOT NULL
                        CHECK(was_already_present IN (0,1)),
                    first_scan_id INTEGER,
                    FOREIGN KEY(identity) REFERENCES jobs(identity)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS review_outbox (
                    identity TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    job_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending_review',
                    FOREIGN KEY(identity) REFERENCES jobs(identity)
                )
            """)


def due_for_enrichment(path, entry, now):
    """Check whether an indexed Workday job may receive detail enrichment.

    Read-only. Does not reset retries, update timestamps, or create jobs.
    """
    key = entry.get("identity")
    if not key:
        return False

    with closing(open_db(path)) as con:
        row = con.execute(
            "SELECT state, attempts, next_retry FROM jobs WHERE identity=?",
            (json.dumps(key, separators=(",", ":")),),
        ).fetchone()

    if row is None:
        return True

    if row["state"] != "pending" or row["attempts"] >= MAX_ATTEMPTS:
        return False

    if row["next_retry"]:
        return datetime.fromisoformat(row["next_retry"]) <= now

    return True
