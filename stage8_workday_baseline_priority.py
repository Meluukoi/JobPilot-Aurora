"""Stage 8 isolated Workday baseline-aware enrichment selection.

Offline-only pure selection. Does not alter the existing priority selector.
'new_to_monitoring' is based on a previously established complete employer index,
NOT on Workday posting timestamps.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib


def select_baseline_batch(candidates, now, newly_monitored_ids, limit=5, fresh_quota=3):
    """Select (queue_row, index_entry) and return metrics.

    newly_monitored_ids must be the tracker classification for the same employer,
    index and scan, expressed in the queue's serialized identity representation.
    A job known before baseline completion is never promoted to new-monitoring.
    """
    if now.tzinfo is None:
        raise ValueError('now must be timezone aware')
    now = now.astimezone(timezone.utc)
    if not isinstance(limit, int) or not isinstance(fresh_quota, int) or limit < 0 or fresh_quota < 0:
        raise ValueError('Invalid selection budget')
    if not isinstance(newly_monitored_ids, (list, tuple, set, frozenset)):
        raise ValueError('Expected explicit classified identities')
    fresh_ids = set(newly_monitored_ids)
    if len(fresh_ids) != len(newly_monitored_ids) or not all(isinstance(x,str) and x for x in fresh_ids):
        raise ValueError('Invalid or repeated new identities')
    fresh, backlog = [], []
    seen = set()
    for row, entry in candidates:
        key = row['identity']
        if key in seen:
            raise ValueError('Duplicate queue identity')
        seen.add(key)
        if row['state'] != 'pending':
            continue
        if row.get('next_retry') and datetime.fromisoformat(row['next_retry']).astimezone(timezone.utc) > now:
            continue
        first = datetime.fromisoformat(row['first_seen']).astimezone(timezone.utc)
        if first > now:
            raise ValueError('Future first_seen timestamp')
        if key in fresh_ids:
            fresh.append((row,entry))
        else:
            backlog.append((row,entry))
    if not fresh_ids.issubset(seen):
        raise ValueError('Tracker returned IDs absent from this index')
    digest = lambda pair: hashlib.sha256(pair[0]['identity'].encode('utf-8')).digest()
    fresh.sort(key=digest)
    # Preserve oldest pending coverage, but disperse ties when new baseline jobs
    # all share the same first_seen timestamp; avoid API-order/ID-prefix bias.
    backlog.sort(key=lambda pair: (pair[0]['first_seen'], digest(pair)))
    allowance = min(limit, fresh_quota)
    chosen_new = fresh[:allowance] if backlog else fresh[:limit]
    capacity = limit - len(chosen_new)
    chosen_old = backlog[:capacity]
    remaining = limit - len(chosen_new) - len(chosen_old)
    if remaining:
        chosen_new += fresh[len(chosen_new):len(chosen_new)+remaining]
    selected = chosen_new + chosen_old
    return selected, {
        'due_new_monitoring':len(fresh),
        'due_baseline_or_backlog':len(backlog),
        'selected_new_monitoring':len(chosen_new),
        'selected_baseline_or_backlog':len(chosen_old),
        'remaining_due':len(fresh)+len(backlog)-len(selected),
    }
