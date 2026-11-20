"""Stage 8, offline-only comparison of current vs soft-title enrichment selection.

No network, DB writes, scheduler, scoring, notifications, or production integration.
Usage: python -B stage8_workday_soft_enrichment_preview.py --self-test
       python -B stage8_workday_soft_enrichment_preview.py --inspect
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / 'stage8_workday_baseline_workspace'

# Purely prioritization hints, NOT exclusions, applicant-match judgments or scoring.
HINTS = (
    (r'\b(it|information technology|help\s*desk|service\s*desk|desktop)\s+(support|technician|specialist|analyst|engineer)\b', 3),
    (r'\b(technical|technology|product|application|customer|client|player|community)\s+support\b', 3),
    (r'\b(support|systems|network|security|soc|incident|operations|data\s*center)\s+(analyst|technician|specialist|associate|administrator)\b', 2),
    (r'\b(technical|support)\s+(success|operations|coordinator)\b', 1),
    (r'\b(technician|helpdesk|troubleshooting|service desk|soc analyst|vulnerability)\b', 1),
)

def title_hint(title):
    title = str(title or '').casefold()
    return max((level for pat, level in HINTS if re.search(pat, title)), default=0)

def digest(pair):
    return hashlib.sha256(pair[0]['identity'].encode('utf-8')).digest()

def choose_soft(candidates, now, newly_monitored_ids, limit=5, fresh_quota=3):
    """Same new/backlog quotas as current selector, with a diversity reserve.

    At least one selection from each nonempty quota group uses the old fair order;
    other slots rank by a weak title hint. No title or geography can exclude a job.
    """
    from stage8_workday_baseline_priority import select_baseline_batch
    selected_old, metrics = select_baseline_batch(candidates, now, newly_monitored_ids,
                                                  limit=limit, fresh_quota=fresh_quota)
    fresh_keys = set(newly_monitored_ids)
    count_new = sum(row['identity'] in fresh_keys for row, _ in selected_old)
    count_backlog = len(selected_old) - count_new
    fresh = [(row, entry) for row, entry in candidates if row['identity'] in fresh_keys]
    backlog = [(row, entry) for row, entry in candidates if row['identity'] not in fresh_keys]
    def due(group):
        return [(row, entry) for row, entry in group if row['state'] == 'pending'
                and (not row.get('next_retry') or
                     datetime.fromisoformat(row['next_retry']).astimezone(timezone.utc) <= now)]
    def pick(group, quota, fresh_group):
        if quota <= 0: return []
        eligible = due(group)
        # Retain exact legacy order as the coverage reserve.
        if fresh_group:
            legacy = sorted(eligible, key=digest)
        else:
            legacy = sorted(eligible, key=lambda x: (x[0]['first_seen'], digest(x)))
        if quota == 1:
            return legacy[:1]  # Do not sacrifice last remaining coverage slot.
        chosen = [legacy[0]] if legacy else []
        rest = [p for p in eligible if p not in chosen]
        rest.sort(key=lambda p: (-title_hint(p[1].get('title') or json.loads(p[0]['job_json']).get('title')),
                                 (p[0]['first_seen'] if not fresh_group else ''), digest(p)))
        return chosen + rest[:quota-len(chosen)]
    result = pick(fresh, count_new, True) + pick(backlog, count_backlog, False)
    if len(result) != len(selected_old) or len({r['identity'] for r, _ in result}) != len(result):
        raise AssertionError('Selection quota/identity changed')
    return result, {**metrics, 'hinted_selected':sum(title_hint(e.get('title')) > 0 for _,e in result),
                    'coverage_reserved_slots':(int(count_new>0)+int(count_backlog>0))}

def _snapshot():
    if 'copy' not in ROOT.name.casefold():
        raise SystemExit('Safety stop: run only inside JobPilot_v0.2 - Copy')
    q = WORKSPACE / 'workday_queue_STAGE8.sqlite'
    if not q.is_file(): raise FileNotFoundError(q)
    uri = f'file:{q.resolve().as_posix()}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(x) for x in con.execute('SELECT identity,employer,job_json,state,first_seen,next_retry FROM jobs')]
    result = {}
    for row in rows:
        job = json.loads(row['job_json'])
        entry = {'title': job.get('title', ''), 'identity': row['identity']}
        result.setdefault(row['employer'],[]).append((row,entry))
    return result

def inspect():
    from stage8_workday_baseline_priority import select_baseline_batch
    now = datetime.now(timezone.utc)
    print('STAGE 8 SOFT ENRICHMENT — READ-ONLY COMPARISON')
    print('Snapshot uses current pending jobs; no new-to-monitoring IDs are inferred from historical data.')
    for employer, candidates in sorted(_snapshot().items()):
        original, _ = select_baseline_batch(candidates,now,[],limit=5,fresh_quota=3)
        proposed, stats = choose_soft(candidates,now,[],limit=5,fresh_quota=3)
        print(f'\n{employer}: pending due={stats["due_baseline_or_backlog"]}')
        for label, selected in [('CURRENT',original),('SOFT',proposed)]:
            print(f'  {label}: hints={sum(title_hint(e["title"])>0 for _,e in selected)}/{len(selected)}')
            for _, entry in selected:
                print(f'    [{title_hint(entry["title"])}] {entry["title"][:105]}')
    print('\nNo workday API requests, SMTP, DB writes, history, or scheduled tasks.')

def self_test():
    from stage8_workday_baseline_priority import select_baseline_batch
    now = datetime(2026,11,4,12,tzinfo=timezone.utc)
    titles = ['Principal Software Engineer','IT Support Specialist','Senior Account Executive',
              'Community Support Specialist','Data Center Technician','Systems Architect',
              'Product Support Analyst', 'Marketing Director', 'Network Analyst']
    candidates=[]
    for i,t in enumerate(titles):
        row={'identity':f'workday-test-{i}', 'state':'pending','next_retry':None,
             'first_seen':(now-timedelta(days=1)).isoformat(),
             'job_json':json.dumps({'title':t})}
        candidates.append((row,{'title':t}))
    old,_=select_baseline_batch(candidates,now,[],limit=5)
    new,m=choose_soft(candidates,now,[],limit=5)
    assert len(new)==5 and len(set(r['identity'] for r,e in new))==5
    assert sum(title_hint(e['title'])>0 for r,e in new) >= sum(title_hint(e['title'])>0 for r,e in old)
    print('PASS 1: Soft hints improve or preserve titles while keeping five eligible jobs')
    assert new[0][0]['identity']==old[0][0]['identity']
    print('PASS 2: Fairly dispersed legacy coverage slot remains selected')
    fresh_ids={candidates[1][0]['identity'],candidates[3][0]['identity'],candidates[7][0]['identity']}
    selected,stats=choose_soft(candidates,now,fresh_ids,limit=5,fresh_quota=3)
    assert stats['selected_new_monitoring']==3 and stats['selected_baseline_or_backlog']==2
    print('PASS 3: Three new-monitoring and two backlog slots preserved')
    candidates[1][0]['next_retry']=(now+timedelta(hours=2)).isoformat()
    candidates[3][0]['state']='stalled'
    candidates[4][0]['state']='ready'
    selected,stats=choose_soft(candidates,now,fresh_ids,limit=5)
    assert all(r['identity'] not in {candidates[i][0]['identity'] for i in [1,3,4]} for r,e in selected)
    print('PASS 4: Not-yet-due, stalled and ready records remain excluded')
    assert title_hint('Community/Player Support Specialist')>0
    assert title_hint('Data Center Technician')>0
    assert title_hint('Principal Software Engineer')==0
    print('PASS 5: Unconventional support and technician titles receive hints; generic senior engineering does not')
    assert len(choose_soft(candidates,now,[],limit=0)[0])==0
    assert len(choose_soft(candidates,now,[],limit=1)[0])==1
    print('PASS 6: Zero and one-slot budgets remain bounded')
    print('SUCCESS: 6 offline soft-priority checks; no network, databases, SMTP or production changes')

def main():
    p=argparse.ArgumentParser(description='Stage 8 testing-only soft title enrichment comparison; no writes/network')
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test',action='store_true');group.add_argument('--inspect',action='store_true')
    args=p.parse_args()
    if args.self_test:self_test()
    else:inspect()
if __name__=='__main__':main()
