"""Stage 8 testing-only adaptive Workday detail-request prioritizer.

Index-only triage. NEVER decides alert eligibility, original posting dates,
application status, salary, or clearance. Does not drop/alter queued jobs.
The real runner explicitly opts into this module; no production modification.
"""
from __future__ import annotations
from stage8_workday_actionability_geo_refined import (
    choose_actionable as legacy_choice, due_rows, priority_value,
    geo_hint, index_location, digest,
)
from stage8_workday_soft_enrichment_preview import title_hint


def choose_actionable(candidates, now, newly_monitored_ids, limit=5, fresh_quota=3):
    # The working selector is used as a reference for bounds, due/retry policy,
    # uniqueness validation and the existing reporting shape.
    legacy, base = legacy_choice(candidates, now, newly_monitored_ids,
                                 limit=limit, fresh_quota=fresh_quota)
    if not legacy:
        return legacy, {**base, 'adaptive_fresh': 0, 'foreign_only_selected': 0,
                        'title_hints': 0, 'exploration_reserved': 0}
    due = due_rows(candidates, now)
    if len({p[0]['identity'] for p in due}) != len(due):
        raise AssertionError('Duplicate due identity in adaptive selector input')
    fresh_ids = set(newly_monitored_ids)
    fresh = [p for p in due if p[0]['identity'] in fresh_ids]
    backlog = [p for p in due if p[0]['identity'] not in fresh_ids]

    def ranked(pairs):
        return sorted(pairs, key=lambda p: (-priority_value(p), digest(p)))

    selected = []
    # One oldest-backlog exploration slot (when possible) is retained to
    # discover unconventional titles and avoid starvation.
    explore = min(backlog, key=lambda p: (p[0]['first_seen'], digest(p))) if backlog else None
    if explore is not None:
        selected.append(explore)
        backlog = [p for p in backlog if p[0]['identity'] != explore[0]['identity']]

    # Prioritize new jobs with positive support/IT title signals and a
    # non-foreign-only location. Keep one fresh US/unknown exploration slot
    # for unconventional titles if remaining budget allows.
    admissible_fresh = [p for p in ranked(fresh)
                        if geo_hint(index_location(p)) != 'clearly_foreign_only']
    strong_new = [p for p in admissible_fresh if title_hint(p[1].get('title', '')) > 0]
    quota = min(fresh_quota, len(legacy) - len(selected))
    fresh_pick = strong_new[:quota]
    if len(fresh_pick) < quota and admissible_fresh:
        other = [p for p in admissible_fresh
                 if p[0]['identity'] not in {x[0]['identity'] for x in fresh_pick}]
        # Protect ONE newly monitored unconventional role, not all low-signal
        # novelty at the expense of a large existing backlog.
        if other and not fresh_pick:
            fresh_pick.append(other[0])
    selected.extend(fresh_pick)
    taken = {p[0]['identity'] for p in selected}

    # Backlog competes on actionable index hints; previously un-enriched
    # roles remain eligible even if first observed in an older scan.
    for p in ranked(backlog):
        if len(selected) >= len(legacy):
            break
        if p[0]['identity'] not in taken:
            selected.append(p)
            taken.add(p[0]['identity'])

    # Use any otherwise unused budget, including foreign-only listings,
    # rather than making them permanently ineligible.
    for p in ranked(fresh):
        if len(selected) >= len(legacy):
            break
        if p[0]['identity'] not in taken:
            selected.append(p)
            taken.add(p[0]['identity'])

    if len(selected) != len(legacy) or len(taken) != len(selected):
        raise AssertionError('Adaptive selection budget or identity invariant violated')
    new_selected = sum(p[0]['identity'] in fresh_ids for p in selected)
    return selected, {**base,
        'selected_new_monitoring': new_selected,
        'selected_baseline_or_backlog':len(selected)-new_selected,
        'adaptive_fresh':new_selected,
        'foreign_only_selected':sum(geo_hint(index_location(p)) == 'clearly_foreign_only'
                                    for p in selected),
        'title_hints':sum(title_hint(p[1].get('title', '')) > 0 for p in selected),
        'exploration_reserved':int(explore is not None),
        'adaptive_transfer_from_new_to_backlog':max(0,base['selected_new_monitoring']-new_selected),
    }
