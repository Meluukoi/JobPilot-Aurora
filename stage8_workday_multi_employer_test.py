"""Offline acceptance test for Stage 8 multi-employer Workday adapter.
Run: python -B stage8_workday_multi_employer_test.py
No network, databases, files, real JobPilot module imports, or email.
"""
from stage8_workday_multi_employer_adapter import EMPLOYERS, collect_preview, req_from_path
from stage8_workday_identity import workday_key

DESCRIPTION = ('Responsibilities include investigating technical support tickets, diagnosing user issues, '
               'and documenting resolutions. Qualifications include customer-service experience, '
               'written communication skills, and familiarity with software support systems.')


def row(req, title, *, suffix='', location='2 Locations'):
    return {'externalPath': f'/job/USA---Remote/{title.replace(" ", "-")}_{req}{suffix}',
            'title': title, 'locationsText': location}


class MockClient:
    def __init__(self, employer, pages, detail_overrides=None):
        self.employer = employer
        self.pages = pages
        self.detail_overrides = detail_overrides or {}
        self.calls = []
    def get_json(self, url, payload=None):
        assert url.startswith(self.employer.api + '/')
        self.calls.append((url, payload))
        if payload is not None:
            offset = payload['offset']
            assert payload['searchText'] == '' and payload['appliedFacets'] == {}
            page = self.pages.get(offset, [])
            return {'jobPostings': page, 'total': 0 if offset else sum(len(p) for p in self.pages.values())}
        path = url[len(self.employer.api):]
        if path in self.detail_overrides:
            return self.detail_overrides[path]
        entry = next(r for p in self.pages.values() for r in p if r['externalPath'] == path)
        ident = req_from_path(path)
        return {'jobPostingInfo': {'jobReqId': ident, 'title': entry['title'],
                                   'location': 'USA - Remote', 'additionalLocations': ['San Diego, CA'],
                                   'jobDescription': '<p>' + DESCRIPTION + '</p>'}}


def main():
    keys = []
    for name in ('crowdstrike', 'boozallen'):
        emp = EMPLOYERS[name]
        rows = [row('R30448', 'Community Player Support', suffix='-1'), row('R30449', 'IT Support', location='USA - Remote')]
        client = MockClient(emp, {0: rows})
        result = collect_preview(emp, client, max_pages=1, max_details=2)
        assert result['index_complete'] and len(result['index']) == 2
        assert len(result['ready_records']) == 2 and len(client.calls) == 3
        assert all(e['score_ready'] for e in result['index'])
        assert 'San Diego' in result['ready_records'][0]['location']
        assert set(result['ready_records'][0]) == {'source','source_id','company','title','location','url','description','posted_at'}
        assert result['ready_records'][0]['source_id'] == 'R30448'
        keys.append(result['index'][0]['identity'])
    assert keys[0] != keys[1]
    print('PASS 1: Both approved employers produce separate, score-ready JobPilot records')

    emp = EMPLOYERS['crowdstrike']
    old = row('R30448', 'Community Player Support', suffix='-1')
    new = row('R30448', 'Client Support', suffix='')
    a = {'source':'Workday','source_id':'R30448','company':emp.company,
         'url':emp.base+'/'+emp.board+old['externalPath']}
    b = {**a, 'url':emp.base+'/'+emp.board+new['externalPath']}
    assert workday_key(a, emp.registry) == workday_key(b, emp.registry)
    assert workday_key({**a, 'source_id':'R99999'}, emp.registry) is None
    assert workday_key({**a, 'company':'Unknown Employer'}, emp.registry) is None
    assert req_from_path(old['externalPath']) == 'R30448'
    print('PASS 2: Stable identity across title/path/suffix; mismatches and untrusted names rejected')

    # Two paths with the same requisition must be retained visibly, not merged.
    client = MockClient(emp, {0:[old,new]})
    result = collect_preview(emp, client, max_pages=1, max_details=2)
    assert len(result['index']) == 2 and len(result['collisions']) == 1
    assert len(result['ready_records']) == 0 and all(e['enrichment_status']=='error' for e in result['index'])
    assert len(client.calls) == 1
    print('PASS 3: Same-ID path collisions flagged without merging or detail requests')

    client = MockClient(emp, {0:[row('R30001','Support Specialist'),row('R30002','Support Specialist')]})
    result = collect_preview(emp, client, max_pages=1, max_details=2)
    assert len(result['ready_records']) == 2 and result['ready_records'][0]['source_id'] != result['ready_records'][1]['source_id']
    print('PASS 4: Similar job titles with different requisition IDs remain separate')

    first = row('R40001','Community Player Support')
    second = row('R40002','Help Desk')
    client = MockClient(emp, {0:[first,second]}, {second['externalPath']:{'jobPostingInfo':{'jobReqId':'R40002','jobDescription':'Loading...','location':'USA - Remote'}}})
    result = collect_preview(emp, client, max_pages=1, max_details=2)
    assert len(result['index']) == 2 and len(result['ready_records']) == 1
    assert result['index'][1]['enrichment_status'] == 'error'
    assert not result['index'][1]['score_ready']
    print('PASS 5: Unusual titles kept; bad descriptions not scored or dropped')

    # Full 20 rows with capped pages => explicit partial status, no title filtering.
    twenty = [row(f'R{i:05d}',f'Position {i}') for i in range(20)]
    client = MockClient(emp, {0:twenty,20:twenty[:1]})
    result = collect_preview(emp, client, max_pages=1, max_details=0)
    assert len(result['index']) == 20 and not result['index_complete']
    assert len(result['ready_records']) == 0 and len(client.calls) == 1
    assert any('PARTIAL' in w for w in result['warnings'])
    print('PASS 6: Partial index explicitly reported; all indexed jobs preserved pending')
    print('SUCCESS: Multi-employer Workday adapter offline acceptance passed.')
    print('No network, databases, reports, application tracker, live collectors, or emails touched.')

if __name__ == '__main__':
    main()
