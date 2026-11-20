"""Offline integration tests for Stage 8 Workday baseline-aware selection/runner.
No production paths, no network, no SMTP, no scheduler access.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sqlite3

import stage8_workday_live_runner_baseline_isolated as runner
from stage8_workday_baseline_tracker import classify, empty_state, load_state
from stage8_workday_baseline_priority import select_baseline_batch

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)

def item(name, age=0, state='pending', retry=None):
    return ({'identity':name,'first_seen':(NOW-timedelta(days=age)).isoformat(),
             'state':state,'next_retry':retry}, {'title':'Any role','identity':name})

def unit_tests():
    baseline = [item(f'B{i:03d}') for i in range(75)]
    a, m = select_baseline_batch(baseline, NOW, [],5,3)
    b, _ = select_baseline_batch(list(reversed(baseline)), NOW, [],5,3)
    assert len(a)==5 and m['selected_new_monitoring']==0
    assert {x[0]['identity'] for x in a}=={x[0]['identity'] for x in b}
    assert max(int(x[0]['identity'][1:]) for x in a)>20
    print('PASS 1: Baseline never gets new-monitoring priority and sampling is dispersed')

    new=[item(f'N{i:03d}') for i in range(8)]
    old=[item(f'O{i:03d}',age=i+1) for i in range(8)]
    pick, m=select_baseline_batch(new+old,NOW,[r['identity'] for r,_ in new],5,3)
    assert m['selected_new_monitoring']==3 and m['selected_baseline_or_backlog']==2
    assert len(pick)==5
    print('PASS 2: New-to-monitoring gets three slots; backlog retains two')

    retry=(NOW+timedelta(minutes=10)).isoformat()
    chosen,m=select_baseline_batch([item('stalled',state='stalled'),item('ready',state='ready'),
       item('delayed',retry=retry),item('due',age=3)],NOW,[],5,3)
    assert len(chosen)==1 and chosen[0][0]['identity']=='due'
    print('PASS 3: Stalled, ready and not-yet-due retries are excluded')

    s=empty_state()
    _,s=classify(s,'crowdstrike',['C1','C2'],True)
    rb,s=classify(s,'boozallen',['B1','B2'],False)
    assert not rb['new_to_monitoring'] and not rb['baseline_complete_after']
    rn,s=classify(s,'crowdstrike',['C1','C2','C3'],False)
    assert rn['new_to_monitoring']==['C3']
    rn,s=classify(s,'boozallen',['B2','B3'],False)
    assert not rn['new_to_monitoring']
    print('PASS 4: Complete Crowd baseline recognizes new ID; partial Booz remains baseline')

    for bad in (['absent'],['absent','absent']):
        try: select_baseline_batch([item('present')],NOW,bad)
        except ValueError: pass
        else: raise AssertionError('Expected invalid tracker ID refusal')
    print('PASS 5: Invalid tracker identity sets fail closed')


def integration_tests():
    from stage8_workday_adapter_queue_bridge import initialize, open_db
    counts={'crowdstrike':0,'boozallen':0}
    class FakeClient:
        def __init__(self, employer, budget):
            self.employer=employer; self.budget=budget; self.used=0
        def get_json(self, url, payload=None):
            if payload is not None:
                raise AssertionError('index_only must be patched; no HTTP')
            self.used+=1
            rid=url.rsplit('_',1)[-1]
            return {'jobPostingInfo':{'jobReqId':rid,'title':'Technical Support Associate',
                'location':'San Diego, CA','additionalLocations':[],
                'jobDescription':('Responsibilities: Provide technical troubleshooting and support for customers, '
                   'document incident resolutions and diagnose issues with enterprise systems. ')*3}}
    def fake_index(employer, client, pages):
        slug=employer.slug
        counts[slug]+=1
        # Full initial Crowd baseline, then one additional genuine new-to-monitoring.
        # Booz stays partial; extra baseline IDs must not count as new.
        ids = (['R10001','R10002'] if counts[slug]==1 else ['R10001','R10002','R10003']) if slug=='crowdstrike' else (
            ['R20001','R20002'] if counts[slug]==1 else ['R20001','R20002','R20003'])
        items=[]
        for rid in ids:
            path=f'/job/San-Diego/Technical-Support-Associate_{rid}'
            items.append({'source_id':rid,'title':'Technical Support Associate',
                'location_summary':'San Diego, CA','url':employer.base+'/'+employer.board+path,
                'external_path':path,'enrichment_status':'pending', 'score_ready':False,
                'job_record':None,'identity':runner.identity({'source':'Workday','company':employer.company,'source_id':rid,'url':employer.base+'/'+employer.board+path}),'error':None})
        return {'employer':slug,'index':items,'index_complete':slug=='crowdstrike',
            'reported_total':len(ids) if slug=='crowdstrike' else 2000,'warnings':[]}

    with TemporaryDirectory(prefix='stage8_baseline_integration_') as d:
        root=Path(d)/'stage8_workday_priority_workspace';root.mkdir()
        initialize(root/'workday_queue_STAGE8.sqlite')
        # Create existing history baseline locally using its own synthetic fixture.
        import discovery_history_stage8_preview as dh
        dh.record_scan([{'source':'Lever','company':'Stage8 Test Baseline',
            'source_id':'stage8-baseline-abcdef0123456789',
            'title':'Stage 8 isolated workspace baseline','location':'San Diego, CA',
            'url':'https://jobs.lever.co/stage8-test-baseline/stage8-baseline-abcdef0123456789',
            'description':'Test baseline only','posted_at':'Unknown'}],
            path=root/'discovery_history_STAGE8.sqlite')
        state_path=root/'workday_baseline_state_STAGE8.json'
        assert not state_path.exists()
        old_workspace=runner.WORKSPACE
        try:
            runner.WORKSPACE=root
            with patch.object(runner,'PublicClient',FakeClient),\
                 patch.object(runner,'index_only',fake_index),\
                 patch.object(runner,'render_report', lambda queue,settings,reports,now,stats,handoffs: {'scan_stats':stats}):
                run1=runner.run_live(root=root)
                assert run1['scan_stats']['crowdstrike']['new_to_monitoring_count']==0
                assert run1['scan_stats']['boozallen']['new_to_monitoring_count']==0
                after1=load_state(state_path)
                assert after1['employers']['crowdstrike']['baseline_complete'] is True
                assert after1['employers']['boozallen']['baseline_complete'] is False
                print('PASS 6: Runner persists full Crowd baseline and partial Booz separately')
                run2=runner.run_live(root=root)
                assert run2['scan_stats']['crowdstrike']['new_to_monitoring_count']==1
                assert run2['scan_stats']['boozallen']['new_to_monitoring_count']==0
                assert run2['scan_stats']['crowdstrike']['detail']['priority']['selected_new_monitoring']==1
                print('PASS 7: Second scan promotes Crowd new job, not Booz partial catalog')
                run3=runner.run_live(root=root)
                assert run3['scan_stats']['crowdstrike']['new_to_monitoring_count']==0
                assert run3['scan_stats']['boozallen']['new_to_monitoring_count']==0
                print('PASS 8: Third scan does not repeatedly mark same requisition new')
        finally:
            runner.WORKSPACE=old_workspace
        with sqlite3.connect(root/'workday_queue_STAGE8.sqlite') as c:
            assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==6
            assert c.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0]==6
        print('PASS 9: Queue/history handoff is preserved, no duplicate jobs')
        assert len(load_state(state_path)['employers'])==2
        print('PASS 10: Tracker state persisted only inside disposable isolated workspace')

if __name__=='__main__':
    unit_tests();integration_tests()
    print('SUCCESS: 10 offline tests; no network, SMTP, production files or scheduled tasks')
