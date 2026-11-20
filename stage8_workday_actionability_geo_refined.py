"""Stage 8 geo-refined read-only, OFFLINE comparison of Workday enrichment priorities.

Does not change any installed selector, runner, database, ATS configuration,
scorer, applications, email, or scheduling. No network calls.

Usage:
  python -B stage8_workday_actionability_geo_refined.py --self-test
  python -B stage8_workday_actionability_geo_refined.py --inspect

Candidate geography is an INDEX-ONLY heuristic, never a final eligibility ruling.
No actual posting dates, salary eligibility, clearance, or experience fit inferred.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from stage8_workday_baseline_priority import select_baseline_batch
from stage8_workday_soft_enrichment_preview import title_hint

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / 'stage8_workday_baseline_workspace'

# Country/country-code metadata only. Do not infer ineligibility from generic
# 'Remote', missing location, an ambiguous city, or a foreign-language title.
FOREIGN_LABELS = re.compile(
    r'(?i)(?:\b(?:GBR|AUS|IND|EUR|EMEA|UK|United Kingdom|England|Scotland|'
    r'Ireland|India|Australia|Romania|Denmark|Germany|France|Canada|'
    r'Netherlands|Singapore|Philippines|Japan|Poland|Spain|Mexico)\b)'
)
US_SIGNAL = re.compile(
    r'(?i)(?:\b(?:USA|United States|U\.S\.|US Remote|Remote US|Remote USA|'
    r'California|San Diego|Rancho Cordova|Sacramento|Sunnyvale|Redmond|'
    r'New York|Seattle|Austin|Denver|Virginia|Maryland|Florida)\b|'
    r'\b(?:CA|NY|TX|WA|CO|VA|MD|FL|OH|NC|GA|MA|IL|AZ|OR|NJ|PA|TN|UT|MN|DC)\b)'
)
# Explicit regional metadata, not city-only guesses. Stuttgart alone is ambiguous;
# Baden-Wurttemberg is a German state and makes this combination unambiguous.
FOREIGN_REGION = re.compile(r'(?i)\bBaden[- ]W(?:u|ue|ü)rttemberg\b')
# Title country markers are weak negative hints ONLY when indexed locations do not
# reveal whether US is among multiple locations. Never equate these with a verified
# foreign-only posting or final California eligibility decision.
TITLE_FOREIGN_REMOTE = re.compile(
    r'(?i)\bremote\s*[,/-]?\s*\(?\s*(?:AUS|GBR|IND|EUR|EMEA|UK)\s*\)?\b'
)
CLEARANCE_SIGNAL = re.compile(r'(?i)\b(?:clearance|cleared|TS/SCI|polygraph)\b')


def geo_hint(raw):
    """Return plausible_us, unknown, or clearly_foreign_only (index hints)."""
    s = str(raw or '').strip()
    if not s:
        return 'unknown'
    # Multiple locations: a single US marker precludes foreign-only label.
    if US_SIGNAL.search(s):
        return 'plausible_us'
    if FOREIGN_LABELS.search(s) or FOREIGN_REGION.search(s):
        return 'clearly_foreign_only'
    return 'unknown'


def digest(pair):
    return hashlib.sha256(pair[0]['identity'].encode('utf-8')).digest()


def due_rows(candidates, now):
    result=[]
    for row, entry in candidates:
        if row['state'] != 'pending':
            continue
        if row.get('next_retry') and datetime.fromisoformat(row['next_retry']).astimezone(timezone.utc) > now:
            continue
        result.append((row, entry))
    return result


def index_location(pair):
    return pair[1].get('location_summary') or json.loads(pair[0]['job_json']).get('location', '')


def foreign_title_warning(pair):
    """Weak risk signal, not a verified geography classification."""
    title = pair[1].get('title', '')
    location = index_location(pair)
    return (geo_hint(location) == 'unknown' and
            bool(TITLE_FOREIGN_REMOTE.search(str(title))))


def priority_value(pair):
    title = pair[1].get('title', '')
    geo = geo_hint(index_location(pair))
    # Title and location influence order, never eligibility or removal.
    return 3 * title_hint(title) + {'plausible_us': 3, 'unknown': 1,
                                   'clearly_foreign_only': -12}[geo] - (6 if foreign_title_warning(pair) else 0)


def choose_actionable(candidates, now, newly_monitored_ids, limit=5, fresh_quota=3):
    """Same quota counts as working selector. One legacy exploration slot per batch.

    If any backlog is due, the exploration slot comes from the oldest due
    backlog, guaranteeing some non-hinted coverage each run. Otherwise it comes
    from the fresh quota's stable hash order. No permanent exclusion by title or
    location; all due candidates remain selectable on subsequent scans.
    """
    existing, baseline_metrics = select_baseline_batch(candidates, now,
                                      newly_monitored_ids, limit=limit,
                                      fresh_quota=fresh_quota)
    fresh_set=set(newly_monitored_ids)
    counts={'fresh':sum(r['identity'] in fresh_set for r,_ in existing)}
    counts['backlog']=len(existing)-counts['fresh']
    due=due_rows(candidates, now)
    groups={
        'fresh':[x for x in due if x[0]['identity'] in fresh_set],
        'backlog':[x for x in due if x[0]['identity'] not in fresh_set],
    }
    groups['fresh'].sort(key=digest)
    groups['backlog'].sort(key=lambda x:(x[0]['first_seen'], digest(x)))
    selected=[]
    explore_group='backlog' if counts['backlog'] else 'fresh'
    if counts[explore_group]:
        selected.append(groups[explore_group][0])
        counts[explore_group]-=1
        groups[explore_group]=groups[explore_group][1:]
    for group in ('fresh', 'backlog'):
        # Ranking cannot make a job permanently ineligible; one exploration
        # job is always chosen by baseline legacy order when limit > 0.
        ranked=sorted(groups[group], key=lambda p:(-priority_value(p), digest(p)))
        selected.extend(ranked[:counts[group]])
    identities=[r['identity'] for r,_ in selected]
    if len(identities)!=len(existing) or len(set(identities))!=len(identities):
        raise AssertionError('Working selector quotas or unique identity broken')
    if sum(k in fresh_set for k in identities) != baseline_metrics['selected_new_monitoring']:
        raise AssertionError('New-to-monitoring quota changed')
    return selected, {**baseline_metrics,
        'exploration_reserved':int(bool(existing)),
        'title_hints':sum(title_hint(e.get('title'))>0 for _,e in selected)}


def summary(selected, now):
    geo=Counter(geo_hint(index_location(p)) for p in selected)
    titles=Counter(e.get('title','') for _,e in selected)
    oldest=max(((now-datetime.fromisoformat(r['first_seen']).astimezone(timezone.utc)).days
                for r,_ in selected),default=0)
    return {'hints':sum(title_hint(e.get('title'))>0 for _,e in selected),
       'plausible_us':geo['plausible_us'],
       'clearly_foreign_only':geo['clearly_foreign_only'],
       'unknown':geo['unknown'],
       'foreign_title_warnings':sum(foreign_title_warning(p) for p in selected),
       'distinct_requisitions':len({r['identity'] for r,_ in selected}),
       'identical_titles':{t:n for t,n in titles.items() if n>1},
       'oldest_selected_age_days':oldest,
       'potentially_actionable_proxy':sum(title_hint(e.get('title'))>0 and
             geo_hint(index_location((r,e)))!='clearly_foreign_only'
             and not foreign_title_warning((r,e)) for r,e in selected)}


def snapshot():
    if 'copy' not in ROOT.name.casefold():
        raise SystemExit('Safety stop: use JobPilot_v0.2 - Copy only')
    q=WORKSPACE/'workday_queue_STAGE8.sqlite'
    if not q.is_file(): raise FileNotFoundError(q)
    uri='file:'+q.resolve().as_posix()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(x) for x in con.execute('SELECT identity,employer,job_json,state,first_seen,next_retry FROM jobs')]
    all_rows={}
    for row in rows:
        job=json.loads(row['job_json'])
        entry={'title':job.get('title',''), 'location_summary':job.get('location',''),
               'identity':row['identity']}
        all_rows.setdefault(row['employer'],[]).append((row,entry))
    return all_rows


def inspect():
    now=datetime.now(timezone.utc)
    print('STAGE 8 GEO-REFINED ACTIONABILITY — READ-ONLY COMPARISON')
    print('No new-to-monitoring IDs inferred from historical state. All shown jobs are baseline/backlog.')
    print('US geography is PLAUSIBLE, not verified remote eligibility or salary;')
    print('experience, clearance and compensation remain UNKNOWN until full descriptions.')
    print('A foreign region in the title is a weak warning, NOT a foreign-only verdict.')
    for employer, pairs in sorted(snapshot().items()):
        original,_=select_baseline_batch(pairs,now,[],limit=5,fresh_quota=3)
        title_soft,_=__import__('stage8_workday_soft_enrichment_preview').choose_soft(pairs,now,[],limit=5,fresh_quota=3)
        # Old experiment is imported without modifying it, so this is a fair comparison.
        from stage8_workday_actionability_preview import choose_actionable as prior_choose
        previous,_=prior_choose(pairs,now,[],limit=5,fresh_quota=3)
        proposed,info=choose_actionable(pairs,now,[],limit=5,fresh_quota=3)
        due=due_rows(pairs,now)
        ages=[(now-datetime.fromisoformat(r['first_seen']).astimezone(timezone.utc)).days for r,_ in due]
        print(f'\n{employer}: pending_due={len(due)}, oldest_due_days={max(ages,default=0)}, '
              f'due_over_7_days={sum(age>=7 for age in ages)}')
        for label,selection in [('CURRENT',original),('TITLE_SOFT',title_soft),('ACTIONABILITY_OLD',previous),('GEO_REFINED',proposed)]:
            s=summary(selection,now)
            print(f'  {label}: title_hints={s["hints"]}/{len(selection)}, '
                  f'geo_plausible={s["plausible_us"]}, '
                  f'clearly_foreign_only={s["clearly_foreign_only"]}, '
                  f'geo_unknown={s["unknown"]}, title_foreign_warning={s["foreign_title_warnings"]}, '
                  f'distinct_req={s["distinct_requisitions"]}, '
                  f'proxy_title_plus_nonforeign={s["potentially_actionable_proxy"]}')
            for r,e in selection:
                try: rid=json.loads(r['identity'])[-1]
                except (ValueError,TypeError): rid=r['identity']
                print(f'    {rid} | geo={geo_hint(index_location((r,e)))} | foreign_title_warning={foreign_title_warning((r,e))} | title={e["title"][:90]} | '
                      f'index_location={index_location((r,e))[:85]}')
            if s['identical_titles']:
                print('    Same-title counts (NOT assumed duplicates):',s['identical_titles'])
        print(f'  GEO_REFINED exploration_reserved={info["exploration_reserved"]}, '
              f'backlog_age_risk={"YES" if ages and max(ages)>=7 else "not currently >=7d"}')
    print('\nCurrent and experimental policies select identities, not titles; no duplicate identity allowed.')
    print('Continuous priority arrivals can still outpace enrichment capacity; inspect backlog age over time.')
    print('No API, SQLite writes, SMTP, history, scheduler or production changes.')


def self_test():
    now=datetime(2026,11,4,12,tzinfo=timezone.utc)
    def mk(req,title,loc,day=1):
        ident=json.dumps(['workday','test','board',req],separators=(',',':'))
        row={'identity':ident,'state':'pending','first_seen':(now-timedelta(days=day)).isoformat(),
             'next_retry':None,'job_json':json.dumps({'title':title,'location':loc})}
        return row,{'title':title,'location_summary':loc}
    assert geo_hint('Remote')=='unknown'
    assert geo_hint('Remote, GBR')=='clearly_foreign_only'
    assert geo_hint('Remote, AUS')=='clearly_foreign_only'
    assert geo_hint('USA - Remote, CA')=='plausible_us'
    assert geo_hint('USA - Remote, CA | Ireland Remote')=='plausible_us'
    assert geo_hint('')=='unknown'
    print('PASS 1: Explicit foreign-only lowered; generic Remote remains unknown; mixed US plausible')
    pairs=[mk('R1','Technical Support Engineer','Remote, GBR'),
           mk('R2','Technical Support Associate','USA - Remote, CA'),
           mk('R3','Technical Support Engineer','Remote, AUS'),
           mk('R4','IT Support Technician','Remote'),
           mk('R5','Community/Player Support Specialist','San Diego, CA'),
           mk('R6','Principal Engineer','USA - Remote, CA'),
           mk('R7','Unusual Content Specialist','Remote'),
           mk('R8','Facilities Manager','Remote, GBR')]
    chosen,stats=choose_actionable(pairs,now,[],limit=5)
    assert len(chosen)==5 and summary(chosen,now)['distinct_requisitions']==5
    assert summary(chosen,now)['clearly_foreign_only']<=1  # one legacy exploration pick may be foreign
    assert title_hint('Community/Player Support Specialist')>0
    print('PASS 2: Soft ranking favors plausible/unknown geography and nonstandard support titles')
    legacy,_=select_baseline_batch(pairs,now,[],limit=5)
    assert chosen[0][0]['identity']==legacy[0][0]['identity']
    print('PASS 3: One fair exploration slot remains from legacy baseline order')
    newids=[pairs[1][0]['identity'],pairs[2][0]['identity'],pairs[4][0]['identity'],pairs[6][0]['identity']]
    chosen,stats=choose_actionable(pairs,now,newids,limit=5,fresh_quota=3)
    assert stats['selected_new_monitoring']==3 and stats['selected_baseline_or_backlog']==2
    print('PASS 4: Newly observed IDs receive 3 slots, backlog receives 2; baseline classification external')
    pairs[1][0]['next_retry']=(now+timedelta(hours=1)).isoformat()
    pairs[2][0]['state']='stalled'
    pairs[4][0]['state']='ready'
    chosen,_=choose_actionable(pairs,now,newids,limit=5)
    assert not {pairs[i][0]['identity'] for i in (1,2,4)}.intersection(
        {r['identity'] for r,_ in chosen})
    print('PASS 5: Retry, ready and stalled candidates excluded')
    duplicate_title=[mk('R100','Personnel Security Specialist','Virginia'),
                     mk('R101','Personnel Security Specialist','Virginia')]
    selected,_=choose_actionable(duplicate_title,now,[],limit=5)
    assert len(selected)==2 and summary(selected,now)['distinct_requisitions']==2
    try:
        choose_actionable([duplicate_title[0],duplicate_title[0]],now,[],limit=5)
    except ValueError: pass
    else: raise AssertionError('Same identity accepted twice')
    print('PASS 6: Same-title distinct requisitions retained; repeated identity rejected')
    assert choose_actionable(pairs,now,[],limit=0)[0]==[]
    assert len(choose_actionable(pairs,now,[],limit=1)[0])==1
    print('PASS 7: Zero/one-slot budget bounded')
    # Simulated aging: a continuously replenished higher-hint pool cannot
    # prevent at least one due backlog item being explored each batch.
    rolling=[mk('OLD1','Unconventional Job','Remote',day=25),
             mk('OLD2','Unconventional Job','Remote',day=24)]
    for cycle in range(2):
        rolling.extend(mk(f'NEW{cycle}-{i}','IT Support Specialist','San Diego, CA',day=0)
                       for i in range(8))
        chosen,_=choose_actionable(rolling,now,[],limit=5)
        assert chosen[0][0]['identity'] in {p[0]['identity'] for p in rolling[:2]}
        identities={r['identity'] for r,_ in chosen}
        rolling=[p for p in rolling if p[0]['identity'] not in identities]
    assert not any('OLD' in r['identity'] for r,_ in rolling)
    print('PASS 8: Oldest legacy backlog progresses despite high-hint replenishment')
    # Targeted regression cases from the read-only real queue preview.
    assert geo_hint('Stuttgart, Baden-Wurttemberg') == 'clearly_foreign_only'
    assert geo_hint('Stuttgart') == 'unknown'  # avoid city-alone assumption
    assert geo_hint('10 Locations') == 'unknown'
    assert geo_hint('USA - Remote | Germany') == 'plausible_us'
    print('PASS 9: Explicit German state recognized; location counts and ambiguous cities remain unknown')
    foreign_title = mk('AUS1', 'Technical Support Engineer, Korean-speaking (Remote, AUS)', '10 Locations')
    assert foreign_title_warning(foreign_title)
    assert geo_hint(index_location(foreign_title)) == 'unknown'
    assert not foreign_title_warning(mk('MIX1', 'Technical Support (Remote, AUS)', 'USA - Remote | Australia'))
    assert not foreign_title_warning(mk('LANG1', 'Korean-speaking Technical Support', 'Remote'))
    assert not foreign_title_warning(mk('GEN1', 'Technical Support', 'Remote'))
    print('PASS 10: Foreign title is only a weak warning under unknown index geography')
    # Same title preference, U.S. location beats unknown foreign-coded title.
    pair_us = mk('US1', 'Technical Support Engineer', 'USA - Remote')
    assert priority_value(pair_us) > priority_value(foreign_title)
    assert choose_actionable([pair_us,foreign_title],now,[],limit=2)[1]['exploration_reserved'] == 1
    print('PASS 11: Country-tagged ambiguous job is deprioritized but retained for exploration')
    print('SUCCESS: 11 offline geo-refined actionability checks; no network, DB writes, SMTP or production changes')


def main():
    p=argparse.ArgumentParser(description='Stage 8 index-only actionability and exploration comparison')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--self-test',action='store_true')
    g.add_argument('--inspect',action='store_true')
    a=p.parse_args()
    self_test() if a.self_test else inspect()

if __name__=='__main__':
    main()
