"""JobPilot Stage 6A: read-only external application-history reconciliation.

No JobPilot database writes, no imports, no application submissions, no network.
Uses Python's standard library so no extra packages are required on Windows.
The Excel export is read directly; report CSV is local and may contain personal history.
"""
from __future__ import annotations
import argparse
import csv
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit, parse_qs, unquote
import xml.etree.ElementTree as ET
from zipfile import ZipFile

MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
REQUIRED = ('Job Title', 'Company', 'Status', 'Job URL', 'Added Date')
REPORT_FIELDS = ('export_row', 'company', 'title', 'external_status', 'added_date', 'job_url', 'match_result', 'jobpilot_status', 'jobpilot_url', 'action', 'details')


def clean(x):
    return re.sub(r'\s+', ' ', str(x or '').strip()).casefold()


def name(x):
    return re.sub(r'[^a-z0-9]+', ' ', clean(x)).strip()


def url_identity(value):
    value = str(value or '').strip()
    if not value:
        return None
    try:
        x = urlsplit(value)
        host = (x.hostname or '').lower().removeprefix('www.')
        path = unquote(x.path).rstrip('/').lower()
        if not host or x.scheme.lower() not in ('https', 'http'):
            return None
        q = parse_qs(x.query)
    except ValueError:
        return None
    if host.endswith('greenhouse.io'):
        gh = re.search(r'/jobs/(\d+)(?:/|$)', path)
        if gh:
            return ('greenhouse', gh.group(1))
        for k in ('gh_jid', 'job_id'):
            if q.get(k) and q[k][0].isdigit():
                return ('greenhouse', q[k][0])
    if host.endswith('lever.co'):
        m = re.search(r'^/([^/]+)/([a-f0-9-]{30,})(?:/|$)', path)
        if m:
            return ('lever', m.group(1), m.group(2))
    meaningful = tuple(sorted((k.lower(), v[0].lower()) for k,v in q.items()
                              if k.lower() in {'id','jobid','job_id','reqid','req_id'} and v))
    return ('url', host, path, meaningful)


def _text(cell, shared):
    typ = cell.get('t')
    if typ == 'inlineStr':
        return ''.join(n.text or '' for n in cell.iter(MAIN+'t'))
    val = cell.find(MAIN+'v')
    if val is None:
        return ''
    if typ == 's':
        idx = int(val.text)
        return shared[idx]
    return val.text or ''


def read_excel(path):
    """Read first worksheet data from a conventional XLSX without Excel/openpyxl."""
    with ZipFile(path) as z:
        ss = []
        if 'xl/sharedStrings.xml' in z.namelist():
            sr = ET.fromstring(z.read('xl/sharedStrings.xml'))
            ss = [''.join(t.text or '' for t in si.iter(MAIN+'t')) for si in sr.findall(MAIN+'si')]
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = wb.find(MAIN+'sheets')
        if sheets is None or not list(sheets):
            raise ValueError('Spreadsheet has no worksheets')
        preferred = next((s for s in sheets if s.get('name') == 'Jobs'), list(sheets)[0])
        rid = preferred.get(REL+'id')
        rel_root = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        mapping = {r.get('Id'): r.get('Target') for r in rel_root.findall(PKG_REL+'Relationship')}
        target = mapping.get(rid)
        if not target:
            raise ValueError('Cannot locate selected worksheet')
        sheetpath = target.lstrip('/') if target.startswith('/') else 'xl/' + target.lstrip('/')
        sheetpath = sheetpath.replace('xl/xl/', 'xl/')
        root = ET.fromstring(z.read(sheetpath))
        rows = []
        for r in root.findall('.//'+MAIN+'sheetData/'+MAIN+'row'):
            d = {}
            for c in r.findall(MAIN+'c'):
                cell_ref = c.get('r','')
                m = re.match(r'([A-Z]+)',cell_ref)
                if not m: continue
                n=0
                for char in m.group(1): n=n*26 + (ord(char)-64)
                d[n-1] = _text(c,ss)
            rows.append((int(r.get('r') or len(rows)+1),d))
    if not rows: raise ValueError('Spreadsheet has no populated rows')
    headers = [rows[0][1].get(i,'') for i in range(max(rows[0][1],default=-1)+1)]
    missing = set(REQUIRED)-set(headers)
    if missing: raise ValueError('Missing tracker columns: '+', '.join(sorted(missing)))
    out=[]
    for n,d in rows[1:]:
        item={headers[i]:str(v or '').strip() for i,v in d.items() if i<len(headers)}
        if item.get('Job Title') or item.get('Company'):
            item['_row']=n
            out.append(item)
    return out


