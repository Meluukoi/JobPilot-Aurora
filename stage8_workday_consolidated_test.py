"""Stage 8 isolated Workday queue + history acceptance test.

Run: python -B stage8_workday_consolidated_test.py
Offline only. ALL SQLite paths are in TemporaryDirectory; no HTTP, email,
project databases, production collectors, or configuration are changed.
Uses discovery_history_stage8_preview for Workday-only fingerprints, plus the
existing scoring and review modules. No production integration is performed.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from stage8_workday_identity import workday_key
from stage8_workday_queue_safeguards import meaningful, MAX_ATTEMPTS, BACKOFF_MINUTES
from discovery_history_stage8_preview import job_fingerprint, record_scan, new_fingerprints
from scorer import score_job
from opportunity_review import build_review_reports
import scheduled_scan
import opportunity_review
from scheduled_scan import select_relevant_first_seen
from alert_policy import email_eligible


def stamp(t):
    return t.astimezone(timezone.utc).isoformat()


def db(path):
    c = sqlite3.connect(str(path), timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init(path):
    with closing(db(path)) as c, c:
        c.executescript('''
        CREATE TABLE q (
            job_key TEXT PRIMARY KEY, job_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','ready','stalled')),
            attempts INTEGER NOT NULL DEFAULT 0, next_retry TEXT, last_error TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            absence_count INTEGER NOT NULL DEFAULT 0, missing_flag INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE handoff (
            job_key TEXT PRIMARY KEY REFERENCES q(job_key),
            state TEXT NOT NULL CHECK(state IN ('await_history','candidate_ready','acknowledged')),
            first_scan_id INTEGER
        );
        ''')


def key_for(job):
    ident = workday_key(job)
    if ident is None:
        raise ValueError('Missing, mismatched, or untrusted Workday identity')
    return json.dumps(ident, separators=(',', ':'))


def add(path, job, now):
    key = key_for(job)
    with closing(db(path)) as c, c:
        c.execute('''INSERT INTO q(job_key,job_json,status,first_seen,last_seen)
            VALUES (?,?,'pending',?,?) ON CONFLICT(job_key) DO UPDATE SET
            last_seen=excluded.last_seen,absence_count=0,missing_flag=0,
            job_json=CASE WHEN q.status='pending' THEN excluded.job_json ELSE q.job_json END''',
            (key, json.dumps(job), stamp(now), stamp(now)))
    return key


def row(path, key):
    with closing(db(path)) as c:
        r = c.execute('SELECT * FROM q WHERE job_key=?', (key,)).fetchone()
    return dict(r) if r else None


def retry_due(r, now):
    return r['status']=='pending' and r['attempts'] < MAX_ATTEMPTS and (not r['next_retry'] or datetime.fromisoformat(r['next_retry']) <= now)


def failed(path, key, now, error):
    with closing(db(path)) as c, c:
        r = c.execute('SELECT * FROM q WHERE job_key=?', (key,)).fetchone()
        if r is None: raise KeyError(key)
        if not retry_due(r, now): return False
        attempts = r['attempts']+1
        status = 'stalled' if attempts>=MAX_ATTEMPTS else 'pending'
        next_retry = stamp(now+timedelta(minutes=BACKOFF_MINUTES[attempts-1])) if status=='pending' else None
        c.execute('UPDATE q SET attempts=?,status=?,next_retry=?,last_error=? WHERE job_key=?',
                  (attempts,status,next_retry,str(error)[:200],key))
    return True


def ready(path, key, desc):
    if not meaningful(desc): raise ValueError('Description not meaningful')
    with closing(db(path)) as c, c:
        r=c.execute('SELECT * FROM q WHERE job_key=?',(key,)).fetchone()
        if not r: raise KeyError(key)
        if r['status']!='pending': return False
        job=json.loads(r['job_json'])
        if key_for(job)!=key or not all(str(job.get(k,'')).strip() for k in ('title','location','url','source_id')):
            raise ValueError('Invalid full record')
        job['description']=desc
        c.execute('UPDATE q SET job_json=?,status=\'ready\',last_error=NULL,next_retry=NULL WHERE job_key=?',
                  (json.dumps(job),key))
        c.execute("INSERT OR IGNORE INTO handoff(job_key,state) VALUES (?,'await_history')",(key,))
    return True


def missing_from_complete_index(path, found_keys, is_complete):
    """Only complete-index scans can raise absence flags; never delete postings."""
    if not is_complete: return
    with closing(db(path)) as c, c:
        for r in c.execute('SELECT job_key FROM q').fetchall():
            if r['job_key'] not in found_keys:
                c.execute('UPDATE q SET absence_count=absence_count+1, missing_flag=CASE WHEN absence_count+1>=2 THEN 1 ELSE 0 END WHERE job_key=?',(r['job_key'],))


class SimulatedCrash(Exception): pass


def handoff(path, history, key, crash=None):
    with closing(db(path)) as c:
        r=c.execute('SELECT job_json,status FROM q WHERE job_key=?',(key,)).fetchone()
        h=c.execute('SELECT state,first_scan_id FROM handoff WHERE job_key=?',(key,)).fetchone()
    if r is None or r['status']!='ready' or h is None: raise ValueError('Not ready')
    if h['state']=='acknowledged': return 'already_acknowledged'
    job=json.loads(r['job_json']); fp=job_fingerprint(job)
    if fp is None: raise ValueError('No trusted fingerprint')
    if h['state']=='await_history':
        with closing(db(history)) as c:
            existing=c.execute('SELECT first_scan_id FROM discovery_jobs WHERE fingerprint=?',(fp,)).fetchone()
        scan_id = existing['first_scan_id'] if existing else None
        if scan_id is None:
            result=record_scan([job],path=history)
            assert not result['baseline']
            scan_id=result['scan_id']
        if crash=='after_history': raise SimulatedCrash('after_history')
        with closing(db(path)) as c, c:
            c.execute("UPDATE handoff SET state='candidate_ready',first_scan_id=? WHERE job_key=? AND state='await_history'",(scan_id,key))
    if crash=='after_stage': raise SimulatedCrash('after_stage')
    # Acknowledgment here means local simulated review staging only, never SMTP.
    with closing(db(path)) as c, c:
        c.execute("UPDATE handoff SET state='acknowledged' WHERE job_key=? AND state='candidate_ready'",(key,))
    return 'candidate_acknowledged'


def job(req,title='IT Support Technician',loc='San Diego, CA',suffix='',company='CrowdStrike'):
    p=f'/job/USA---Example-CA/{title.replace(" ","-")}_{req}{suffix}'
    return {'source':'Workday','company':company,'source_id':req,'title':title,'location':loc,
            'url':'https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers'+p,
            'external_path':p,'description':'','posted_at':'Unknown'}


def run():
    now=datetime(2026,11,3,19,tzinfo=timezone.utc)
    desc=('Responsibilities include troubleshooting customer support requests, documenting '
          'technical incidents, helping users resolve product issues, escalating complex '
          'problems to engineering, and tracking cases. Qualifications include excellent '
          'communication skills and experience with technical support processes.')
    with TemporaryDirectory(prefix='jobpilot_stage8_consolidated_') as tmp:
        q=Path(tmp)/'queue_TEMP.sqlite'; h=Path(tmp)/'history_TEMP.sqlite'; init(q)
        # Local baseline in preview history only, no actual project DB.
        baseline={'source':'Lever','company':'Fixture','source_id':'baseline-12345678901234567890',
                  'title':'Fixture','location':'San Diego','url':'https://jobs.lever.co/fixture/baseline-12345678901234567890',
                  'description':'Test fixture','posted_at':'Unknown'}
        assert record_scan([baseline],path=h)['baseline']
        j=job('R30448', 'Community Player Support Specialist',suffix='-1')
        k=add(q,j,now); assert row(q,k)['status']=='pending'
        assert add(q,job('R30448','IT Support Specialist'),now+timedelta(days=1))==k
        with closing(db(h)) as c: assert c.execute('SELECT COUNT(*) FROM discovery_jobs').fetchone()[0]==1
        assert key_for(job('R30448',suffix='-1'))==key_for(job('R30448',title='New Title'))
        assert key_for(job('R30449'))!=k
        try: add(q,job('R30448',company='Different Employer'),now)
        except ValueError: pass
        else: raise AssertionError('Untrusted employer accepted')
        print('PASS 1: Trusted Workday identity stable across title/path/suffix; untrusted employer blocked; pending outside history')
        assert failed(q,k,now+timedelta(days=1),'temporary HTTP 503')
        assert not retry_due(row(q,k),now+timedelta(days=1,minutes=1))
        assert retry_due(row(q,k),now+timedelta(days=1,minutes=15))
        try: ready(q,k,'Loading...')
        except ValueError: pass
        else: raise AssertionError('Invalid description accepted')
        assert row(q,k)['status']=='pending'
        assert ready(q,k,desc); assert row(q,k)['status']=='ready'
        print('PASS 2: Bounded retry, meaningful description, atomic ready + handoff')
        with closing(db(q)) as c: assert c.execute('SELECT state FROM handoff WHERE job_key=?',(k,)).fetchone()[0]=='await_history'
        try: handoff(q,h,k,crash='after_history')
        except SimulatedCrash: pass
        else: raise AssertionError('Crash not simulated')
        with closing(db(h)) as c: assert c.execute('SELECT COUNT(*) FROM discovery_jobs').fetchone()[0]==2
        try: handoff(q,h,k,crash='after_stage')
        except SimulatedCrash: pass
        else: raise AssertionError('Crash not simulated')
        assert handoff(q,h,k)=='candidate_acknowledged'
        assert handoff(q,h,k)=='already_acknowledged'
        with closing(db(h)) as c:
            assert c.execute('SELECT COUNT(*) FROM discovery_jobs').fetchone()[0]==2
            scan=c.execute('SELECT first_scan_id FROM discovery_jobs WHERE fingerprint=?',(job_fingerprint({**j,'description':desc}),)).fetchone()['first_scan_id']
        assert job_fingerprint(j)==job_fingerprint(job('R30448','Changed Again'))
        assert job_fingerprint({**j,'source_id':'R30449','url':j['url'].replace('R30448','R30449')})!=job_fingerprint(j)
        assert job_fingerprint(job('R30448',company='Different Employer')) is None
        assert job_fingerprint(j) in new_fingerprints(scan,path=h)
        print('PASS 3: Crash after history commit and candidate stage recovered; no duplicate records; stable history fingerprint')
        with closing(db(q)) as c: enr=json.loads(c.execute('SELECT job_json FROM q WHERE job_key=?',(k,)).fetchone()['job_json'])
        profile={'target_role_families':{'IT Support':['it support','technical support'], 'Customer Support':['customer support','client support']},
                 'skills':['troubleshooting','ticketing'], 'degree_status':'no_bachelors',
                 'candidate_profile':{'status':'approved_by_user_test_fixture', 'preferences':{'relocation':{'minimum_annual_salary_usd':75000}}}}
        # The pure screening builders import the legacy fingerprint at module load.
        # Patch only in this process to exercise the separate Workday identity preview.
        scheduled_scan.job_fingerprint = job_fingerprint
        opportunity_review.job_fingerprint = job_fingerprint
        score=score_job(enr,profile)
        selected=select_relevant_first_seen([(enr,profile)],{job_fingerprint(enr)},[],min_score=0,scorer=score_job)
        review,audit,counts=build_review_reports([enr],profile,[],{job_fingerprint(enr)},scorer=score_job)
        assert counts['collected']==1 and len(audit)==1 and selected
        assert score['geo_status']=='local' and email_eligible(selected[0])
        print('PASS 4: Ready record processed by existing scorer and review builders; local eligibility simulation only')
        # Missing-job logic must not interpret partial failed index as absence.
        missing_from_complete_index(q,set(),False); assert row(q,k)['absence_count']==0
        missing_from_complete_index(q,set(),True); assert row(q,k)['absence_count']==1 and row(q,k)['missing_flag']==0
        missing_from_complete_index(q,set(),True); assert row(q,k)['missing_flag']==1
        assert add(q,job('R30448'),now+timedelta(days=3))==k and row(q,k)['absence_count']==0
        print('PASS 5: Disappearance only flagged after two complete index snapshots, reversible on reappearance')
        other=add(q,job('R30377'),now)
        t=now
        for i in range(MAX_ATTEMPTS):
            assert failed(q,other,t,'503')
            if i<MAX_ATTEMPTS-1: t+=timedelta(minutes=BACKOFF_MINUTES[i])
        assert row(q,other)['status']=='stalled' and not retry_due(row(q,other),now+timedelta(days=30))
        age=(now+timedelta(days=8)-datetime.fromisoformat(row(q,other)['first_seen'])).days
        assert age==8
        print('PASS 6: Retry exhaustion stays visible and pending age measurable')
    print('SUCCESS: Consolidated Workday queue/identity/history preview passed, OFFLINE ONLY.')
    print('NOT IMPLEMENTED: production history integration, real delivery acknowledgment, recurring scheduler, or full cross-database transaction.')
    print('No network, SMTP, existing JobPilot database/report/source/config writes.')

if __name__=='__main__':
    run()
