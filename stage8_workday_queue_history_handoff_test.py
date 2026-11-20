"""Stage 8 multi-employer queue -> JobPilot history PREVIEW acceptance test.

Run: python -B stage8_workday_queue_history_handoff_test.py
Offline/synthetic only. All SQLite databases live inside TemporaryDirectory.
Does NOT import the live scheduler, touch project databases, send emails, or run HTTP.
This is a test harness, NOT an installed queue-to-history service.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3

import stage8_workday_identity as identity_module
from stage8_workday_multi_employer_adapter import EMPLOYERS, collect_preview
from stage8_workday_adapter_queue_bridge import (initialize, import_preview, get,
    identity, open_db)
from discovery_history_stage8_preview import job_fingerprint, record_scan, new_fingerprints

DESCRIPTION = ("Responsibilities include responding to technical support tickets, "
    "investigating application and connectivity problems, documenting incidents, "
    "escalating issues to engineering, and helping users with troubleshooting. "
    "Qualifications include customer service experience, good communication, "
    "technical skills, and familiarity with IT support workflows.")

class FakeClient:
    def __init__(self, req, title, suffix='-1'):
        self.req, self.title, self.suffix = req, title, suffix
    def get_json(self, url, payload=None):
        if payload is not None:
            return {'total': 1, 'jobPostings': [{
                'externalPath': f'/job/USA---Remote/{self.title.replace(" ", "-")}_{self.req}{self.suffix}',
                'title': self.title, 'locationsText': 'USA - Remote'}]}
        return {'jobPostingInfo': {'jobReqId': self.req, 'title': self.title,
            'location': 'USA - Remote', 'additionalLocations': ['San Diego, CA'],
            'jobDescription': DESCRIPTION}}

def make_preview(employer, req, title, detail):
    return collect_preview(EMPLOYERS[employer], FakeClient(req,title),
                           max_pages=1, max_details=int(detail))

def db(path):
    return open_db(path)

def init_handoff_audit(queue):
    # Extra tables exist ONLY in temporary test queue; no schema changes to user's files.
    with closing(db(queue)) as c, c:
        c.executescript('''
          CREATE TABLE history_intents (
            identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            was_already_present INTEGER NOT NULL CHECK(was_already_present IN (0,1)),
            first_scan_id INTEGER, FOREIGN KEY(identity) REFERENCES jobs(identity));
          CREATE TABLE review_outbox (
            identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
            job_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending_review',
            FOREIGN KEY(identity) REFERENCES jobs(identity));
        ''')

def history_row(path, fp):
    with closing(db(path)) as c:
        return c.execute('SELECT first_scan_id FROM discovery_jobs WHERE fingerprint=?', (fp,)).fetchone()

class SimulatedCrash(Exception):
    pass

def handoff(queue, history, key, crash=None):
    """Recovery preview with durable pre-history intent and local review outbox.

    Not concurrency safe across multiple worker processes or exactly-once SMTP.
    """
    with closing(db(queue)) as c:
        job_row = c.execute('SELECT state, job_json FROM jobs WHERE identity=?',(key,)).fetchone()
        event = c.execute('SELECT state FROM handoffs WHERE identity=?',(key,)).fetchone()
        existing_intent = c.execute('SELECT * FROM history_intents WHERE identity=?',(key,)).fetchone()
    if not job_row or job_row['state'] != 'ready' or not event:
        raise ValueError('Only fully enriched jobs may enter discovery history')
    if event['state'] == 'acknowledged':
        return 'already_acknowledged'
    job = json.loads(job_row['job_json'])
    fp = job_fingerprint(job)
    if fp is None:
        raise ValueError('Untrusted Workday fingerprint')
    if existing_intent is None:
        # Durable decision *before* history update: prevents older historical
        # records from being falsely classified as new on recovery.
        already = history_row(history, fp)
        with closing(db(queue)) as c, c:
            c.execute('''INSERT OR IGNORE INTO history_intents
              (identity,fingerprint,was_already_present,first_scan_id) VALUES (?,?,?,?)''',
              (key,fp,int(already is not None), already['first_scan_id'] if already else None))
        if crash == 'after_intent':
            raise SimulatedCrash('after_intent')
    with closing(db(queue)) as c:
        intent = c.execute('SELECT * FROM history_intents WHERE identity=?',(key,)).fetchone()
    if intent['fingerprint'] != fp:
        raise ValueError('Fingerprint changed after handoff intent')
    found = history_row(history, fp)
    scan_id = found['first_scan_id'] if found else None
    if scan_id is None:
        result = record_scan([job], path=history)
        if result['baseline']:
            raise AssertionError('Isolated baseline missing')
        scan_id = result['scan_id']
    if crash == 'after_history':
        raise SimulatedCrash('after_history')
    with closing(db(queue)) as c, c:
        if not intent['was_already_present']:
            c.execute('''INSERT OR IGNORE INTO review_outbox(identity,fingerprint,job_json)
                         VALUES (?,?,?)''',(key,fp,json.dumps(job)))
        c.execute('UPDATE history_intents SET first_scan_id=? WHERE identity=?',(scan_id,key))
        c.execute("UPDATE handoffs SET state='candidate_ready' WHERE identity=? AND state='await_history'",(key,))
    if crash == 'after_staging':
        raise SimulatedCrash('after_staging')
    # ACK only means staged locally for review, NEVER emailed or delivered.
    with closing(db(queue)) as c, c:
        c.execute("UPDATE handoffs SET state='acknowledged' WHERE identity=?",(key,))
    return 'review_staged' if not intent['was_already_present'] else 'already_seen_no_new_review'

def count(path, table):
    assert table in {'discovery_jobs','discovery_scans','review_outbox'}
    with closing(db(path)) as c:
        return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

def run():
    # Only within this process: preview fingerprint supports both previously
    # verified boards. Never mutate actual discovery_history.py or registry file.
    old_registry = identity_module.TRUSTED_BOARDS.copy()
    identity_module.TRUSTED_BOARDS.update({e_key: employer for e_key, employer in
        { (e.tenant.casefold(),e.board.casefold()): ''.join(x for x in e.company.casefold() if x.isalnum())
          for e in EMPLOYERS.values() }.items()})
    try:
        with TemporaryDirectory(prefix='jobpilot_stage8_queue_to_history_') as tmp:
            queue = Path(tmp)/'workday_queue_TEST.sqlite'
            history = Path(tmp)/'discovery_history_TEST.sqlite'
            initialize(queue); init_handoff_audit(queue)
            baseline = {'source':'Lever','company':'Fixture Inc',
                        'source_id':'baseline-12345678901234567890','title':'Fixture',
                        'location':'San Diego','url':'https://jobs.lever.co/fixture/baseline-12345678901234567890',
                        'description':'Fixture', 'posted_at':'Unknown'}
            assert record_scan([baseline],path=history)['baseline']
            now = datetime(2026,11,3,22,tzinfo=timezone.utc)
            # Two employers, identical requisition number, two separate queue keys.
            c_index = make_preview('crowdstrike','R30448','Community Player Support',False)
            b_index = make_preview('boozallen','R30448','Technical Support Analyst',False)
            import_preview(queue,c_index,now); import_preview(queue,b_index,now)
            ck = identity({'source':'Workday','company':'CrowdStrike','source_id':'R30448','url':c_index['index'][0]['url']})
            bk = identity({'source':'Workday','company':'Booz Allen Hamilton','source_id':'R30448','url':b_index['index'][0]['url']})
            assert ck != bk and count(history,'discovery_jobs')==1
            for key in (ck,bk):
                try: handoff(queue,history,key)
                except ValueError: pass
                else: raise AssertionError('Pending job entered history')
            print('PASS 1: Two employers isolated; index-only pending jobs excluded from history')
            # Enrich CrowdStrike; changed title and path must preserve queue key.
            c_ready = make_preview('crowdstrike','R30448','Renamed Support Specialist',True)
            import_preview(queue,c_ready,now)
            assert get(queue,ck)['state']=='ready'
            assert ck == identity(c_ready['index'][0]['job_record'])
            try: handoff(queue,history,ck,crash='after_intent')
            except SimulatedCrash: pass
            else: raise AssertionError('Intent crash missing')
            assert count(history,'discovery_jobs')==1
            try: handoff(queue,history,ck,crash='after_history')
            except SimulatedCrash: pass
            else: raise AssertionError('History crash missing')
            assert count(history,'discovery_jobs')==2 and count(queue,'review_outbox')==0
            assert handoff(queue,history,ck)=='review_staged'
            assert handoff(queue,history,ck)=='already_acknowledged'
            assert count(history,'discovery_jobs')==2 and count(queue,'review_outbox')==1
            print('PASS 2: Crash after intent/history recovers one first-seen review candidate')
            # Restart simulation: new calls each open fresh SQLite connections.
            import_preview(queue,c_ready,now)
            assert count(queue,'review_outbox')==1 and count(history,'discovery_scans')==2
            with closing(db(queue)) as c:
                assert c.execute('SELECT state FROM review_outbox').fetchone()[0]=='pending_review'
            print('PASS 3: Repeated scan and restart do not duplicate history or staged review')
            b_ready = make_preview('boozallen','R30448','Technical Support Analyst',True)
            import_preview(queue,b_ready,now)
            assert get(queue,bk)['state']=='ready'
            try: handoff(queue,history,bk,crash='after_staging')
            except SimulatedCrash: pass
            else: raise AssertionError('Staging crash missing')
            assert count(queue,'review_outbox')==2
            assert handoff(queue,history,bk)=='review_staged'
            assert count(history,'discovery_jobs')==3 and count(queue,'review_outbox')==2
            assert len({row['fingerprint'] for row in _outbox_rows(queue)})==2
            print('PASS 4: Same requisition at different employers stays distinct; crash after staging deduplicated')
            # Pre-existing Workday posting must not be re-alerted as newly seen.
            old_job = make_preview('crowdstrike','R99999','Existing Technician',True)['index'][0]['job_record']
            record_scan([old_job],path=history)
            existing_n=count(history,'discovery_jobs'); outbox_n=count(queue,'review_outbox')
            old_preview=make_preview('crowdstrike','R99999','Existing Technician',True)
            import_preview(queue,old_preview,now)
            old_key=identity(old_job)
            assert handoff(queue,history,old_key)=='already_seen_no_new_review'
            assert count(history,'discovery_jobs')==existing_n and count(queue,'review_outbox')==outbox_n
            print('PASS 5: Pre-existing history record never incorrectly creates a new review candidate')
            print('SUCCESS: Offline multi-employer queue-to-history handoff preview passed')
            print('No HTTP, SMTP, existing project databases, reports, scheduler, or source changes.')
            print('LIMITATIONS: Not installed in scheduled_scan; no real report or email delivery;')
            print('cross-database concurrency/transaction guarantees and alert acknowledgment remain unimplemented.')
    finally:
        identity_module.TRUSTED_BOARDS.clear()
        identity_module.TRUSTED_BOARDS.update(old_registry)

def _outbox_rows(path):
    with closing(db(path)) as c:
        return c.execute('SELECT fingerprint FROM review_outbox').fetchall()

if __name__=='__main__':
    run()
