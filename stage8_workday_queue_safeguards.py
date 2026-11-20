"""Stage 8 Workday queue safeguards, OFFLINE standalone testing version.

Run: python -B stage8_workday_queue_safeguards.py --self-test
No network. Writes only to TemporaryDirectory. Does not alter prior prototype.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

MAX_ATTEMPTS = 4
BACKOFF_MINUTES = (15, 60, 240)
MIN_DESCRIPTION_CHARS = 100


def when(t):
    return t.astimezone(timezone.utc).isoformat()


def connect(path):
    con = sqlite3.connect(str(path), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def initialize(path):
    with closing(connect(path)) as con, con:
        con.executescript("""
        CREATE TABLE queue (
            job_key TEXT PRIMARY KEY, job_json TEXT NOT NULL,
            external_path TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','ready','stalled')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, next_retry_at TEXT,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE handoff (
            job_key TEXT PRIMARY KEY REFERENCES queue(job_key),
            state TEXT NOT NULL CHECK(state IN ('await_history','candidate_ready','acknowledged')),
            first_scan_id INTEGER
        );
        """)


def add_index(path, job, now):
    # Identity is a strict prerequisite at integration; this prototype tests lifecycle only.
    key = f"{job['company'].casefold()}:{job['source_id']}"
    with closing(connect(path)) as con, con:
        con.execute("""INSERT INTO queue(job_key,job_json,external_path,status,first_seen_at,last_seen_at)
                       VALUES (?,?,?,'pending',?,?)
                       ON CONFLICT(job_key) DO UPDATE SET
                          last_seen_at=excluded.last_seen_at,
                          external_path=excluded.external_path""",
                    (key, json.dumps(job), job['external_path'], when(now), when(now)))
    return key


def get(path, key):
    with closing(connect(path)) as con:
        row = con.execute('SELECT * FROM queue WHERE job_key=?', (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return dict(row)


def due(row, now):
    return (row['status'] == 'pending' and row['attempts'] < MAX_ATTEMPTS and
            (row['next_retry_at'] is None or datetime.fromisoformat(row['next_retry_at']) <= now))


def fail(path, key, error, now):
    with closing(connect(path)) as con, con:
        row = con.execute('SELECT * FROM queue WHERE job_key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        if not due(row, now):
            return False
        count = row['attempts'] + 1
        status = 'stalled' if count >= MAX_ATTEMPTS else 'pending'
        next_retry = None if status == 'stalled' else when(now + timedelta(minutes=BACKOFF_MINUTES[count-1]))
        con.execute('UPDATE queue SET attempts=?,status=?,last_error=?,next_retry_at=? WHERE job_key=?',
                    (count,status,str(error)[:500],next_retry,key))
    return True


def meaningful(description):
    if not isinstance(description, str):
        return False
    cleaned = re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', description)).strip()
    return (len(cleaned) >= MIN_DESCRIPTION_CHARS and
            bool(re.search(r'\b(responsibilities|requirements|qualifications|experience|skills|duties|you will)\b', cleaned, re.I)))


def valid_job(job, description):
    if job.get('source') != 'Workday':
        return False
    if not all(isinstance(job.get(k),str) and job[k].strip() for k in
               ('company','source_id','title','location','url','external_path')):
        return False
    url = urlparse(job['url'])
    return (url.scheme == 'https' and url.hostname is not None and
            url.hostname.endswith('.myworkdayjobs.com') and meaningful(description))


def enrich(path, key, description):
    """Atomic description completion and queue handoff; invalid data remains pending."""
    with closing(connect(path)) as con, con:
        row = con.execute('SELECT * FROM queue WHERE job_key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        if row['status'] != 'pending':
            return False
        job = json.loads(row['job_json'])
        if not valid_job(job, description):
            raise ValueError('Incomplete record: must not be scored')
        job['description'] = description
        con.execute('UPDATE queue SET status=\'ready\',job_json=?,last_error=NULL,next_retry_at=NULL WHERE job_key=?',
                    (json.dumps(job),key))
        con.execute('INSERT OR IGNORE INTO handoff(job_key,state) VALUES (?,\'await_history\')',(key,))
    return True


def overview(path, now, stale_days=7):
    summary = {'pending':0,'ready':0,'stalled':0,'stale_pending':0,'oldest_pending_days':0.0}
    with closing(connect(path)) as con:
        rows = con.execute('SELECT status,first_seen_at FROM queue').fetchall()
    for row in rows:
        summary[row['status']] += 1
        if row['status'] == 'pending':
            age = max(0.0, (now-datetime.fromisoformat(row['first_seen_at'])).total_seconds()/86400)
            summary['oldest_pending_days'] = max(age,summary['oldest_pending_days'])
            summary['stale_pending'] += int(age>=stale_days)
    summary['oldest_pending_days'] = round(summary['oldest_pending_days'],2)
    return summary


def self_test():
    now = datetime(2026,11,3,12,tzinfo=timezone.utc)
    job = {'source':'Workday','source_id':'R30448','company':'CrowdStrike',
           'title':'Community and Player Support Specialist','location':'USA - Remote',
           'url':'https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/example_R30448-1',
           'external_path':'/job/example_R30448-1','description':'','posted_at':'Unknown'}
    full = ('Responsibilities include investigating customer support tickets, documenting '
            'technical troubleshooting steps, escalating issues to engineering, and providing '
            'timely customer assistance. Qualifications include clear written communication '
            'and experience supporting software users and troubleshooting application problems.')
    with TemporaryDirectory(prefix='stage8_queue_safeguards_') as tmp:
        path=Path(tmp)/'workday_queue_TEST_ONLY.sqlite'
        initialize(path)
        key=add_index(path,job,now-timedelta(days=8))
        assert add_index(path,{**job,'external_path':'/job/updated_R30448'},now)==key
        assert get(path,key)['status']=='pending'
        assert overview(path,now)['stale_pending']==1
        assert overview(path,now)['oldest_pending_days']==8
        print('PASS 1: Pending survives reindex; age and stalled visibility can be measured')
        assert fail(path,key,'503 temporary',now)
        assert get(path,key)['attempts']==1
        assert not due(get(path,key),now+timedelta(minutes=1))
        assert not fail(path,key,'too soon',now+timedelta(minutes=1))
        assert due(get(path,key),now+timedelta(minutes=15))
        print('PASS 2: Temporary error schedules bounded backoff; early retries blocked')
        for bad in ('','Loading...', '<p>Description unavailable</p>', 'Placeholder filler ' * 12):
            try:
                enrich(path,key,bad)
                raise AssertionError('Bad description accepted')
            except ValueError:
                pass
        assert get(path,key)['status']=='pending'
        with closing(connect(path)) as con:
            assert con.execute('SELECT COUNT(*) FROM handoff').fetchone()[0]==0
        print('PASS 3: Missing or meaningless descriptions never become score-ready')
        assert enrich(path,key,full)
        assert get(path,key)['status']=='ready'
        with closing(connect(path)) as con:
            assert con.execute('SELECT state FROM handoff WHERE job_key=?',(key,)).fetchone()[0]=='await_history'
        assert not enrich(path,key,full)
        print('PASS 4: Valid description and durable handoff committed together, only once')
        other={**job,'source_id':'R99999','external_path':'/job/other_R99999'}
        otherkey=add_index(path,other,now)
        t=now
        for i in range(MAX_ATTEMPTS):
            assert fail(path,otherkey,'temporary 503',t)
            if i<MAX_ATTEMPTS-1:
                t+=timedelta(minutes=BACKOFF_MINUTES[i])
        assert get(path,otherkey)['status']=='stalled'
        assert not due(get(path,otherkey),t+timedelta(days=100))
        assert not fail(path,otherkey,'again',t+timedelta(days=100))
        add_index(path,other,t+timedelta(days=101))
        assert get(path,otherkey)['status']=='stalled'
        assert overview(path,t+timedelta(days=101))['stalled']==1
        print('PASS 5: Exhausted jobs remain visible as stalled; no endless retries or resets')
    print('SUCCESS: Offline lifecycle safeguards passed; temporary SQLite only, no network/email/history writes.')
    print('NOT YET TESTED: trusted tenant identity enforcement, changed posting disappearance handling, and actual discovery/email handoff.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-test',action='store_true',required=True)
    parser.parse_args()
    self_test()
