"""Testing-only career-fit selector checks + read-only comparison on actionability workspace.

Usage:
 python -B stage8_workday_career_fit_offline_test.py --self-test
 python -B stage8_workday_career_fit_offline_test.py --inspect
No live Workday queries, writes, production files, notifications or scheduler calls.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
from stage8_workday_career_fit_selector_v1 import choose_career_fit,career_value
from stage8_workday_actionability_budget_v2 import choose_actionable as previous
from stage8_workday_actionability_geo_refined import geo_hint,index_location

NOW=datetime(2026,11,5,12,tzinfo=timezone.utc)

def pair(key,title,loc='USA - Remote',age=2,state='pending',next_retry=None):
    row={'identity':key,'first_seen':(NOW-timedelta(days=age)).isoformat(),
         'state':state,'next_retry':next_retry,'job_json':json.dumps({'title':title,'location':loc})}
    entry={'title':title,'location_summary':loc,'source_id':key}
    return row,entry

def keys(selected):return {r['identity'] for r,e in selected}

def self_test():
    assert career_value('Technical Support Associate')>=8
    assert career_value('IT Support Specialist')>=8
    assert career_value('Community/Player Support Specialist')>=8
    assert career_value('SOC Analyst I')>=8
    assert career_value('Data Center Technician')>=8
    assert career_value('Customer Success Specialist')>0
    assert career_value('Senior Security Architect')<0
    assert career_value('Vulnerability Manager')<career_value('Vulnerability Analyst')
    print('PASS 1: Support, SOC, technician and transferable roles outrank senior engineering')

    backlog=[pair('oldest','Unusual Job',age=40),
             pair('support','IT Support Specialist',age=9),
             pair('soc','SOC Analyst I',age=8),
             pair('technician','Data Center Technician',age=7),
             pair('community','Community/Player Support Specialist',age=6),
             pair('director','Senior Security Architect',age=5),
             pair('manager','Vulnerability Manager',age=4)]
    selected,stats=choose_career_fit(backlog,NOW,[],limit=5)
    assert len(selected)==5 and 'oldest' in keys(selected)
    assert keys(selected)=={'oldest','support','soc','technician','community'}
    assert stats['career_fit_direct']==4
    print('PASS 2: Four actionable career roles plus an oldest-backlog exploration slot')

    fresh=[pair('f1','Technical Support Associate'),pair('f2','Customer Success Specialist'),
           pair('f3','SOC Analyst I'),pair('f4','Director, Engineering')]
    selected,stats=choose_career_fit(backlog+fresh,NOW,['f1','f2','f3','f4'])
    assert {'f1','f2','f3'}.issubset(keys(selected))
    assert stats['selected_new_monitoring']==3
    print('PASS 3: Three relevant new roles still receive protected fresh slots')

    abroad=[pair('foreign','Technical Support Engineer','Australia - Remote'),
            pair('foreign2','IT Support','India - Pune')]
    selected,stats=choose_career_fit(backlog+abroad,NOW,['foreign','foreign2'])
    assert not keys(selected).intersection({'foreign','foreign2'})
    print('PASS 4: Overseas-only title matches remain monitored but do not consume available U.S. slots')

    selected,stats=choose_career_fit(abroad,NOW,['foreign','foreign2'])
    assert len(selected)==2 and stats['foreign_only_selected']==2
    print('PASS 5: No candidate is permanently eliminated from the queue')

    with_retry=[pair('notdue','Technical Support','USA - Remote',next_retry=(NOW+timedelta(days=1)).isoformat()),
                pair('ready','IT Support','USA - Remote',state='ready')]
    selected,_=choose_career_fit(backlog+with_retry,NOW,[])
    assert 'notdue' not in keys(selected) and 'ready' not in keys(selected)
    print('PASS 6: Existing retry/ready exclusion preserved')

    try:choose_career_fit(backlog+[backlog[0]],NOW,[])
    except (AssertionError,ValueError):pass
    else:raise AssertionError('Duplicate identity was not rejected')
    print('PASS 7: Duplicate candidate identities still fail closed')

    assert choose_career_fit(backlog,NOW,[],limit=0)[0]==[]
    assert len(choose_career_fit(backlog,NOW,[],limit=1)[0])==1
    mixed=backlog+[pair('gb','Technical Support Engineer - Windows OS (Remote, GBR)','United Kingdom - Remote'),
                   pair('aus','Technical Support Engineer (Remote, AUS)','10 Locations'),
                   pair('us','Help Desk Analyst','USA - San Diego, CA')]
    out,_=choose_career_fit(mixed,NOW,[])
    assert 'us' in keys(out) and 'gb' not in keys(out) and 'aus' not in keys(out)
    print('PASS 9: Foreign-region title warnings and multi-location summaries do not beat U.S. support')
    print('PASS 8: Zero- and one-request budgets respected')
    print('SUCCESS: 9 offline career-fit checks; no HTTP, SQLite writes, SMTP or production changes')


def inspect():
    root=Path(__file__).resolve().parent
    if 'copy' not in root.name.lower():
        raise SystemExit('Safety guard: --inspect must run from JobPilot_v0.2 - Copy')
    q=root/'stage8_workday_actionability_budget_workspace'/'workday_queue_STAGE8.sqlite'
    if not q.is_file():raise FileNotFoundError(f'Missing testing queue: {q}')
    con=sqlite3.connect('file:'+q.resolve().as_posix()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    with closing(con):
        rows=[dict(x) for x in con.execute('SELECT identity,employer,job_json,state,first_seen,next_retry FROM jobs')]
    groups={}
    for row in rows:
        job=json.loads(row['job_json'])
        groups.setdefault(row['employer'],[]).append((row,{'title':job.get('title',''),
            'location_summary':job.get('location',''),'source_id':job.get('source_id','')}))
    now=datetime.now(timezone.utc)
    print('READ-ONLY CAREER FIT COMPARISON: prior adaptive vs candidate career-fit')
    print('Baseline/backlog only; historical new-to-monitoring labels are not reconstructed.')
    for name,pairs in sorted(groups.items()):
        print(f'\nEMPLOYER: {name}, monitored={len(pairs)}')
        for label,selector in [('PREVIOUS',previous),('CAREER_FIT',choose_career_fit)]:
            chosen,stats=selector(pairs,now,[],limit=5,fresh_quota=3)
            print(f'  {label}: selected={len(chosen)} fit-positive={sum(career_value(e["title"])>0 for _,e in chosen)} '
                  f'strong-fit={sum(career_value(e["title"])>=8 for _,e in chosen)} '
                  f'explicit-foreign={sum(geo_hint(index_location(p))=="clearly_foreign_only" for p in chosen)}')
            for _,e in chosen:
                print(f'    fit={career_value(e["title"]):3} | {e["title"][:78]} | {e["location_summary"][:60]}')
    print('\nNo new dates or applications inferred; no network/database writes.')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--self-test',action='store_true');g.add_argument('--inspect',action='store_true')
    args=p.parse_args()
    self_test() if args.self_test else inspect()
