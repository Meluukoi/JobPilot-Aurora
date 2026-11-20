"""Testing-only Stage 8 career-fit index ranking; never gates email, publication date, or applications.

Wraps the already-tested adaptive selector's due/budget behavior. All candidate records
remain in queue; deprioritization is not rejection. One oldest-backlog exploratory
slot is retained when backlog exists. No network or file writes.
"""
from __future__ import annotations
import re
from stage8_workday_actionability_budget_v2 import choose_actionable as previous_choice
from stage8_workday_actionability_geo_refined import due_rows, geo_hint, index_location, digest, foreign_title_warning

# Intentionally broad transferable-career scope; avoid exact job-title-only matching.
DIRECT = (
    (r'\b(?:help\s*desk|service\s*desk|desktop\s*support|it\s*support|technical\s*support|technology\s*support|product\s*support|application\s*support|customer\s*support|client\s*support|player\s*support|community\s*support)\b', 12),
    (r'\b(?:soc\s*analyst|security\s*operations|vulnerability\s*(?:analyst|technician|specialist)|incident\s*response\s*analyst|information\s*security\s*analyst|cybersecurity\s*analyst)\b', 10),
    (r'\b(?:data\s*center\s*technician|field\s*(?:support|service)\s*technician|computer\s*technician|pc\s*technician|network\s*technician|systems?\s*administrator|support\s*associate|support\s*specialist|support\s*technician|support\s*analyst|technical\s*customer\s*success)\b', 9),
    (r'\b(?:customer\s*success|customer\s*experience|customer\s*service|client\s*services|client\s*success|customer\s*care|player\s*experience|community\s*relations)\b', 6),
    (r'\b(?:vulnerability\s*management|security\s*analyst|network\s*analyst|systems?\s*analyst|operations\s*analyst|support\s*operations)\b', 5),
)
SENIOR = re.compile(r'(?i)\b(?:sr\.?|senior|principal|staff|lead|director|vp|vice president|chief|head of|manager|architect|consultant|engineer\s*(?:ii|iii|iv|[2-9]))\b')
JUNIOR = re.compile(r'(?i)\b(?:junior|entry[ -]?level|associate|level\s*1|level\s*i\b|tier\s*1|tier\s*i\b|analyst\s*i\b|technician\s*i\b)')
FOREIGN_ONLY = 'clearly_foreign_only'


def career_value(title):
    """Index-only relative fit signal, not experience/clearance or application eligibility."""
    s=str(title or '').strip()
    fit=max((weight for pattern,weight in DIRECT if re.search(pattern,s,re.I)),default=0)
    if fit:
        fit += (2 if JUNIOR.search(s) else 0)
        fit -= (9 if SENIOR.search(s) else 0)
    elif SENIOR.search(s):
        fit=-9
    else:
        fit=0
    return fit


def choose_career_fit(candidates, now, newly_monitored_ids, limit=5, fresh_quota=3):
    """Same budget/due/identity checks as prior selector; soft ordering only."""
    prior, stats = previous_choice(candidates,now,newly_monitored_ids,limit,fresh_quota)
    if not prior:
        return prior,{**stats,'career_fit_positive':0,'career_fit_direct':0,'career_fit_senior':0}
    due=due_rows(candidates,now)
    if len({r['identity'] for r,_ in due}) != len(due):
        raise AssertionError('Duplicate due identity')
    fresh_ids=set(newly_monitored_ids)
    backlog=[p for p in due if p[0]['identity'] not in fresh_ids]
    fresh=[p for p in due if p[0]['identity'] in fresh_ids]
    # One true oldest-backlog slot, even if its title is odd, so novel categories remain discoverable.
    explore=min(backlog,key=lambda p:(p[0]['first_seen'],digest(p))) if backlog else None
    chosen=[explore] if explore else []
    taken={p[0]['identity'] for p in chosen}
    target=len(prior)
    def rank(p):
        fit=career_value(p[1].get('title',''))
        geo=geo_hint(index_location(p))
        loc_bonus={'plausible_us':3,'unknown':0,FOREIGN_ONLY:-40}[geo]
        if foreign_title_warning(p):loc_bonus-=80
        return (-(fit*3+loc_bonus),digest(p))
    # Strong *new* opportunities deserve up to the established quota. Unconventional
    # new US roles can still receive one slot, retaining breadth without forcing
    # senior/overseas novelty to dominate the daily budget.
    admissible_new=sorted((p for p in fresh if geo_hint(index_location(p))!=FOREIGN_ONLY),key=rank)
    positive_new=[p for p in admissible_new if career_value(p[1].get('title',''))>0]
    quota=min(fresh_quota,target-len(chosen))
    for p in positive_new[:quota]:
        chosen.append(p); taken.add(p[0]['identity'])
    if len(chosen)-(1 if explore else 0)<quota and not positive_new:
        neutral_new=next((p for p in admissible_new if career_value(p[1].get('title',''))>=0),None)
        if neutral_new:
            chosen.append(neutral_new);taken.add(neutral_new[0]['identity'])
    # Remaining slots go to the strongest *actionable* backlog, not merely oldest.
    # Deliberately rank across backlog; no title-based removal or blocking.
    for p in sorted(backlog,key=rank):
        if len(chosen)>=target:break
        if p[0]['identity'] not in taken:
            chosen.append(p);taken.add(p[0]['identity'])
    for p in sorted(fresh,key=rank):
        if len(chosen)>=target:break
        if p[0]['identity'] not in taken:
            chosen.append(p);taken.add(p[0]['identity'])
    if len(chosen)!=target or len(taken)!=len(chosen):
        raise AssertionError('Career selection changed budget/unique identity')
    nnew=sum(r['identity'] in fresh_ids for r,_ in chosen)
    values=[career_value(e.get('title','')) for _,e in chosen]
    return chosen,{**stats,'selected_new_monitoring':nnew,'selected_baseline_or_backlog':target-nnew,
        'career_fit_positive':sum(v>0 for v in values),
        'career_fit_direct':sum(v>=8 for v in values),
        'career_fit_senior':sum(bool(SENIOR.search(e.get('title',''))) for _,e in chosen),
        'foreign_only_selected':sum(geo_hint(index_location(p))==FOREIGN_ONLY for p in chosen),
        'exploration_reserved':int(explore is not None)}
