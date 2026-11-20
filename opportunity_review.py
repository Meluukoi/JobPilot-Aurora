"""Optional read-only Stage 3/5 opportunity review and screening reports.

Reports ALL jobs returned by the latest successful collector call, not just
first-seen listings. This is reporting only: does not modify applicants,
profiles, history, existing alert selection, or mail delivery.

A collected posting isn't necessarily still open, and a heuristic score isn't
a probability of an offer. Verify original employer links before applying.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from application_tracking import SavedJobIndex, already_applied, HistoricalWarningIndex
from discovery_history import job_fingerprint
from scorer import requires_international_travel

REVIEW_FIELDS = [
    "priority", "review_rationale", "company", "title", "location", "score", "role_family",
    "recommendation", "geographic_status", "geographic_note", "saved_status",
    "first_seen_in_this_scan", "experience_and_requirement_gaps", "hard_flags",
    "reasons", "url", "posted_at", "historical_application_warning"
]
AUDIT_FIELDS = REVIEW_FIELDS + ["screening_decision", "decision_explanation"]
REVIEW_FLOOR = 30



# Conservative, report-only second-chance titles. These are NOT changes to the
# main scorer, first-seen email matching, or eligibility logic. Employer text
# must also support the title classification; generic "support" is insufficient.
# Additional titles should only be introduced with a regression test.
EXPLORATORY_ROLES = (
    ("Player / community support", r"\b(?:community\s*/\s*player support specialist|player support (?:specialist|representative|associate)|community support specialist)\b",
     r"\b(?:player support|customer support|customer service|support tickets?|technical troubleshooting|troubleshoot|case management|customer inquiries|player inquiries)\b"),
    ("Billing administration", r"\b(?:billing (?:associate|assistant|coordinator|specialist)|payment operations (?:associate|coordinator))\b",
     r"\b(?:billing|invoice|invoicing|accounts receivable|customer account|payment processing)\b"),
    ("Technical product support", r"\b(?:level [12] support representative|product support (?:representative|specialist)|technical operations support analyst|support representative[, ]+level [12])\b",
     r"\b(?:software|saas|troubleshoot|technical|ticketing|help desk|service desk|application)\b"),
    ("Client implementation / onboarding", r"\b(?:implementation (?:specialist|coordinator)|client onboarding (?:specialist|coordinator)|customer onboarding (?:specialist|coordinator))\b",
     r"\b(?:client|customer|software|saas|onboard|implementation|training)\b"),
    ("IT field services", r"\bfield support specialist\b",
     r"\b(?:computer|laptop|desktop|information technology|network troubleshooting|hardware troubleshooting|it support|technical support)\b"),
    ("Workplace customer services", r"\bworkplace experience ambassador\b",
     r"\b(?:customer service|visitor|guest|reception|front desk|workplace experience|hospitality)\b"),
)
EXPLORATORY_SENIORITY = re.compile(
    r"\b(?:senior|sr|principal|staff|lead|manager|director|head|chief|vp|intern|internship)\b", re.I
)


def exploratory_title_track(job):
    """Return a human-review track, never proof of qualification.

    Role text is untrusted. Title + description are inspected with narrowly
    scoped regexes, rather than treating any use of 'support' as a match.
    """
    title = str(job.get("title") or "").lower().strip()
    description = str(job.get("description") or "").lower()
    if EXPLORATORY_SENIORITY.search(title):
        return None
    for track, title_pattern, description_pattern in EXPLORATORY_ROLES:
        if re.search(title_pattern, title, re.I) and re.search(description_pattern, description, re.I):
            return track
    return None



def specialist_title_verification(job, result):
    """Catch specialist titles a keyword-heavy scorer may overrank.

    Only adds a manual-verification label. It never calls the applicant
    ineligible, asserts a mandatory credential, or affects email matching.
    """
    title = str(job.get("title") or "").lower()
    family = result.get("matched_family") or "Other"
    if family == "Cybersecurity" and re.search(r"\bsecurity operations analyst\b", title):
        return "Security operations title: verify production incident response, detection engineering, and tool requirements against documented experience"
    if family == "IT Support" and re.search(r"\btechnical support engineer(?:\s+[i123])?\b", title):
        return "Technical support engineer title: verify required enterprise SaaS/support ownership, API diagnostics, and paid-experience expectations"
    return None

def experience_gap_signals(result):
    """Spot experience/education uncertainty independently of geography."""
    gaps = [str(g) for g in (result.get("gaps") or [])]
    hard = [str(h) for h in (result.get("hard_flags") or [])]
    return any(re.search(r"\b(?:years?|yrs?|experience|tenure|degree|seniority|specialty)\b", g, re.I)
               for g in gaps) or any("degree" in h.lower() or "senior-level" in h.lower() for h in hard)


def classify(job, result, saved_row):
    """Return (priority, screening_decision, reason), preserving real blockers.

    'Exploratory review' is a manual research queue for titles not covered by
    the main role classifier. It is not an assertion of qualifications. Scores
    in that queue are not calibrated and must not be used to rank those jobs.
    """
    score = int(result.get("score") or 0)
    family = result.get("matched_family") or "Other"
    geo = result.get("geo_status", "review")
    hard = result.get("hard_flags") or []
    status = saved_row.get("status", "") if saved_row else "Untracked"
    if saved_row and already_applied(status):
        return "Excluded", "excluded", "Already applied or advanced in application tracking"
    if requires_international_travel(job.get("description", "")) or any(
        "mandatory international travel" in str(flag).lower()
        for flag in hard
    ):
        return "Excluded", "excluded", "Mandatory international travel required"
    if geo == "blocked":
        return "Excluded", "excluded", "Geographic or salary eligibility blocked: " + str(result.get("geo_reason", ""))
    if any("active clearance" in str(x).lower() for x in hard):
        return "Excluded", "excluded", "Requires existing active clearance not evidenced by profile"
    if family == "Other":
        exploratory = exploratory_title_track(job)
        if exploratory:
            if hard:
                return ("Exploratory / verify", "review", f"Manual title review: {exploratory}. Potential mandatory requirements: {', '.join(map(str, hard))}. Original score is not calibrated for this title; verify posting, qualifications, and location.")
            return ("Exploratory review", "review", f"Manual title review: {exploratory}. Outside main career-family classifier; original score is not calibrated for this title. Verify requirements, salary and location.")
        return "Excluded", "excluded", "Job title outside configured target career families"
    if score < REVIEW_FLOOR:
        return "Excluded", "excluded", f"Heuristic score below broad-review floor of {REVIEW_FLOOR}"
    if hard:
        return "Stretch / verify", "review", "Potential mandatory requirement gap: review hard flags and original posting"
    if experience_gap_signals(result):
        if geo == "review":
            return "Stretch + location verification", "review", "Experience or education gap plus uncertain California/relocation eligibility; verify both independently"
        return "Stretch review", "review", "Experience or education requirements need manual verification against documented skills"
    specialist = specialist_title_verification(job, result)
    if specialist:
        return "Specialist skills verification", "review", specialist + "; the general matching score alone is insufficient evidence of qualifications"
    if geo == "review":
        return "Location / pay verification", "review", "Remote CA eligibility, exact worksite, or relocation pay not established"
    if score >= 78 and result.get("recommendation") == "STRONG APPLY":
        return "Strong candidate", "review", "High heuristic relevance; confirm live posting and qualifications"
    if score >= 55:
        return "Priority review", "review", "Relevant target role; verify requirements against your evidence"
    return "Stretch review", "review", "Transferable skills or experience may warrant closer review"


def _text(value):
    if isinstance(value, (list, tuple)):
        value = " | ".join(str(v) for v in value)
    value = str(value if value is not None else "")
    # Prevent Excel/Sheets formula execution from employer-provided text.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value[:1] in ("\t", "\r", "\n"):
        return "'" + value
    return value


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, mode="wb", prefix=".tmp_review_", delete=False) as f:
            temporary = f.name
            f.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _make_csv(rows, fields):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _text(row.get(field)) for field in fields})
    return out.getvalue().encode("utf-8-sig")


def build_review_reports(jobs, matching_profile, saved_rows, fresh_ids, *, scorer, historical_rows=()):
    """Pure function, no filesystem writes. Score all collected listings once.

    Returns (review, audit, counts). Order is conservative by priority then
    descending heuristic score. Every candidate includes the underlying gaps.
    """
    saved_index = SavedJobIndex(saved_rows)
    historical_index = HistoricalWarningIndex(historical_rows)
    review, audit = [], []
    counts = {"collected": 0, "review": 0, "excluded": 0, "first_seen_review": 0,
              "stretch_review": 0, "requires_verification": 0,
              "exploratory_review": 0, "experience_gap_reviews": 0,
              "stretch_labeled": 0, "specialist_review": 0}
    priority_order = {"Strong candidate": 0, "Priority review": 1,
                      "Location / pay verification": 2, "Stretch review": 3,
                      "Stretch + location verification": 4, "Stretch / verify": 5,
                      "Specialist skills verification": 6,
                      "Exploratory review": 7, "Exploratory / verify": 8}
    for job in jobs:
        counts["collected"] += 1
        result = scorer(job, matching_profile)
        saved, _possible = saved_index.lookup(job)
        priority, decision, explanation = classify(job, result, saved)
        is_first = bool(fresh_ids and job_fingerprint(job) in fresh_ids)
        row = {
            "priority": priority,
            "review_rationale": explanation,
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "score": result.get("score", 0),
            "role_family": (result.get("matched_family") or
                            ("Exploratory: " + exploratory_title_track(job) if exploratory_title_track(job) else "Other")),
            "recommendation": result.get("recommendation", ""),
            "geographic_status": result.get("geo_status", "review"),
            "geographic_note": result.get("geo_reason", ""),
            "saved_status": saved.get("status", "") if saved else "Untracked",
            "first_seen_in_this_scan": "Yes" if is_first else "No",
            "experience_and_requirement_gaps": result.get("gaps") or [],
            "hard_flags": result.get("hard_flags") or [],
            "reasons": result.get("reasons") or [],
            "url": job.get("url", ""),
            "posted_at": job.get("posted_at", ""),
            "historical_application_warning": historical_index.warning(job)
        }
        audit.append(dict(row, screening_decision=decision, decision_explanation=explanation))
        if decision == "review":
            review.append(row)
            counts["review"] += 1
            if is_first:
                counts["first_seen_review"] += 1
            if priority.startswith("Stretch") or experience_gap_signals(result):
                counts["stretch_review"] += 1
            if priority.startswith("Stretch"):
                counts["stretch_labeled"] += 1
            if priority == "Specialist skills verification":
                counts["specialist_review"] += 1
            if priority.startswith("Exploratory"):
                counts["exploratory_review"] += 1
            if experience_gap_signals(result):
                counts["experience_gap_reviews"] += 1
            if (result.get("geo_status", "review") == "review" or
                    priority in ("Stretch / verify", "Exploratory / verify")):
                counts["requires_verification"] += 1
        else:
            counts["excluded"] += 1
    review.sort(key=lambda r: (priority_order.get(r["priority"], 10), -int(r["score"]),
                               str(r["company"]), str(r["title"])))
    return review, audit, counts


def save_review_reports(root, review, audit, counts, *, scan_id):
    """Atomically replace only optional reports, not the application databases."""
    target = Path(root) / "monitoring_reports"
    _write_atomic(target / "latest_review_opportunities.csv", _make_csv(review, REVIEW_FIELDS))
    _write_atomic(target / "latest_screening_audit.csv", _make_csv(audit, AUDIT_FIELDS))
    summary = {
        "scan_id": scan_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **counts,
        "review_score_floor": REVIEW_FLOOR,
        "note": ("These are collected job-board listings, not independently verified live openings. "
                 "Scores are explanatory heuristics, not hiring probabilities. "
                 "Screening reports do not trigger email alerts or update applications.")
    }
    _write_atomic(target / "latest_review_summary.json",
                  (json.dumps(summary, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return summary
