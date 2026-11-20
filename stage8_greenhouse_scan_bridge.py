"""Greenhouse verification-to-notification bridge.

Sandbox integration component. No discovery-history writes,
SMTP delivery, or Workday processing.
"""

from pathlib import Path
from jobpilot_config import load_policy

from stage8_greenhouse_queue import (
    initialize,
    enqueue,
    due_candidates,
    mark_failed,
    mark_verified,
)
from stage8_greenhouse_verification import verify_candidates
from stage8_greenhouse_handoff import (
    ready_for_handoff,
    mark_handed_off,
)
from stage8_greenhouse_notification import build_notification_matches
from stage8_greenhouse_archive import archive_verified_matches


QUEUE_NAME = "stage8_greenhouse_verification_queue.db"


def process_greenhouse_notifications(
    root, sources, first_seen_jobs, profile, saved_rows,
    scorer, scan_id, max_requests=10, now=None,
    historical_rows=(),
    min_score=45,
):
    """Process newly discovered and previously deferred candidates.

    Verification failures remain retryable. Successful publication
    verification does not create a new discovery-history event.
    Eligible handoffs complete only after durable archiving.
    """
    if scan_id is not None and (
        type(scan_id) is not int or scan_id < 0
    ):
        raise ValueError("scan_id must be a nonnegative integer or None")

    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("max_requests must be a nonnegative integer")

    root = Path(root)
    queue_path = root / QUEUE_NAME

    initialize(queue_path)

    enqueued = 0

    for job in first_seen_jobs:
        if job.get("source") != "Greenhouse":
            continue

        enqueue(queue_path, job, now=now)
        enqueued += 1

    due = due_candidates(
        queue_path, now=now, limit=max_requests
    )

    verified_results = verify_candidates(
        due, sources, max_requests=max_requests
    )

    verified_count = 0
    failed_count = 0

    for original, result in zip(due, verified_results):
        if result.get("original_publication_verified") is True:
            published = result.get("original_published_at")

            if mark_verified(
                queue_path, original, published, now=now
            ):
                verified_count += 1
        else:
            if mark_failed(queue_path, original, now=now):
                failed_count += 1

    # No completed discovery scan means no valid notification
    # archive ID. Keep verified work durable for a later scan.
    if scan_id is None:
        return {
            "enqueued": enqueued,
            "verification_attempts": len(due),
            "verified": verified_count,
            "verification_failed": failed_count,
            "ready_for_handoff": 0,
            "notification_matches": 0,
            "handed_off": 0,
            "archive_path": None,
            "handoff_deferred": True,
        }

    ready = ready_for_handoff(queue_path)
    freshness_hours = load_policy(root)["publication_freshness_hours"]

    matches = build_notification_matches(
        ready,
        profile=profile,
        saved_rows=saved_rows,
        scorer=scorer,
        min_score=min_score,
        historical_rows=historical_rows,
        freshness_hours=freshness_hours,
    )

    archive = None

    if matches:
        archive = archive_verified_matches(
            root, scan_id, matches
        )

        if archive is None:
            raise RuntimeError(
                "Eligible Greenhouse matches were not archived"
            )

    # Screening has completed. Eligible candidates have already
    # been durably archived; ineligible candidates need no email.
    handed_off = 0

    for job in ready:
        if mark_handed_off(queue_path, job):
            handed_off += 1

    return {
        "enqueued": enqueued,
        "verification_attempts": len(due),
        "verified": verified_count,
        "verification_failed": failed_count,
        "ready_for_handoff": len(ready),
        "notification_matches": len(matches),
        "handed_off": handed_off,
        "archive_path": str(archive) if archive else None,
    }
