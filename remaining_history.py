"""JobPilot Stage 6B follow-up: preserve unmatched RabbitResume records in a separate ledger.

Does not alter jobs, scoring, email eligibility or recommendation filtering.
Uses existing Stage6A preview CSV. A --plan is read-only; --apply requires fresh plan token.
"""
from __future__ import annotations
import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

TARGET_ACTIONS = {'Manual review — no usable URL', 'Review tracker duplicates before any import'}
ALLOWED_STATUSES = {'Applied', 'Rejected'}
TABLE_SQL = '''CREATE TABLE IF NOT EXISTS rabbitresume_unmatched_history (
    record_key TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    added_date TEXT,
    job_url TEXT,
    source_export_row INTEGER,
    reason TEXT NOT NULL,
    first_imported_utc TEXT NOT NULL,
    last_updated_utc TEXT NOT NULL
)'''


def _normal(s):
    return ' '.join(str(s or '').lower().strip().split())


def _load_preview(path):
    with Path(path).open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'export_row', 'company', 'title', 'external_status', 'added_date', 'job_url', 'action'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Not a recognized Stage 6A preview CSV')
        rows = list(reader)
    if not rows:
        raise ValueError('Preview report is empty')
    return rows


def _records(rows):
    # Exact repeated content gets a stable occurrence number, so duplicates stay visible.
    occurrence = {}
    result = []
    for row in rows:
        if row['action'] not in TARGET_ACTIONS:
            continue
        status = row['external_status'].strip()
        company, title = row['company'].strip(), row['title'].strip()
        if status not in ALLOWED_STATUSES or not company or not title:
            raise ValueError('Missing or unsupported company/title/status in unmatched history')
        fingerprint = [_normal(company), _normal(title), _normal(row['added_date']),
                       _normal(row['job_url'])]
        ident = json.dumps(fingerprint, separators=(',', ':'), ensure_ascii=False)
        occurrence[ident] = occurrence.get(ident, 0) + 1
        key = hashlib.sha256((ident + '#' + str(occurrence[ident])).encode()).hexdigest()
        result.append(dict(record_key=key, company=company, title=title, status=status,
                           added_date=row['added_date'], job_url=row['job_url'],
                           source_export_row=int(row['export_row']), reason=row['action']))
    return result


def _existing(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rabbitresume_unmatched_history'").fetchone()
    if not exists:
        return {}
    return {r[0]: r[1] for r in conn.execute('SELECT record_key,status FROM rabbitresume_unmatched_history')}


def _db_snapshot(conn):
    # Detect out-of-band changes to both existing jobs and ledger between plan and apply.
    jobs = conn.execute('SELECT id,company,title,url,status FROM jobs ORDER BY id').fetchall()
    ledger_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rabbitresume_unmatched_history'").fetchone()
    ledger = conn.execute('SELECT record_key,company,title,status,added_date,job_url,source_export_row,reason FROM rabbitresume_unmatched_history ORDER BY record_key').fetchall() if ledger_exists else []
    return (jobs, ledger)


def plan(preview, db):
    preview, db = Path(preview), Path(db)
    if not preview.is_file() or not db.is_file():
        raise FileNotFoundError('Existing preview CSV and JobPilot database are required')
    rows = _load_preview(preview)
    records = _records(rows)
    with closing(sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro', uri=True)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
            raise ValueError('JobPilot jobs table missing')
        snapshot = _db_snapshot(c)
        old = _existing(c)
    inserts = [r for r in records if r['record_key'] not in old]
    updates = [r for r in records if r['record_key'] in old and old[r['record_key']] != r['status']]
    unchanged = len(records) - len(inserts) - len(updates)
    # Hash all source content and database snapshot (not the timestamp or SQLite file metadata).
    payload = json.dumps(dict(csv_sha256=hashlib.sha256(preview.read_bytes()).hexdigest(),
                              db=snapshot, proposed=records), default=str, ensure_ascii=False, sort_keys=True)
    token = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return dict(token=token, source_rows=len(rows), unmatched=len(records), inserts=inserts,
                updates=updates, unchanged=unchanged,
                missing_urls=sum(r['reason']=='Manual review — no usable URL' for r in records),
                duplicate_flags=sum(r['reason']=='Review tracker duplicates before any import' for r in records))


def apply(preview, db, confirmation):
    db = Path(db)
    p = plan(preview, db)
    if confirmation != p['token']:
        raise ValueError('Plan token does not match. Run --plan again; nothing changed.')
    if not p['inserts'] and not p['updates']:
        return None, 0, 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = db.with_name(f'{db.stem}_pre_unmatched_{stamp}.db')
    if backup.exists():
        raise FileExistsError(f'Backup already exists: {backup}')
    with closing(sqlite3.connect(db, timeout=15)) as c:
        with closing(sqlite3.connect(backup)) as dest:
            c.backup(dest)
        try:
            c.execute('BEGIN IMMEDIATE')
            c.execute(TABLE_SQL)
            now = datetime.now(timezone.utc).isoformat(timespec='seconds')
            for r in p['inserts']:
                c.execute('INSERT INTO rabbitresume_unmatched_history VALUES (?,?,?,?,?,?,?,?,?,?)',
                          (r['record_key'],r['company'],r['title'],r['status'],r['added_date'],
                           r['job_url'],r['source_export_row'],r['reason'],now,now))
            for r in p['updates']:
                count = c.execute('UPDATE rabbitresume_unmatched_history SET status=?,last_updated_utc=? WHERE record_key=?',
                                  (r['status'],now,r['record_key'])).rowcount
                if count != 1:
                    raise ValueError('Unexpected update count; rolled back')
            c.commit()
        except Exception:
            c.rollback()
            raise
    return backup, len(p['inserts']), len(p['updates'])


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read-only preview / backed-up import of unmatched RabbitResume history')
    ap.add_argument('--preview', default='application_history_preview.csv')
    ap.add_argument('--db', default='jobpilot.db')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm')
    args = ap.parse_args(argv)
    try:
        if args.plan:
            p = plan(args.preview,args.db)
            print(f"READ-ONLY PLAN: {p['source_rows']} source records; {p['unmatched']} unmatched records ({p['missing_urls']} no URL, {p['duplicate_flags']} duplicate-flagged).")
            print(f"Separate history ledger: {len(p['inserts'])} new, {len(p['updates'])} status updates, {p['unchanged']} unchanged.")
            print('No records will be added to the jobs table or filtering. No database changes in plan mode.')
            print('Plan token:',p['token'])
        else:
            if not args.confirm or not re.fullmatch(r'[a-f0-9]{16}',args.confirm):
                ap.error('--apply requires --confirm with the current plan token')
            backup,n,u = apply(args.preview,args.db,args.confirm)
            print(f'Ledger saved: {n} new records, {u} status updates. Backup: {backup}')
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print('Stopped safely:',exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
