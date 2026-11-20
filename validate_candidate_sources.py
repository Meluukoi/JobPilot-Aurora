"""Stage 8: standalone read-only ATS compatibility probe.

Never edits sources.csv, jobpilot.db, discovery_history.db, or reports.
Uses existing collectors.fetch_source without running batch scans.
"""
import argparse
from collections import Counter
from pathlib import Path
import csv
import sys

from collectors import fetch_source

CANDIDATES = (
    {'enabled': 'True', 'ats': 'Lever', 'company': 'Lyra Health', 'token_or_site': 'lyrahealth'},
    {'enabled': 'True', 'ats': 'Greenhouse', 'company': 'Upshop', 'token_or_site': 'upshop'},
)
HINTS = ('support', 'service desk', 'help desk', 'customer success', 'onboarding', 'implementation', 'operations coordinator', 'client service', 'it analyst')


def existing_sources(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        return {(str(r.get('ats', '')).casefold(), str(r.get('token_or_site', '')).strip().casefold()) for r in csv.DictReader(f)}


def summarize(jobs):
    if not isinstance(jobs, list):
        raise ValueError('Collector did not return a list')
    incomplete = [j for j in jobs if not all(str(j.get(k) or '').strip() for k in ('company', 'title', 'url', 'source_id'))]
    if incomplete:
        raise ValueError(f'{len(incomplete)} postings missing company/title/url/source_id')
    interesting = [j for j in jobs if any(h in str(j.get('title') or '').casefold() for h in HINTS)]
    return interesting


def main(argv=None):
    p = argparse.ArgumentParser(description='Read-only test of two proposed ATS sources; no JobPilot files modified')
    p.add_argument('--sources', default='sources.csv', help='Existing source catalog, read only')
    p.add_argument('--live', action='store_true', help='Make live requests to these two public ATS boards only')
    a = p.parse_args(argv)
    existing = existing_sources(a.sources)
    for s in CANDIDATES:
        if (s['ats'].casefold(), s['token_or_site'].casefold()) in existing:
            print(f"SKIP already configured: {s['company']}")
            continue
        print(f"CANDIDATE: {s['company']} ({s['ats']}, token/site {s['token_or_site']})")
        if not a.live:
            continue
        try:
            jobs = fetch_source(s, max_age_days=45, verify_first_published=False)
            shortlist = summarize(jobs)
            print(f'  Collector success: {len(jobs)} listings; {len(shortlist)} title-keyword possibilities (NOT eligibility verified)')
            for j in shortlist[:12]:
                print(f"  - {j['title']} | {j.get('location','')} | {j['url']}")
            if not jobs:
                print('  WARNING: zero listings; do not approve this source without further validation')
        except Exception as e:
            print(f'  FAILED: {type(e).__name__}: {e}')
            return 1
    print('No changes made to source catalog, databases, or JobPilot reports.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
