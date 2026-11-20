"""Offline selector regression, synthetic queue records; no network, DB, SMTP."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import hashlib
import importlib.util
import json
import re
import sys

now=datetime(2026,11,5,tzinfo=timezone.utc)

def title_hint(t):
    return int(bool(re.search(r'(?i)(?:technical support|IT support|help desk|community.player support|desktop support|service desk)',str(t))))

def digest(p):
    return hashlib.sha256(p[0]['identity'].encode()).digest()

def due_rows(rows, at):
    return [p for p in rows if p[0]['state']=='pending' and
            (not p[0]['next_retry'] or datetime.fromisoformat(p[0]['next_retry']) <= at)]

def index_location(p):
    return p[1].get('location_summary','')

def geo_hint(s):
    s=str(s or '').lower()
    if any(x in s for x in ('usa','san diego','ca |','california','austin, tx','virginia')): return 'plausible_us'
    if any(x in s for x in ('india','australia','romania','united kingdom','remote, aus')):return 'clearly_foreign_only'
    return 'unknown'

def priority_value(p):
    return 3*title_hint(p[1]['title'])+{'plausible_us':3,'unknown':1,'clearly_foreign_only':-12}[geo_hint(index_location(p))]

def legacy_choice(candidates,at,ids,limit=5,fresh_quota=3):
    due=due_rows(candidates,at)
    new=set(ids)
    fresh=sorted((x for x in due if x[0]['identity'] in new),key=digest)
    backlog=sorted((x for x in due if x[0]['identity'] not in new),key=lambda x:(x[0]['first_seen'],digest(x)))
    target=min(limit,len(due))
    nf=min(fresh_quota,len(fresh),target)
    nb=min(len(backlog),target-nf)
    while nf+nb<target:
        if nf<len(fresh):nf+=1
        elif nb<len(backlog):nb+=1
        else:break
    selection=fresh[:nf]+backlog[:nb]
    return selection,{'selected_new_monitoring':nf,'selected_baseline_or_backlog':nb,
                      'due_new_monitoring':len(fresh),'due_baseline_or_backlog':len(backlog),
                      'remaining_due':len(due)-len(selection)}

fake_geo=ModuleType('stage8_workday_actionability_geo_refined')
for name in ('legacy_choice','due_rows','priority_value','geo_hint','index_location','digest'):
    setattr(fake_geo,'choose_actionable' if name=='legacy_choice' else name,globals()[name])
fake_title=ModuleType('stage8_workday_soft_enrichment_preview')
fake_title.title_hint=title_hint
source=Path(__file__).with_name('stage8_workday_actionability_budget_v2.py')
with patch.dict(sys.modules,{'stage8_workday_actionability_geo_refined':fake_geo,
                              'stage8_workday_soft_enrichment_preview':fake_title}):
    spec=importlib.util.spec_from_file_location('stage8_workday_actionability_budget_v2',source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
choose=module.choose_actionable

def job(req,title,location,day=1,state='pending',retry=None):
    return ({'identity':req,'state':state,'first_seen':(now-timedelta(days=day)).isoformat(),
             'next_retry':retry}, {'title':title,'location_summary':location})

def ids(out):return {r['identity'] for r,e in out}

# Real-scan-shaped example: three new jobs (two overseas; one federal alliances)
# should not consume all three protected fresh slots; older US support wins.
new=[job('R30789','Sr. Intelligence Analyst II (Remote)','Australia - Remote, VC'),
     job('R30799','Sr. Analyst 2, Billing','India - Pune'),
     job('R29510','Strategic Alliances Manager','USA - Remote, MD')]
old=[job('OLD1','Technical Support Associate','USA - Remote',day=10),
     job('OLD2','IT Support Technician','San Diego, CA',day=9),
     job('OLD3','Desktop Support Technician','USA - Remote',day=8),
     job('OLD4','Security Operations Analyst','USA - Remote',day=7),
     job('OLD5','Unusual Job','Remote',day=30),
     job('OLD6','Facilities Manager','USA - Remote',day=5)]
a,info=choose(new+old,now,[p[0]['identity'] for p in new])
assert len(a)==5 and len(ids(a))==5 and info['foreign_only_selected']==0
assert info['selected_new_monitoring']==1 and info['selected_baseline_or_backlog']==4
assert a[0][0]['identity']=='OLD5'
print('PASS 1: Actual observed foreign-only new-role pattern yields 0 foreign slots; oldest backlog explored')

# New relevant U.S. roles must still get all eligible fresh slots.
new_support=[job(f'NEW{i}','Technical Support Associate','USA - Remote') for i in range(3)]
a,info=choose(new_support+old,now,[p[0]['identity'] for p in new_support])
assert info['selected_new_monitoring']==3 and ids(a).issuperset({p[0]['identity'] for p in new_support})
print('PASS 2: Three relevant newly observed U.S. support roles retain three slots')

# New unusual US title preserved without misleading strong support keywords.
unusual=[job('NEWZ','Community Relations Concierge','USA - Remote')]
a,info=choose(unusual+old,now,['NEWZ'])
assert 'NEWZ' in ids(a)
print('PASS 3: One non-obvious U.S. role can receive an exploration slot')

# Foreign jobs never removed permanently; if only due candidates, details budget can still fill.
foreign=[job(f'F{i}','Technical Support Engineer','Australia - Remote') for i in range(3)]
a,info=choose(foreign,now,[p[0]['identity'] for p in foreign])
assert len(a)==3 and info['foreign_only_selected']==3
print('PASS 4: Foreign-only records remain monitored and are fallback selectable')

# If new jobs all explicitly foreign, existing US backlog gets available slots.
a,info=choose(foreign+old,now,[p[0]['identity'] for p in foreign])
assert info['selected_new_monitoring']==0 and info['foreign_only_selected']==0
print('PASS 5: Foreign-only fresh quota transfers to due U.S. backlog')

# Delay and state gates preserved.
blocked=[job('RETRY','Technical Support','USA - Remote',retry=(now+timedelta(hours=1)).isoformat()),
         job('READY','Technical Support','USA - Remote',state='ready')]
a,info=choose(blocked+old,now,['RETRY','READY'])
assert 'RETRY' not in ids(a) and 'READY' not in ids(a)
print('PASS 6: Retry and ready jobs excluded')

# Duplicate identities should be rejected by the working selector, not silently deduplicated.
try: choose([old[0],old[0]],now,[])
except AssertionError: pass
else:
    # Fake baseline doesn't detect duplicate; adaptive must enforce uniqueness.
    raise AssertionError('Duplicate input identity accepted')
print('PASS 7: Duplicate identities fail closed')

assert choose(old,now,[],limit=0)[0]==[]
assert len(choose(old,now,[],limit=1)[0])==1
print('PASS 8: Zero and one request budgets respected')
print('SUCCESS: 8 offline adaptive-priority checks; no network, SQLite, SMTP or production changes')
