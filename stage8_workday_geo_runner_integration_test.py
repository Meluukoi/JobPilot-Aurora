"""Stage 8 OFFLINE geo-runner integration acceptance (no network, SMTP or SQLite writes).
Run from JobPilot_v0.2 - Copy: python -B stage8_workday_geo_runner_integration_test.py
Uses in-memory synthetic Workday entries and mocks queue access and detail responses.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import json

import stage8_workday_live_runner_geo_experiment_guarded as runner
from stage8_workday_actionability_geo_refined import choose_actionable


def run():
    now = datetime(2026, 11, 4, 18, tzinfo=timezone.utc)
    employer = runner.EMPLOYERS['crowdstrike']
    source = [
        ('R80001', 'Analyst I, Falcon Complete (Remote, GBR)', 'United Kingdom - Remote', 15),
        ('R80002', 'Technology Support Associate', 'USA - Austin, TX', 14),
        ('R80003', 'Technical Support', 'USA - Remote', 13),
        ('R80004', 'Principal Engineer', 'USA - Sunnyvale, CA', 12),
        ('R80005', 'Technical Support Engineer (Remote, AUS)', '10 Locations', 11),
        ('R80006', 'Data Center Technician', 'USA - Santa Clara, CA', 10),
        ('R80007', 'Unusual Player Experience Associate', 'Remote', 9),
        ('R80008', 'Senior Cloud Architect', 'India - Bangalore', 8),
        ('R80009', 'Desktop Support Technician', 'USA - Remote', 0),
        ('R80010', 'Information Security Specialist', 'USA - San Diego, CA', 0),
        ('R80011', 'Unusual Operations Specialist', 'Remote', 0),
        ('R80012', 'IT Support Associate', 'United Kingdom - Remote', 0),
    ]
    entries, rows_by_key = [], {}
    for req, title, loc, age in source:
        path = '/job/Synthetic/' + title.replace(' ', '-') + '_' + req
        url = employer.base + '/' + employer.board + path
        item = {'source_id':req, 'title':title, 'location_summary':loc,
                'external_path':path, 'url':url, 'enrichment_status':'pending',
                'score_ready':False, 'job_record':None}
        key = runner.identity({'source':'Workday','company':employer.company,
                               'source_id':req,'url':url})
        item['identity'] = key
        rows_by_key[key] = {'identity': key, 'state':'pending',
                 'first_seen':(now-timedelta(days=age)).isoformat(),
                 'next_retry':None,
                 'job_json':json.dumps({'title':title,'location':loc})}
        entries.append(item)
    pending = [(rows_by_key[e['identity']], e) for e in entries]
    fresh = [e['identity'] for e in entries if e['source_id'] in {'R80009','R80010','R80011','R80012'}]
    expected, expected_metrics = choose_actionable(pending,now,fresh,5,3)
    expected_paths = [e['external_path'] for _,e in expected]
    assert len(set(expected_paths)) == 5
    assert expected_metrics['selected_new_monitoring'] == 3
    assert expected_metrics['selected_baseline_or_backlog'] == 2
    assert expected_metrics['exploration_reserved'] == 1
    print('PASS 1: Real geo selector respects 3 new / 2 backlog and reserves exploration')

    calls=[]
    class OfflineClient:
        def get_json(self, url, payload=None):
            assert payload is None
            calls.append(url)
            path = url[len(employer.api):]
            match = next(e for e in entries if e['external_path']==path)
            return {'jobPostingInfo':{'jobReqId':match['source_id'],
                    'title':match['title'],'location':match['location_summary'],
                    'additionalLocations':[],
                    'jobDescription':'Synthetic responsibilities and qualifications for offline test only. ' * 8}}

    imports=[]
    def fake_import(_queue, _preview, _now):
        imports.append('import')
        return {'indexed':len(entries)}
    preview={'employer':'crowdstrike','index':entries,'index_complete':False}
    with patch.object(runner,'import_preview',side_effect=fake_import), \
         patch.object(runner,'get',side_effect=lambda q,k: rows_by_key[k]), \
         patch.object(runner,'meaningful_description',return_value=True):
        result = runner.choose_and_enrich(preview,employer,OfflineClient(),
                                          Path('unused_synthetic_queue'),5,now,fresh)

    assert calls == [employer.api + p for p in expected_paths]
    assert imports == ['import','import']
    assert result['attempted']==result['ready']==5 and result['failed']==0
    assert result['priority']['selected_new_monitoring']==3
    assert result['priority']['selected_baseline_or_backlog']==2
    assert result['priority']['exploration_reserved']==1
    assert sum(e['score_ready'] is True for e in entries) == 5
    assert all(not e['score_ready'] for e in entries if e['external_path'] not in expected_paths)
    print('PASS 2: Runner requests exactly five geo-selected details; others remain pending')
    print('PASS 3: Runner preserves pending/ready boundary and existing quota metrics')
    assert len({e['source_id'] for e in entries})==len(entries)
    print('PASS 4: Different requisitions remain distinct; no duplicate detail requests')

    assert runner.WORKSPACE.name == 'stage8_workday_geo_experiment_workspace'
    for blocked in [runner.ORIGINAL_WORKSPACE, runner.PRIORITY_WORKSPACE,
                    runner.BASELINE_WORKSPACE, runner.ROOT]:
        try:
            runner.run_live(root=blocked, settings_root=runner.ROOT)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe workspace accepted: ' + str(blocked))
    print('PASS 5: Runner rejects original, priority, baseline and project-root workspaces')
    print('SUCCESS: 5 offline geo-runner integration checks; no HTTP, SMTP, DB writes or scheduling')


if __name__=='__main__':
    run()
