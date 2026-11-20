"""Stage 8 Workday-only enrichment selection. Pure, offline, no file/network IO.

Balances newly indexed jobs with older due backlog, without title or geography filters.
All candidates must already have passed queue retry eligibility checks.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib


def select_batch(candidates, now, limit=5, fresh_quota=3):
    """Return (selected, metrics).

    candidates: list of (queue_row, index_entry) pairs. `first_seen` and
    `next_retry` are ISO UTC timestamps. Only `state='pending'` may qualify.
    We never discard unselected jobs; they're retained by the SQLite queue.
    On initial baseline (all fresh), spread selection over the index instead
    of taking its first N arbitrary API rows.
    """
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    now = now.astimezone(timezone.utc)
    fresh, backlog = [], []
    for row, entry in candidates:
        if row['state'] != 'pending':
            continue
        if row.get('next_retry') and datetime.fromisoformat(row['next_retry']) > now:
            continue
        first = datetime.fromisoformat(row['first_seen']).astimezone(timezone.utc)
        if first > now:
            raise ValueError('Future first_seen timestamp')
        # Queue import stamps brand-new records at run's current time.
        (fresh if first == now else backlog).append((row, entry))

    # Old backlog: oldest first; ties broken by stable identity.
    backlog.sort(key=lambda x: (x[0]['first_seen'], x[0]['identity']))
    # New posts: deterministic, dispersed through source index; not title-based.
    # This intentionally avoids bias to the earliest Workday API rows.
    fresh.sort(key=lambda x: hashlib.sha256(x[0]['identity'].encode()).digest())

    fresh_quota = min(fresh_quota, limit)
    chosen_fresh = fresh[:fresh_quota] if backlog else fresh[:limit]
    left = limit - len(chosen_fresh)
    chosen_old = backlog[:left]
    if len(chosen_old) < left:
        chosen_fresh += fresh[len(chosen_fresh):len(chosen_fresh) + left - len(chosen_old)]
    selected = chosen_fresh + chosen_old
    return selected, {
        'due_new': len(fresh), 'due_backlog': len(backlog),
        'selected_new': len(chosen_fresh), 'selected_backlog': len(chosen_old),
        'remaining_due': len(fresh) + len(backlog) - len(selected),
    }
