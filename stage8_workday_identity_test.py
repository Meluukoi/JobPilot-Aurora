"""Offline Stage 8 identity acceptance tests. Run in testing-copy directory.

Reads only local Python modules. No SQLite connections, network, or file writes.
Use python -B to suppress bytecode caching.
"""
from copy import deepcopy
from discovery_history import job_fingerprint as original
from discovery_history_stage8_preview import job_fingerprint as candidate
from stage8_workday_identity import workday_key

BASE = {
    'source': 'Workday', 'source_id': 'R30448', 'company': 'CrowdStrike',
    'title': 'Community / Player Support Specialist', 'location': 'USA - Remote',
    'url': 'https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/USA---Remote/Support_R30448',
}

def variant(**kwargs):
    job = deepcopy(BASE)
    job.update(kwargs)
    return job

def check():
    fingerprint = candidate(BASE)
    assert fingerprint and len(fingerprint) == 64
    for job in [
        variant(title='Renamed: Technical Support'),
        variant(location='Sunnyvale, CA; Seattle, WA'),
        variant(url='https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/USA---Sunnyvale-CA/New-Title_R30448-1'),
        variant(url='https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/USA---Remote/Different-Title_R30448-2?tracking=xyz'),
    ]:
        assert candidate(job) == fingerprint
    print('PASS 1: Stable fingerprint across title, URL, location, and -1 suffix')

    assert candidate(variant(source_id='R30449', url=BASE['url'].replace('R30448','R30449'))) != fingerprint
    print('PASS 2: Different requisitions with similar titles stay separate')

    other = variant(source_id='R30448', company='Example Corp',
                    url='https://examplecorp.wd5.myworkdayjobs.com/careers/job/USA---Remote/Support_R30448')
    registry={('crowdstrike','crowdstrikecareers'):'crowdstrike',
              ('examplecorp','careers'):'examplecorp'}
    assert workday_key(other, registry) != workday_key(BASE, registry)
    assert candidate(other) is None  # No unapproved employer is silently accepted.
    print('PASS 3: Same requisition across tenants separates; unknown employer rejected')

    bad = [
        variant(source_id=''), variant(source_id='R30449'),
        variant(source_id='R30448-1'),
        variant(url=BASE['url'].replace('myworkdayjobs.com','evil.example')),
        variant(url=BASE['url'].replace('https:', 'http:')),
        variant(url=BASE['url'].replace('/crowdstrikecareers/', '/otherboard/')),
        variant(company='Other Employer'),
        variant(url='https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers/job/Some-title-without-ID'),
    ]
    assert all(candidate(job) is None for job in bad)
    print('PASS 4: Missing, mismatched, untrusted identities never guessed')

    legacy = [
        {'source':'Greenhouse', 'source_id':'5258713007', 'company':'Upshop', 'title':'Customer Support', 'url':'https://job-boards.greenhouse.io/upshop/jobs/5258713007'},
        {'source':'Lever', 'source_id':'74e91591-ef69-465b-892b-1c82e07744a9', 'company':'Lyra Health', 'title':'Support','url':'https://jobs.lever.co/lyrahealth/74e91591-ef69-465b-892b-1c82e07744a9'},
        {'source':'Lever','source_id':'baseline-12345678901234567890','company':'Fixture Inc','title':'Fixture','url':'https://jobs.lever.co/fixture/baseline-12345678901234567890'},
    ]
    assert all(candidate(job)==original(job) for job in legacy)
    print('PASS 5: Greenhouse and Lever fingerprints are exactly unchanged')
    assert candidate(BASE) != original(BASE)
    print('PASS 6: Workday has a separate, requisition-based identity')
    print('SUCCESS: Identity-only preview validated; no production files or databases changed.')

if __name__ == '__main__':
    check()
