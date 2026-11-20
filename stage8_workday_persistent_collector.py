"""Persistent Workday collection integration.

No scheduler, history, reporting, or SMTP operations.
Callers supply an approved employer, HTTP client, and initialized queue.
"""

from datetime import datetime, timezone

from stage8_workday_multi_employer_adapter import collect_preview
from stage8_workday_adapter_queue_bridge import (
    due_for_enrichment,
    import_preview,
    fail_enrichment,
    identity,
)


def collect_to_queue(
    employer, client, queue_path, *,
    now=None, max_pages=2, max_details=2
):
    """Collect indexed jobs and persist eligible detail enrichment.

    A queue-aware callback skips ready, stalled, and backoff-restricted jobs.
    Incomplete indexes remain explicitly partial.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    preview = collect_preview(
        employer,
        client,
        max_pages=max_pages,
        max_details=max_details,
        detail_eligible=lambda entry: due_for_enrichment(
            queue_path, entry, now
        ),
    )

    persisted = import_preview(queue_path, preview, now)

    failed = 0

    for entry in preview["index"]:
        error = str(entry.get("error") or "")

        # Only record failures from attempted detail requests.
        # Identity validation errors are not retryable.
        if not entry.get("identity") or not error:
            continue
        if entry.get("enrichment_status") != "error":
            continue
        if error.startswith(("Untrusted/", "Identity collision")):
            continue

        key = identity({
            "source": "Workday",
            "source_id": entry["source_id"],
            "company": employer.company,
            "title": entry["title"],
            "location": entry["location_summary"],
            "url": entry["url"],
        })

        if fail_enrichment(queue_path, key, now, error):
            failed += 1

    return {
        "employer": employer.slug,
        "indexed": persisted["indexed"],
        "ready_in_preview": persisted["ready_in_preview"],
        "index_complete": persisted["index_complete"],
        "failed_enrichments": failed,
        "warnings": list(preview["warnings"]),
    }


def collect_configured_workday(
    sources, queue_path, *, client_factory=None, now=None
):
    """Collect approved, enabled Workday sources into the durable queue.

    No discovery-history updates, reports, SMTP, or applications.
    The caller must explicitly select Workday sources.
    """
    from stage8_workday_multi_employer_adapter import (
        EMPLOYERS, PublicClient
    )
    from stage8_workday_adapter_queue_bridge import (
        initialize, initialize_batch_handoff
    )

    approved = []

    for source in sources:
        if source.get("ats") != "Workday":
            raise ValueError("Non-Workday source passed to Workday collector")

        slug = source.get("token_or_site")
        employer = EMPLOYERS.get(slug)

        if employer is None or source.get("company") != employer.company:
            raise ValueError("Unapproved Workday employer configuration")

        approved.append(employer)

    if not approved:
        return []

    initialize(queue_path)
    initialize_batch_handoff(queue_path)

    if now is None:
        now = datetime.now(timezone.utc)

    summaries = []

    for employer in approved:
        # Maximum 25 index requests plus 2 description requests.
        budget = 27

        client = (
            client_factory(employer, budget)
            if client_factory is not None
            else PublicClient(employer, budget)
        )

        result = collect_to_queue(
            employer,
            client,
            queue_path,
            now=now,
            max_pages=25,
            max_details=2,
        )

        summaries.append(result)

    return summaries


def prepare_configured_workday_history(queue_path, history_path):
    """Prepare fully enriched Workday records for one shared history batch.

    Requires an initialized Workday queue and existing discovery history.
    Persists handoff intentions BEFORE the caller records the batch.

    Returns job records only. Does not record discovery history, score jobs,
    create reports, send email, or acknowledge handoffs.
    """
    from stage8_workday_batch_handoff import prepare_batch

    return prepare_batch(queue_path, history_path)
