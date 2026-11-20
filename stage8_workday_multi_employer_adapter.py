"""JobPilot Stage 8: read-only, opt-in, multi-employer Workday adapter.

Standalone testing-copy preview. Does not import or mutate JobPilot's collectors,
scheduler, application tracker, discovery history, or SQLite queue. Index rows
are NEVER title-filtered or silently dropped. No files are written.

Offline acceptance: python -B stage8_workday_multi_employer_test.py
Optional bounded live sample (only if explicitly requested later):
  python -B stage8_workday_multi_employer_adapter.py --live --employer crowdstrike
"""
from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from stage8_workday_identity import workday_key

PAGE_SIZE = 20
MAX_PAGES = 25            # Bounded full-index cap: at most 500 indexed jobs
MAX_DETAILS = 2           # Strict detail cap
MAX_BYTES = 6 * 1024 * 1024
_REQ = re.compile(r"_([A-Za-z]+\d+)(?:-\d+)?$", re.I)

@dataclass(frozen=True)
class Employer:
    slug: str
    company: str
    tenant: str
    board: str
    host: str

    @property
    def base(self):
        return f"https://{self.host}"

    @property
    def api(self):
        return f"{self.base}/wday/cxs/{self.tenant}/{self.board}"

    @property
    def registry(self):
        return {(self.tenant.lower(), self.board.lower()): re.sub(r"[^a-z0-9]", "", self.company.lower())}

EMPLOYERS = {
    "crowdstrike": Employer("crowdstrike", "CrowdStrike", "crowdstrike", "crowdstrikecareers", "crowdstrike.wd5.myworkdayjobs.com"),
    "boozallen": Employer("boozallen", "Booz Allen Hamilton", "bah", "BAH_Jobs", "bah.wd1.myworkdayjobs.com"),
}


def safe_path(path):
    return (isinstance(path, str) and path.startswith('/job/') and not path.startswith('//')
            and '?' not in path and '#' not in path and '\\' not in path
            and all(part not in ('', '.', '..') for part in path[1:].split('/')))


def req_from_path(path):
    match = _REQ.search(path.rsplit('/', 1)[-1]) if safe_path(path) else None
    return match.group(1).upper() if match else None


def clean_html(raw):
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', str(raw or '')))).strip()


def meaningful_description(text):
    return (len(text) >= 100 and bool(re.search(
        r'\b(responsibilities|requirements|qualifications|experience|skills|duties|you will)\b', text, re.I)))