def read_saved(db_path):
    if not db_path.is_file(): raise FileNotFoundError(f'Missing JobPilot database: {db_path}')
    uri='file:'+db_path.resolve().as_posix()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True,timeout=10)) as con:
        con.row_factory=sqlite3.Row
        return [dict(x) for x in con.execute('SELECT company,title,status,url FROM jobs')]


def _csv_text(v):
    s=str(v if v is not None else '')
    if s.lstrip().startswith(('=','+','-','@')) or s[:1] in ('\t','\n','\r'):
        return "'"+s
    return s


def reconcile(external, saved):
    byurl=defaultdict(list); bytitle=defaultdict(list)
    for j in saved:
        key=url_identity(j.get('url'))
        if key: byurl[key].append(j)
        t=(name(j.get('company')),name(j.get('title')))
        if all(t): bytitle[t].append(j)
    tracker_keys=defaultdict(list)
    for idx,j in enumerate(external):
        key=url_identity(j.get('Job URL'))
        t=(name(j.get('Company')),name(j.get('Job Title')))
        tracker_keys[('url',key) if key else ('title',t)].append(idx)
    report=[]
    for idx,j in enumerate(external):
        url=j.get('Job URL',''); key=url_identity(url)
        t=(name(j.get('Company')),name(j.get('Job Title')))
        isdup=len(tracker_keys[('url',key) if key else ('title',t)])>1
        match=''; existing=None; detail=''
        exact=byurl.get(key,[]) if key else []
        possible=bytitle.get(t,[]) if all(t) else []
        if len(exact)==1:
            existing=exact[0]; match='Exact URL identity'; detail='Strongest identifier match; still confirm status if different.'
        elif len(exact)>1:
            match='Ambiguous database URL'; detail=f'{len(exact)} saved JobPilot records share job identity.'
        elif possible:
            match='Possible company/title match'; detail=f'{len(possible)} existing JobPilot row(s); no matching job URL. Different vacancies may share names.'
        else:
            match='Not found in JobPilot'; detail='No existing matching job URL or company/title.'
        if isdup:
            detail += ' Duplicate external tracker identity; review records together.'
        if existing:
            s=clean(existing.get('status'))
            ext=clean(j.get('Status'))
            action='Already tracked — no change' if s==ext else 'Status differs — manual review'
        elif match.startswith('Ambiguous') or match.startswith('Possible'):
            action='Manual review — do not auto-merge'
        elif isdup:
            action='Review tracker duplicates before any import'
        elif not key:
            action='Manual review — no usable URL'
        else:
            action='Candidate for future import (NOT imported)'
        report.append(dict(export_row=j.get('_row',''),company=j.get('Company',''),title=j.get('Job Title',''),
                           external_status=j.get('Status',''),added_date=j.get('Added Date',''),job_url=url,
                           match_result=match,jobpilot_status=existing.get('status','') if existing else '',
                           jobpilot_url=existing.get('url','') if existing else '',action=action,details=detail))
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Preview external job tracker reconciliation; never writes JobPilot DB')
    p.add_argument('--tracker',required=True,help='Path to job-tracker-export.xlsx')
    p.add_argument('--db',default='jobpilot.db',help='JobPilot DB path, read-only (default ./jobpilot.db)')
    p.add_argument('--output',default='monitoring_reports/application_history_preview.csv')
    args=p.parse_args(argv)
    tracker=Path(args.tracker); db=Path(args.db); dest=Path(args.output)
    if not tracker.is_file(): p.error(f'Tracker not found: {tracker}')
    try:
        records=read_excel(tracker)
        saved=read_saved(db)
        report=reconcile(records,saved)
        counts=Counter(r['action'] for r in report)
        print(f'Preview only: {len(records)} tracker records; {len(saved)} JobPilot saved rows; no imports or DB changes.')
        print('External statuses:',dict(Counter(str(j.get('Status') or 'Blank') for j in records)))
        for k,v in counts.most_common(): print(f'  {v:3d}  {k}')
        dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=REPORT_FIELDS); w.writeheader()
            for row in report: w.writerow({k:_csv_text(row.get(k,'')) for k in REPORT_FIELDS})
        print('Preview report:',dest.resolve())
        print('NO JobPilot database rows were inserted, updated, or deleted.')
        return 0
    except (OSError,ValueError,KeyError,ET.ParseError,sqlite3.Error) as exc:
        print('Preview failed:',str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
