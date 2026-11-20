"""Offline regression for Stage 8 Workday malformed-index quarantine.

Loads only the index_only function by AST from the NEW experimental runner.
No imports of JobPilot modules, no HTTP, no SQLite or other file writes.
Run: python -B stage8_workday_quarantine_regression_test.py
"""
import ast
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import re
import sys

SOURCE = Path(__file__).with_name('stage8_workday_live_runner_freshness_quarantine_guarded.py')
function = next(n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                if isinstance(n, ast.FunctionDef) and n.name == 'index_only')
module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))

# Test-only replacements of upstream ID utilities, not monkey-patches in JobPilot.
def safe_path(path):
    return isinstance(path, str) and bool(re.fullmatch(r'/job/[A-Za-z0-9-]+/[A-Za-z0-9-]+_R\d+', path))

def req_from_path(path):
    match = re.search(r'_R\d+$', path)
    return match.group()[1:] if match else None

def workday_key(record, registry):
    return f"workday|{record['company']}|{record['source_id']}" if record['source_id'] else None

helpers = ModuleType('stage8_workday_identity')
helpers.workday_key = workday_key
scope = {'PAGE_SIZE': 3, 'safe_path': safe_path,
         'req_from_path': req_from_path, 'REGISTRY': {}}
exec(compile(module, str(SOURCE), 'exec'), scope)
index_only = scope['index_only']
EMPLOYER = SimpleNamespace(slug='crowdstrike', company='CrowdStrike',
                           api='https://fake.example.com', base='https://fake.example.com',
                           board='careers')

def row(number, title='Technical Support', path=None):
    return {'externalPath':path if path is not None else f'/job/USA-Remote/{title.replace(" ", "-")}_R{number}',
            'title':title,'locationsText':'USA - Remote'}

class Client:
    def __init__(self, pages):
        self.pages=pages
        self.used=0
    def get_json(self, endpoint, payload):
        assert endpoint.endswith('/jobs')
        assert payload['limit']==3
        page=payload['offset']//3
        self.used+=1
        return self.pages[page]

def run(pages):
    with patch.dict(sys.modules, {'stage8_workday_identity':helpers}):
        c=Client(pages)
        result=index_only(EMPLOYER,c,len(pages))
        assert c.used==len(pages) or len(pages)==1
        return result

def expect_error(pages, needle):
    with patch.dict(sys.modules, {'stage8_workday_identity':helpers}):
        try:
            index_only(EMPLOYER,Client(pages),len(pages))
        except ValueError as exc:
            assert needle in str(exc),str(exc)
        else:
            raise AssertionError(f'Expected fail-closed: {needle}')

# Full small catalog, no error.
r=run([{'total':2,'jobPostings':[row(100),row(101)]}])
assert r['index_complete'] and not r['quarantined'] and len(r['index'])==2
print('PASS 1: Valid complete index remains complete')

# Exact observed CrowdStrike failure: missing path on page index 1.
r=run([{'total':5,'jobPostings':[row(100),row(101),row(102)]},
       {'total':5,'jobPostings':[{'externalPath':None,'title':'Malformed','locationsText':'Remote'},row(103)]}])
assert not r['index_complete'] and len(r['index'])==4 and r['reported_total']==5
assert r['quarantined'][0]['offset']==3 and 'externalPath' in r['quarantined'][0]['reason']
assert any('NEVER mark missing jobs' in w for w in r['warnings'])
print('PASS 2: Missing externalPath quarantined; other jobs retained; index incomplete')

# A genuinely malformed raw row is also not imported.
r=run([{'total':2,'jobPostings':[row(200),None]}])
assert len(r['index'])==1 and r['quarantined'][0]['reason']=='non-dictionary listing'
print('PASS 3: Non-dictionary listing quarantined without invented identity')

# Safe-looking path with absent requisition is quarantined, not assigned synthetic ID.
with patch.dict(sys.modules, {'stage8_workday_identity':helpers}):
    with patch.dict(scope, {'req_from_path':lambda p: None}):
        r=index_only(EMPLOYER,Client([{'total':1,'jobPostings':[row(300)]}]),1)
assert not r['index_complete'] and len(r['index'])==0
assert r['quarantined'][0]['reason']=='missing or untrusted requisition ID'
print('PASS 4: Missing requisition quarantined, no fabricated ID')

# Exact duplicate paths must still fail the entire employer, never dedupe silently.
expect_error([{'total':2,'jobPostings':[row(400),row(400)]}], 'duplicate externalPath')
print('PASS 5: Duplicate externalPath fails closed')

# Different paths encoding the same requisition cannot be collapsed.
expect_error([{'total':2,'jobPostings':[row(500),row(500,'Other Title')]}], 'duplicate requisition identity')
print('PASS 6: Requisition-ID collision fails closed')

# First-page count and short page must both be correct for completeness.
r=run([{'total':3,'jobPostings':[row(600),row(601)]}])
assert not r['index_complete'] and r['reported_total']==3
print('PASS 7: Catalog count mismatch remains incomplete')

# Later-page 'total' drift is flagged while first-page count remains authoritative.
r=run([{'total':4,'jobPostings':[row(700),row(701),row(702)]},
       {'total':2,'jobPostings':[row(703)]}])
assert r['index_complete'] and 'Later-page total changed' in r['warnings']
print('PASS 8: Later-page total anomaly reported, not trusted as catalog count')

print('SUCCESS: 8 offline malformed-index regression checks; no HTTP, SQLite, SMTP, scheduler or production changes')