class PublicClient:
    """Secure, single-approved-host HTTP client with total request budget."""
    def __init__(self, employer, budget):
        import truststore  # optional dependency used only when live mode is requested
        self.employer = employer
        self.budget = budget
        self.used = 0
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                raise ValueError('Unexpected HTTP redirect; request not followed')
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
            NoRedirect())

    def get_json(self, url, payload=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != self.employer.host
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.query or parsed.fragment):
            raise ValueError('Request blocked: not the approved HTTPS employer host')
        if self.used >= self.budget:
            raise RuntimeError('Request limit reached')
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {'Accept': 'application/json', 'User-Agent': 'JobPilot-Stage8-WorkdayReadOnly/1.0'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if data is not None else 'GET')
        self.used += 1
        with self.opener.open(req, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Oversized response')
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError('Expected JSON object')
        return obj


def collect_preview(employer, client, *, max_pages=2, max_details=2, detail_eligible=None):
    """Return complete sampled index in memory, with bounded ready records.

    Fails explicitly on untrustworthy identity or incomplete index structure.
    This *sample* may be partial; partial scans must never be used as closure evidence.
    """
    if not isinstance(employer, Employer) or EMPLOYERS.get(employer.slug) != employer:
        raise ValueError('Unapproved employer configuration')
    if not (1 <= max_pages <= MAX_PAGES and 0 <= max_details <= MAX_DETAILS):
        raise ValueError('Unsafe request limits')
    entries = []
    seen_paths = set()
    ids_to_paths = {}
    warnings = []
    total = None
    short_page = False
    for page in range(max_pages):
        payload = {'appliedFacets': {}, 'limit': PAGE_SIZE, 'offset': page * PAGE_SIZE, 'searchText': ''}
        data = client.get_json(employer.api + '/jobs', payload)
        rows = data.get('jobPostings')
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError('Invalid index response: missing/oversize jobPostings')
        if page == 0:
            total = data.get('total')
            if type(total) is not int or total < 0:
                raise ValueError('Missing first-page total')
        elif data.get('total') != total:
            warnings.append('Later-page total inconsistent; ignored for pagination')
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid listing record')
            path = row.get('externalPath')
            if not safe_path(path) or path in seen_paths:
                raise ValueError('Unsafe or duplicate official job path')
            seen_paths.add(path)
            rid = req_from_path(path)
            record = {'source': 'Workday', 'source_id': rid, 'company': employer.company,
                      'title': str(row.get('title') or '').strip(),
                      'location_summary': str(row.get('locationsText') or '').strip(),
                      'url': employer.base + '/' + employer.board + path,
                      'external_path': path, 'enrichment_status': 'pending',
                      'score_ready': False, 'job_record': None, 'error': None,
                      'identity': None}
            if rid:
                record['identity'] = workday_key(record, employer.registry)
            if record['identity'] is None:
                record['error'] = 'Untrusted/missing requisition identity; cannot score or hand off'
                record['enrichment_status'] = 'error'
            else:
                ids_to_paths.setdefault(record['identity'], []).append(path)
            entries.append(record)
        if len(rows) < PAGE_SIZE:
            short_page = True
            break

    collisions = {key: paths for key, paths in ids_to_paths.items() if len(paths) > 1}
    collision_keys = set(collisions)
    for entry in entries:
        if entry['identity'] in collision_keys:
            entry['enrichment_status'] = 'error'
            entry['error'] = 'Identity collision across different official paths; hold for review'
    if collisions:
        warnings.append(f'{len(collisions)} requisition-ID collisions: records retained, not merged')

    # Sampling only; never use a job title or location to choose enrichment candidates.
    # Fetch from the beginning of the indexed listing order, skipping unsafe IDs.
    detail_candidates = []
    for entry in entries:
        if entry['enrichment_status'] != 'pending':
            continue
        if detail_eligible is not None and not detail_eligible(entry):
            continue
        detail_candidates.append(entry)
        if len(detail_candidates) >= max_details:
            break

    if max_details == 0:
        detail_candidates = []
    for entry in detail_candidates:
        try:
            info = client.get_json(employer.api + entry['external_path']).get('jobPostingInfo')
            if not isinstance(info, dict):
                raise ValueError('Missing jobPostingInfo')
            req = str(info.get('jobReqId') or '').strip().upper()
            if req != entry['source_id']:
                raise ValueError('Index/detail requisition-ID mismatch')
            description = clean_html(info.get('jobDescription'))
            locations = info.get('additionalLocations') or []
            if isinstance(locations, str):
                locations = [locations]
            if not isinstance(locations, list):
                raise ValueError('Malformed additionalLocations')
            locs = list(dict.fromkeys(str(loc).strip() for loc in [info.get('location'), *locations] if loc and str(loc).strip()))
            title = str(info.get('title') or entry['title']).strip()
            if not title or not locs or not meaningful_description(description):
                raise ValueError('Incomplete or non-meaningful job information')
            record = {'source': 'Workday', 'source_id': req, 'company': employer.company,
                      'title': title, 'location': ' | '.join(locs), 'url': entry['url'],
                      'description': description, 'posted_at': 'Unknown'}
            if workday_key(record, employer.registry) != entry['identity']:
                raise ValueError('Untrusted detail record identity')
            entry.update(enrichment_status='ready', score_ready=True, job_record=record)
        except Exception as exc:
            entry.update(enrichment_status='error', error=f'{type(exc).__name__}: {str(exc)[:140]}')

    complete = short_page and len(entries) == total
    if not complete:
        warnings.append(f'PARTIAL INDEX: {len(entries)}/{total} (not suitable for missing-job decisions)')
    return {'employer': employer.slug, 'index': entries, 'ready_records': [e['job_record'] for e in entries if e['score_ready']],
            'reported_total': total, 'index_complete': complete, 'warnings': warnings,
            'collisions': collisions}


def main():
    parser = argparse.ArgumentParser(description='Stage 8 standalone multi-employer Workday preview')
    parser.add_argument('--live', action='store_true', help='Opt in to bounded HTTPS requests; otherwise no requests')
    parser.add_argument('--employer', choices=sorted(EMPLOYERS), required=True)
    args = parser.parse_args()
    if not args.live:
        print('OFFLINE DEFAULT: no requests made. Run companion test for synthetic checks.')
        return 0
    employer = EMPLOYERS[args.employer]
    client = PublicClient(employer, MAX_PAGES + MAX_DETAILS)
    result = collect_preview(employer, client)
    counts = {status: sum(e['enrichment_status'] == status for e in result['index']) for status in ('ready', 'pending', 'error')}
    print(f"Employer: {employer.company}; indexed: {len(result['index'])}/{result['reported_total']}; index_complete={result['index_complete']}")
    print('Enrichment:', counts, 'Requests:', client.used, '/', client.budget)
    print('Warnings:', *result['warnings'], sep='\n - ')
    print('NO files, databases, JobPilot modules, emails, or settings changed.')
    return 0 if not any(e['enrichment_status'] == 'error' for e in result['index']) else 2

if __name__ == '__main__':
    raise SystemExit(main())
