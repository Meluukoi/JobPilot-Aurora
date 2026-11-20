"""Screen verified Greenhouse candidates for downstream notification.

Pure screening only. No database writes, archive creation, network
requests, or email delivery.
"""

from alert_policy import email_eligible
from application_tracking import SavedJobIndex, already_applied
from discovery_history import job_fingerprint
from scheduled_scan import select_relevant_first_seen


def build_notification_matches(
    jobs, profile, saved_rows, scorer, min_score=45,
    historical_rows=(),
    freshness_hours=336,
):
    """Return qualified, verified candidates without duplicates.

    Reuses existing JobPilot scoring and application-history screening.
    These records are not new discovery events.
    """
    candidates = []
    seen = set()

    for job in jobs:
        if job.get("source") != "Greenhouse":
            continue

        fingerprint = job_fingerprint(job)

        if fingerprint is None or fingerprint in seen:
            continue

        seen.add(fingerprint)
        candidates.append(job)

    fingerprints = {
        job_fingerprint(job) for job in candidates
    }

    selected = select_relevant_first_seen(
        ((job, profile) for job in candidates),
        fingerprints,
        saved_rows,
        min_score=min_score,
        scorer=scorer,
        historical_rows=historical_rows,
    )

    eligible = []

    for row in selected:
        if not email_eligible(row, freshness_hours=freshness_hours):
            continue

        recommendation = str(
            row.get("recommendation") or ""
        ).upper()

        if (
            "SKIP" in recommendation
            or "NOT ELIGIBLE" in recommendation
            or "BLOCKED" in recommendation
        ):
            continue

        eligible.append(row)

    return eligible
