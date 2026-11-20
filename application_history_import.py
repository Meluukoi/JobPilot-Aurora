"""JobPilot Stage 6B: cautious tracker import with repeatable plans and SQLite backup.

No network or mailbox access. The default --plan path NEVER writes to jobpilot.db.
Apply requires a matching plan token; always backs up database using SQLite's API.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from application_history_preview import read_excel, read_saved, reconcile, url_identity, name

ALLOWED = {'Applied', 'Rejected'}
CONFIRMED_CONFLICT = ('higherlogic', 'technical support specialist', 'Rejected')


def make_plan(tracker, database):
    tracker, database = Path(tracker), Path(database)
    if not tracker.is_file() or not database.is_file():
        raise FileNotFoundError('Tracker spreadsheet and existing JobPilot database must both exist')
    data = read_excel(tracker)
    existing = read_saved(database)
    reviewed = reconcile(data, existing)
    inserts, updates, skipped = [], [], []
    for j, r in zip(data, reviewed):
        status = str(j.get('Status') or '').strip()
        key = url_identity(j.get('Job URL'))
        row = dict(row=j.get('_row'), company=j.get('Company',''), title=j.get('Job Title',''),
                   status=status, url=j.get('Job URL',''), added_date=j.get('Added Date',''))
        if status not in ALLOWED:
            skipped.append(dict(row, reason='Unsupported tracker status'))
        elif r['action'] == 'Candidate for future import (NOT imported)' and key:
            inserts.append(row)
        elif r['action'] == 'Status differs — manual review':
            if (name(row['company']).replace(' ',''), name(row['title']), status) == CONFIRMED_CONFLICT and key:
                updates.append(row)
            else:
                skipped.append(dict(row, reason='Unconfirmed status difference'))
        else:
            skipped.append(dict(row, reason=r['action']))
    # Schema safety and current snapshot bind every plan to the actual DB state.
    with closing(sqlite3.connect(f'file:{database.resolve().as_posix()}?mode=ro', uri=True)) as c:
        schema = {x[1] for x in c.execute('PRAGMA table_info(jobs)')}
        if not {'id','company','title','url','status','notes'}.issubset(schema):
            raise ValueError('Unexpected jobs database schema; refusing import')
        db_records = c.execute('SELECT id, company, title, url, status FROM jobs ORDER BY id').fetchall()
    identity = json.dumps(dict(inserts=inserts,updates=updates,db_rows=db_records), sort_keys=True, default=str)
    token = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
    return dict(token=token, tracker_rows=len(data), db_rows=len(existing), inserts=inserts, updates=updates,
                skipped=skipped, counts=dict(Counter(r['reason'] for r in skipped)))


def apply_plan(tracker, database, expected):
    database = Path(database)
    plan = make_plan(tracker, database)
    if expected != plan['token']:
        raise ValueError('Confirmation token differs from current plan. Run --plan again; nothing changed.')
    if not plan['inserts'] and not plan['updates']:
        return None, 0, 0
    # The real project cannot be read by a running scanner during writes.
    # SQLite online-backup API captures a consistent DB and commits atomically.
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = database.with_name(f'{database.stem}_pre_stage6b_{ts}.db')
    if backup.exists():
        raise FileExistsError('Refusing to replace an existing backup')
    with closing(sqlite3.connect(str(database), timeout=15)) as conn:
        with closing(sqlite3.connect(str(backup))) as dest:
            conn.backup(dest)
        try:
            conn.execute('BEGIN IMMEDIATE')
            # Recheck currently tracked identities to avoid overwriting changed records.
            current = [dict(zip(('company','title','status','url'), x)) for x in
                       conn.execute('SELECT company,title,status,url FROM jobs')]
            live = reconcile(read_excel(Path(tracker)), current)
            actions = {int(r['export_row']): r['action'] for r in live}
            for item in plan['inserts']:
                if actions.get(int(item['row'])) != 'Candidate for future import (NOT imported)':
                    raise ValueError('Saved jobs changed during import; rolled back')
                url = item['url']
                # Existing different URL with same title is not necessarily same opening,
                # but Stage6A has conservatively excluded possible company/title matches.
                cur = conn.execute('INSERT INTO jobs (source,company,title,url,status,notes,score,recommendation) '
                                   'VALUES (?,?,?,?,?,?,0,?)',
                                   ('RabbitResume tracker',item['company'],item['title'],url,item['status'],
                                    f"Imported from RabbitResume tracker; added date (not application date): {item['added_date']}",
                                    'External application history'))
                if cur.rowcount != 1: raise ValueError('Insert failed; rolled back')
            for item in plan['updates']:
                if actions.get(int(item['row'])) != 'Status differs — manual review':
                    raise ValueError('Status changed before update; rolled back')
                match = [r for r in current if url_identity(r['url']) == url_identity(item['url'])]
                if len(match) != 1 or match[0]['status'] != 'Applied':
                    raise ValueError('Higher Logic status is no longer Applied; rolled back')
                cur = conn.execute('UPDATE jobs SET status=? WHERE url=? AND status=?',
                                   ('Rejected',match[0]['url'],'Applied'))
                if cur.rowcount != 1: raise ValueError('Higher Logic update not unique; rolled back')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return backup, len(plan['inserts']),len(plan['updates'])


def main(argv=None):
    p=argparse.ArgumentParser(description='Stage 6B import — preview by default, backup and explicit confirmation required for writes')
    p.add_argument('--tracker', required=True)
    p.add_argument('--db', default='jobpilot.db')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--plan', action='store_true', help='read-only plan, default safe first step')
    g.add_argument('--apply', action='store_true', help='apply only with confirmation token and backup')
    p.add_argument('--confirm', help='16-digit hex plan token from current --plan')
    args=p.parse_args(argv)
    try:
        if args.plan:
            plan=make_plan(args.tracker,args.db)
            print(f"PREVIEW ONLY: {plan['tracker_rows']} tracker records, {plan['db_rows']} saved in JobPilot.")
            print(f"Proposed: {len(plan['inserts'])} new records, {len(plan['updates'])} confirmed status update(s), {len(plan['skipped'])} skipped/manual review.")
            for reason,num in sorted(plan['counts'].items()): print(f'  {num:3d} {reason}')
            print('Higher Logic planned update:', 'Applied -> Rejected' if plan['updates'] else 'none')
            print('Plan token:',plan['token'])
            print('Database NOT modified. Do not run --apply until you have reviewed this plan.')
        else:
            if not args.confirm or not re.fullmatch('[0-9a-f]{16}',args.confirm):
                p.error('--apply requires the exact 16-character --confirm token from --plan')
            backup,n,m=apply_plan(args.tracker,args.db,args.confirm)
            print(f'Success: {n} records imported, {m} confirmed status updates. Backup: {backup}')
        return 0
    except (OSError,ValueError,sqlite3.Error) as exc:
        print('Stage 6B stopped safely:',exc)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
