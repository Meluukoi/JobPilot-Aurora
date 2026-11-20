"""Stage 8: opt-in Workday baseline-aware preview, isolated TESTING ONLY (not scheduled).

Uses ONLY a manually copied ./stage8_workday_priority_workspace/{queue,history,reports} for --live.
Does not import a notifier or call JobPilot's installed scan runner.
Designed for one local Windows process, not concurrent workers or production.

Offline acceptance: python -B stage8_workday_live_runner_baseline_isolated.py --self-test
Explicit live call: python -B stage8_workday_live_runner_baseline_isolated.py --live
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import stage8_workday_identity as identity_module
from stage8_workday_multi_employer_adapter import (
    EMPLOYERS, PublicClient, PAGE_SIZE, req_from_path, safe_path, clean_html,
    meaningful_description,
)
from stage8_workday_adapter_queue_bridge import (
    initialize, import_preview, identity, open_db, get, REGISTRY,
)
from stage8_workday_queue_history_handoff_test import init_handoff_audit, handoff
from discovery_history_stage8_preview import record_scan, job_fingerprint
from scorer import score_job
from opportunity_review import build_review_reports
from alert_policy import email_eligible
from scheduled_scan import load_settings, load_saved_jobs_read_only, load_historical_warnings_read_only

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / 'stage8_workday_priority_workspace'
ORIGINAL_WORKSPACE = ROOT / 'stage8_workday_live_workspace'
# CrowdStrike: 25 pages covers ~400; Booz: strictly limited sample, NOT full coverage.
LIMITS = {'crowdstrike': (25, 5), 'boozallen': (5, 5)}
MAX_TOTAL_REQUESTS = sum(p + d for p, d in LIMITS.values())

class FileLock:
    def __init__(self, target): self.target = target; self.fd = None
    def __enter__(self):
        try:
            self.fd = os.open(str(self.target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(self.fd, str(os.getpid()).encode())
        except FileExistsError:
            raise RuntimeError(f'Another run may be active: {self.target}. Do not delete a live lock.')
        return self
    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.target.unlink()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)

def index_only(employer, client, max_pages):
    """Fetch bounded Workday pages; do not title-filter or discard pending records."""
    entries = []
    seen_paths = set()
    unique_ids = set()
    collisions = set()
    warnings = []
    total = None
    short = False
    for page in range(max_pages):
        response = client.get_json(employer.api + '/jobs', {
            'appliedFacets': {}, 'limit': PAGE_SIZE, 'offset': page * PAGE_SIZE, 'searchText': ''})
        rows = response.get('jobPostings')
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError(f'Invalid Workday page for {employer.slug} offset={page*PAGE_SIZE}')
        if page == 0:
            total = response.get('total')
            if type(total) is not int or total < 0:
                raise ValueError('Missing/invalid first-page total')
        elif response.get('total') != total and 'Later-page total changed' not in warnings:
            warnings.append('Later-page total changed')
        for row in rows:
            if not isinstance(row, dict): raise ValueError('Invalid listing')
            path = row.get('externalPath')
            if not safe_path(path) or path in seen_paths:
                raise ValueError('Invalid/duplicate externalPath')
            seen_paths.add(path)
            req = req_from_path(path)
            rec = {'source':'Workday','company':employer.company, 'source_id':req,
                   'title':str(row.get('title') or '').strip(),
                   'location_summary':str(row.get('locationsText') or '').strip(),
                   'url':employer.base + '/' + employer.board + path,
                   'external_path':path, 'identity':None, 'error':None,
                   'enrichment_status':'pending', 'score_ready':False, 'job_record':None}
            if req:
                from stage8_workday_identity import workday_key
                rec['identity'] = workday_key(rec, REGISTRY)
            if not rec['identity']:
                rec['error'] = 'Untrusted or missing ID'; rec['enrichment_status'] = 'error'
            else:
                if rec['identity'] in unique_ids: collisions.add(rec['identity'])
                unique_ids.add(rec['identity'])
            entries.append(rec)
        print(f'  {employer.slug} offset={page*PAGE_SIZE}: {len(rows)} rows')
        if len(rows) < PAGE_SIZE:
            short = True
            break
    if collisions:
        # No guessing/merging; fail the employer preview rather than corrupt queue identity.
        raise ValueError(f'{employer.slug}: {len(collisions)} requisition-ID collisions')
    if any(e['identity'] is None for e in entries):
        raise ValueError(f'{employer.slug}: untrusted/missing requisition IDs (retained in index, not imported)')
    complete = short and len(entries) == total
    if not complete:
        warnings.append(f'PARTIAL index {len(entries)}/{total}; NEVER mark missing jobs from this scan')
    return {'employer':employer.slug, 'index':entries,
            'reported_total':total,'index_complete':complete,'warnings':warnings}

def choose_and_enrich(preview, employer, client, queue, max_details, now, new_ids):
    """Fair new/backlog selection of due jobs; never title-filter."""
    # Queue import happens first so all indexed jobs are preserved, even on detail failure.
    import_preview(queue, preview, now)
    from stage8_workday_baseline_priority import select_baseline_batch
    pending = []
    for entry in preview['index']:
        row = get(queue, identity({'source':'Workday','company':employer.company,
                     'source_id':entry['source_id'],'url':entry['url']}))
        pending.append((row, entry))
    selected, priority = select_baseline_batch(pending, now, new_ids, max_details, fresh_quota=3)
    print(f"  Enrichment selection: new_to_monitoring={priority['selected_new_monitoring']}, "
          f"baseline_or_backlog={priority['selected_baseline_or_backlog']}, due_remaining={priority['remaining_due']}")
    successes = failures = 0
    from stage8_workday_adapter_queue_bridge import fail_enrichment
    for row, entry in selected:
        key = identity({'source':'Workday','company':employer.company,
                        'source_id':entry['source_id'],'url':entry['url']})
        try:
            data = client.get_json(employer.api + entry['external_path'])
            info = data.get('jobPostingInfo')
            if not isinstance(info, dict): raise ValueError('Missing jobPostingInfo')
            rid = str(info.get('jobReqId') or '').strip().upper()
            if rid != entry['source_id']: raise ValueError('Detail requisition mismatch')
            locations = info.get('additionalLocations') or []
            if isinstance(locations, str): locations = [locations]
            if not isinstance(locations, list): raise ValueError('Bad additionalLocations')
            loc = list(dict.fromkeys(str(x).strip() for x in [info.get('location'), *locations] if x and str(x).strip()))
            text = clean_html(info.get('jobDescription'))
            title = str(info.get('title') or entry['title']).strip()
            if not title or not loc or not meaningful_description(text):
                raise ValueError('Insufficient title, location or description')
            job = {'source':'Workday','source_id':rid,'company':employer.company,
                   'title':title, 'location':' | '.join(loc), 'url':entry['url'],
                   'description':text,'posted_at':'Unknown'}
            if identity(job) != key: raise ValueError('Untrusted detail identity')
            entry.update(job_record=job,score_ready=True,enrichment_status='ready')
            successes += 1
        except Exception as ex:
            fail_enrichment(queue,key,now,f'{type(ex).__name__}: {str(ex)[:130]}')
            failures += 1
    # Re-import the enriched snapshots: ready and local handoff are committed atomically.
    import_preview(queue, preview, now)
    return {'attempted':len(selected), 'ready':successes,'failed':failures,
            'eligible_pending_before':priority['due_new_monitoring']+priority['due_baseline_or_backlog'],
            'priority':priority}

def queue_outbox(queue):
    with closing(open_db(queue)) as con:
        return [dict(r) for r in con.execute('SELECT identity, fingerprint,job_json,state FROM review_outbox ORDER BY identity')]

def deliver_into_isolated_history(queue, history):
    with closing(open_db(queue)) as con:
        keys = [row['identity'] for row in con.execute("SELECT identity FROM handoffs WHERE state <> 'acknowledged' ORDER BY identity")]
    outcomes = {}
    for key in keys:
        answer = handoff(queue,history,key)
        outcomes[answer] = outcomes.get(answer,0) + 1
    return outcomes

def render_report(queue, settings_root, reports, now, scan_stats, handoffs):
    _, profile = load_settings(settings_root)
    saved = load_saved_jobs_read_only(settings_root)
    historic = load_historical_warnings_read_only(settings_root)
    candidates = []
    rows = []
    for entry in queue_outbox(queue):
        job = json.loads(entry['job_json'])
        if job_fingerprint(job) != entry['fingerprint']:
            raise AssertionError('Workday history fingerprint mismatch')
        result = score_job(job,profile)
        review, audit, counts = build_review_reports([job],profile,saved,
              {entry['fingerprint']},scorer=score_job,historical_rows=historic)
        # The review builder is the authoritative broad-review signal, not score alone.
        alert_candidate = {
           'company':job['company'],'title':job['title'],'location':job['location'],
           'url':job['url'],'source_id':job['source_id'], 'score':result['score'],
           'eligibility':result['geo_status'],
        }
        rows.append({'company':job['company'], 'title':job['title'],
            'location':job['location'], 'requisition_id':job['source_id'],
            'url':job['url'], 'score':result['score'], 'geo_status':result['geo_status'],
            'review_rows':len(review), 'alert_policy_eligible':email_eligible(alert_candidate),
            'identity':entry['identity']})
        if review and email_eligible(alert_candidate):
            candidates.append(alert_candidate)
    # Report rows are candidates, not guaranteed genuinely qualified or new-to-job-market.
    report={'generated_at_utc':now.isoformat(), 'discovery_scope':'testing-only isolated Workday workspace',
            'not_sent':True, 'scan_stats':scan_stats,'handoff_stats':handoffs,
            'reviewed_outbox_count':len(rows), 'potential_alert_candidates':candidates,
            'review':rows}
    write_json(reports / 'latest_workday_discovery_preview.json',report)
    print(f'  Review outbox: {len(rows)}; simulated eligible candidates: {len(candidates)}; NO EMAIL')
    return report

def run_live(root=WORKSPACE, settings_root=ROOT):
    """Opt-in workday run: all writes constrained to dedicated stage8 workspace."""
    root = Path(root).resolve(); settings_root = Path(settings_root).resolve()
    if root == settings_root or root == ORIGINAL_WORKSPACE.resolve():
        raise ValueError('Refusing to use original workspace or project root')
    if root != WORKSPACE.resolve():
        raise ValueError('Live priority validation only accepts stage8_workday_priority_workspace next to this script')
    if not (root / 'workday_queue_STAGE8.sqlite').is_file() or not (root / 'discovery_history_STAGE8.sqlite').is_file():
        raise FileNotFoundError('Copied priority workspace missing queue/history SQLite files; refusing to create new baseline')
    root.mkdir(parents=True, exist_ok=True)
    queue = root / 'workday_queue_STAGE8.sqlite'
    history = root / 'discovery_history_STAGE8.sqlite'
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    with FileLock(root / '.run.lock'):
        from stage8_workday_baseline_tracker import load_state, save_state_atomic, classify
        tracker_path = root / 'workday_baseline_state_STAGE8.json'
        tracker_state = load_state(tracker_path)
        initialize(queue)
        with closing(open_db(queue)) as con:
            exists = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history_intents'").fetchone()
        if not exists: init_handoff_audit(queue)
        if not history.exists():
            # Distinct synthetic baseline so first real Workday handoff is first-seen in this isolated workspace.
            fixture={'source':'Lever','company':'Stage8 Test Baseline','source_id':'stage8-baseline-abcdef0123456789',
                'title':'Stage 8 isolated workspace baseline','location':'San Diego, CA',
                'url':'https://jobs.lever.co/stage8-test-baseline/stage8-baseline-abcdef0123456789',
                'description':'Test baseline only','posted_at':'Unknown'}
            result = record_scan([fixture],path=history)
            if not result['baseline']: raise AssertionError('Unexpected history baseline')
        now = datetime.now(timezone.utc)
        original = identity_module.TRUSTED_BOARDS.copy()
        identity_module.TRUSTED_BOARDS.update(REGISTRY)
        try:
            stats = {}
            for slug, (pages,details) in LIMITS.items():
                employer = EMPLOYERS[slug]
                client = PublicClient(employer,pages+details)
                print(f'{employer.company}: requesting max {pages} pages + {details} details')
                try:
                    preview = index_only(employer,client,pages)
                    # Classify against employer's *prior* completed baseline.
                    # New-to-monitoring != newly posted by the employer.
                    keys = [identity({'source':'Workday','company':employer.company,
                        'source_id':e['source_id'],'url':e['url']}) for e in preview['index']]
                    classification, proposed_state = classify(
                        tracker_state, slug, keys, preview['index_complete'])
                    enrich = choose_and_enrich(preview,employer,client,queue,details,now,
                        classification['new_to_monitoring'])
                    # Commit tracker only after queue handoff was successful.
                    # On failure, retain the previous baseline state.
                    save_state_atomic(tracker_path, proposed_state)
                    tracker_state = proposed_state
                    stats[slug] = {'index_count':len(preview['index']),
                       'reported_total':preview['reported_total'],
                       'index_complete':preview['index_complete'],
                       'warnings':preview['warnings'],'baseline_classification':{
                           k:v for k,v in classification.items() if not isinstance(v,list)},
                       'baseline_count':len(classification['baseline_or_unclassified']),
                       'new_to_monitoring_count':len(classification['new_to_monitoring']),
                       'detail':enrich,
                       'http_requests':client.used}
                except Exception as ex:
                    # Preserve other employer and previously persisted pending jobs.
                    stats[slug]={'error':f'{type(ex).__name__}: {ex}', 'http_requests':client.used,
                                 'index_complete':False}
                    print(f'  ERROR (not silently successful): {ex}')
            handoffs = deliver_into_isolated_history(queue,history)
            report = render_report(queue,settings_root,reports,now,stats,handoffs)
            print('WORKSPACE:',root)
            print('REPORT:',reports / 'latest_workday_discovery_preview.json')
            print('HTTP requests this run:',sum(s['http_requests'] for s in stats.values()),'/',MAX_TOTAL_REQUESTS)
            print('No SMTP; no installed scheduler or source catalog changes.')
            return report
        finally:
            identity_module.TRUSTED_BOARDS.clear()
            identity_module.TRUSTED_BOARDS.update(original)

def self_test():
    """Offline smoke of end-to-end persistence and output, with a synthetic HTTP client."""
    from unittest.mock import patch
    fake_description=('Responsibilities: Provide support for software systems, investigate support incidents, '
        'document resolutions and coordinate troubleshooting with engineers. Qualifications: Experience with '
        'technical support and customer communication, ticket systems and problem solving.')
    class Client:
        def __init__(self, employer, budget):
            self.employer=employer;self.budget=budget;self.used=0
        def get_json(self,url,payload=None):
            self.used+=1
            if payload is not None:
                if payload['offset']>0: return {'jobPostings':[],'total':0}
                return {'jobPostings':[{'externalPath':'/job/San-Diego/Technical-Support-Associate_R12345-1',
                    'title':'Technical Support Associate','locationsText':'San Diego, CA'}], 'total':1}
            return {'jobPostingInfo':{'jobReqId':'R12345','title':'Technical Support Associate',
                'location':'San Diego, CA','additionalLocations':[], 'jobDescription':fake_description}}
    with TemporaryDirectory(prefix='jobpilot_stage8_live_runner_selftest_') as td:
        root=Path(td)/'stage8_workday_priority_workspace'
        global WORKSPACE
        old_workspace=WORKSPACE
        WORKSPACE=root
        # Self-test creates disposable baseline files before exercising guarded live path.
        root.mkdir(parents=True,exist_ok=True)
        initialize(root/'workday_queue_STAGE8.sqlite')
        record_scan([{'source':'Lever','company':'Stage8 Test Baseline','source_id':'stage8-baseline-abcdef0123456789',
            'title':'Stage 8 isolated workspace baseline','location':'San Diego, CA',
            'url':'https://jobs.lever.co/stage8-test-baseline/stage8-baseline-abcdef0123456789',
            'description':'Test baseline only','posted_at':'Unknown'}],path=root/'discovery_history_STAGE8.sqlite')
        try:
          with patch(__name__+'.PublicClient',Client):
              result=run_live(root=root,settings_root=ROOT)
              assert result['reviewed_outbox_count']==2
              assert len(result['potential_alert_candidates']) <= 2
              repeat=run_live(root=root,settings_root=ROOT)
              assert len(repeat['review'])==2
              with closing(open_db(root/'workday_queue_STAGE8.sqlite')) as con:
                  assert con.execute('SELECT COUNT(*) FROM review_outbox').fetchone()[0]==2
                  assert con.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0]==2
              print('PASS: Two employers, persistent isolated history and queue, no duplicate candidates on repeated run')
        finally:
          WORKSPACE=old_workspace
    print('SUCCESS: Offline runner acceptance passed; no HTTP or original files changed')

def main():
    ap=argparse.ArgumentParser()
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test',action='store_true')
    group.add_argument('--live',action='store_true')
    args=ap.parse_args()
    if args.self_test: self_test(); return
    print('LIVE STAGE 8 BASELINE-AWARE WORKDAY TEST — copied workspace ONLY; no SMTP')
    run_live()

if __name__=='__main__': main()
