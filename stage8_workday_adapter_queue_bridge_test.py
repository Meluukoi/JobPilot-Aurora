"""Offline adapter-to-queue integration acceptance: no network or project DB writes.
Run: python -B stage8_workday_adapter_queue_bridge_test.py
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from stage8_workday_multi_employer_adapter import EMPLOYERS, collect_preview
from stage8_workday_adapter_queue_bridge import initialize, import_preview, get, counts, identity, fail_enrichment, pending_age, open_db
from contextlib import closing

DESC=('Responsibilities include responding to technical support requests, investigating application issues, '
      'documenting incidents, helping users troubleshoot connectivity problems, and escalating problems to engineering. '
      'Qualifications include experience with software support and excellent communication skills.')

class FakeClient:
    def __init__(self, employer, req, title):
        self.employer=employer
        self.req=req
        self.title=title
        self.calls=0
    def get_json(self, url, payload=None):
        self.calls+=1
        if payload is not None:
            return {'total':1,'jobPostings':[{'externalPath':f'/job/USA---Remote/{self.title.replace(" ","-")}_{self.req}-1',
                       'title':self.title,'locationsText':'USA - Remote'}]}
        return {'jobPostingInfo':{'jobReqId':self.req,'title':self.title,'location':'USA - Remote',
                 'additionalLocations':['San Diego, CA'],'jobDescription':DESC}}

def preview(slug,req,title,detail=1):
    e=EMPLOYERS[slug]; c=FakeClient(e,req,title)
    result=collect_preview(e,c,max_pages=1,max_details=detail)
    assert c.calls==1+detail
    assert result['index_complete']
    return result

def run():
    now=datetime(2026,11,3,12,tzinfo=timezone.utc)
    with TemporaryDirectory(prefix='jobpilot_workday_adapter_queue_offline_') as temp:
        path=Path(temp)/'TEST_ONLY.sqlite'; initialize(path)
        c=preview('crowdstrike','R30448','Community Player Support Specialist',0)
        b=preview('boozallen','R30448','IT Support Analyst',0)
        import_preview(path,c,now); import_preview(path,b,now)
        ck=identity({'source':'Workday','company':'CrowdStrike','source_id':'R30448','url':c['index'][0]['url']})
        bk=identity({'source':'Workday','company':'Booz Allen Hamilton','source_id':'R30448','url':b['index'][0]['url']})
        assert ck!=bk and counts(path)=={'pending':2}
        with closing(open_db(path)) as con:
            assert con.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0]==0
        print('PASS 1: Two employers remain distinct with identical requisition IDs; pending outside handoff')
        # Reindex after simulated process restart, without resetting state.
        import_preview(path,c,now+timedelta(days=1)); assert get(path,ck)['state']=='pending'
        assert pending_age(path,now+timedelta(days=8))==2
        assert fail_enrichment(path,ck,now+timedelta(days=1),'HTTP 503')
        assert not fail_enrichment(path,ck,now+timedelta(days=1,minutes=1),'too early')
        print('PASS 2: Pending survives restart; aging visible; retry backoff preserved')
        c_ready=preview('crowdstrike','R30448','Renamed Customer Support Specialist',1)
        import_preview(path,c_ready,now+timedelta(days=2))
        assert get(path,ck)['state']=='ready'
        with closing(open_db(path)) as con:
            assert con.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0]==1
        import_preview(path,c_ready,now+timedelta(days=3))
        with closing(open_db(path)) as con:
            assert con.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0]==1
        print('PASS 3: Enriched changed-title/path posting becomes ready; one durable handoff only')
        # Complete employer-specific scan cannot accidentally mark other tenant absent.
        empty_c={'employer':'crowdstrike','index':[],'index_complete':True}
        import_preview(path,empty_c,now+timedelta(days=4))
        assert get(path,ck)['missing_count']==1 and get(path,bk)['missing_count']==0
        import_preview(path,{'employer':'crowdstrike','index':[],'index_complete':False},now+timedelta(days=5))
        assert get(path,ck)['missing_count']==1
        import_preview(path,empty_c,now+timedelta(days=6))
        assert get(path,ck)['missing_flag']==1 and get(path,bk)['missing_flag']==0
        print('PASS 4: Missing flags require two complete scans and are employer-scoped; partial scans ignored')
        # Fail-closed on mismatched identity and no guessing.
        bad=preview('boozallen','R99999','Unusual Support Title',0)
        bad['index'][0]['source_id']='R77777'
        try: import_preview(path,bad,now)
        except ValueError: pass
        else: raise AssertionError('Inconsistent ID accepted')
        print('PASS 5: Untrusted identity rejected; unusual titles otherwise retained')
        assert counts(path)=={'pending':1,'ready':1}
        print('SUCCESS: Offline multi-employer adapter-to-queue integration validated')
        print('No HTTP, real discovery history, existing databases, reports, scheduler, or emails used.')
        print('NOT IMPLEMENTED: queue-to-production-history delivery, queue scheduling, or cross-database transaction.')

if __name__=='__main__': run()
