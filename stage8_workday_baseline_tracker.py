"""Stage 8 Workday baseline classification prototype.

Standalone testing-only helper. Does not import or modify JobPilot modules,
contact employers, read credentials, send email, or change existing SQLite data.

'new_to_monitoring' means absent from a previously completed full index;
it is NOT a claim about the employer's publication date.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

VERSION = 1


def empty_state():
    return {"version": VERSION, "employers": {}}


def classify(state, employer, identities, index_complete):
    """Return (classification, new_state), with no mutation of input state.

    An incomplete initial scan cannot establish a baseline. A complete initial
    scan marks *all* current entries as baseline, even those seen in earlier
    partial scans. Once established, an unseen identity is new to monitoring,
    even if a subsequent scan is incomplete. An incomplete scan does not imply
    anything about identities absent from that scan.
    """
    if not isinstance(employer, str) or not employer.strip():
        raise ValueError('employer must be a nonempty string')
    if not isinstance(index_complete, bool):
        raise ValueError('index_complete must be a boolean')
    if state.get('version') != VERSION or not isinstance(state.get('employers'), dict):
        raise ValueError('Invalid baseline state')
    ids = list(identities)
    if not all(isinstance(x, str) and x.strip() for x in ids):
        raise ValueError('Identities must be nonempty strings')
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate identities: refuse ambiguous index')
    current = state['employers'].get(employer, {})
    if not isinstance(current, dict):
        raise ValueError('Invalid employer state')
    completed_before = current.get('baseline_complete', False)
    if not isinstance(completed_before, bool):
        raise ValueError('Invalid baseline completion status')
    known = current.get('known', [])
    if not isinstance(known, list) or not all(isinstance(x, str) for x in known):
        raise ValueError('Invalid known identities')
    known_set = set(known)
    if completed_before:
        newly_found = [x for x in ids if x not in known_set]
        baseline = []
        existing = [x for x in ids if x in known_set]
        established_now = False
    else:
        newly_found = []
        baseline = ids  # includes jobs from earlier partial scans
        existing = []
        established_now = index_complete
    updated = {
        'version': VERSION,
        'employers': {**state['employers'], employer: {
            'baseline_complete': completed_before or index_complete,
            'known': sorted(known_set.union(ids)),
        }},
    }
    result = {
        'employer': employer,
        'baseline_complete_before': completed_before,
        'baseline_established_this_scan': established_now,
        'baseline_complete_after': completed_before or index_complete,
        'index_complete': index_complete,
        'baseline_or_unclassified': baseline,
        'new_to_monitoring': newly_found,
        'previously_known': existing,
    }
    return result, updated


def load_state(path):
    p = Path(path)
    if not p.exists():
        return empty_state()
    data = json.loads(p.read_text(encoding='utf-8'))
    if data.get('version') != VERSION or not isinstance(data.get('employers'), dict):
        raise ValueError('Baseline state is incompatible; refusing reset')
    return data


def save_state_atomic(path, state):
    """Save a dedicated tracker JSON file; never write queue/history SQLite."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=p.parent,
                                         prefix=p.name + '.', suffix='.tmp',
                                         delete=False) as fh:
            temp = Path(fh.name)
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write('\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, p)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def self_test():
    state = empty_state()
    # Partial first scan: neither employer is established; no 'new' jobs.
    r, state = classify(state, 'boozallen', ['B1', 'B2'], False)
    assert len(r['baseline_or_unclassified']) == 2 and not r['new_to_monitoring']
    assert not r['baseline_complete_after']
    # More initial pages in next partial scan must not be called new.
    r, state = classify(state, 'boozallen', ['B2', 'B3'], False)
    assert not r['new_to_monitoring'] and not r['baseline_complete_after']
    # Independent employer completes its first index.
    r, state = classify(state, 'crowdstrike', ['C1', 'C2'], True)
    assert r['baseline_established_this_scan'] and not r['new_to_monitoring']
    # The first *complete* Booz index is all baseline, even a never-seen ID.
    r, state = classify(state, 'boozallen', ['B1', 'B2', 'B3', 'B4'], True)
    assert r['baseline_established_this_scan'] and not r['new_to_monitoring']
    # Later scan: only unseen requisitions are new to monitoring.
    r, state = classify(state, 'boozallen', ['B1', 'B4', 'B5'], False)
    assert r['new_to_monitoring'] == ['B5']
    assert not r['baseline_established_this_scan']
    # Incomplete later scan does not erase missing old identities.
    r, state = classify(state, 'boozallen', ['B3', 'B5'], True)
    assert not r['new_to_monitoring'] and r['baseline_complete_before']
    # Independent employer's newly discovered ID.
    r, state = classify(state, 'crowdstrike', ['C2', 'C3'], True)
    assert r['new_to_monitoring'] == ['C3']
    # Duplicate/rejected malformed identity safeguards.
    for ids in (['X', 'X'], [''], [None]):
        try:
            classify(state, 'boozallen', ids, True)
        except ValueError:
            pass
        else:
            raise AssertionError('Should reject invalid IDs')
    # The input state is never mutated on classification.
    earlier = json.dumps(state, sort_keys=True)
    _, updated = classify(state, 'crowdstrike', ['C4'], False)
    assert json.dumps(state, sort_keys=True) == earlier
    assert updated != state
    # Persistence and reload, outside any JobPilot folder.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'baseline_tracker.json'
        save_state_atomic(path, state)
        assert load_state(path) == state
        assert load_state(Path(tmp) / 'absent.json') == empty_state()
    print('PASS: 10 offline baseline, identity, isolation and persistence checks')
    print('No JobPilot files or SQLite databases changed; no HTTPS/SMTP requests.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', required=True)
    args = parser.parse_args()
    self_test()
