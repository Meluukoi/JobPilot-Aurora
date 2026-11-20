"""Bounded Greenhouse original-publication verification.

No database, reporting, email, or scheduler side effects.
Call only with previously selected candidate jobs.
"""

from datetime import datetime
import re

import requests


def _valid_publication_date(value):
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError:
        return False

    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def verify_candidates(jobs, sources, max_requests=10):
    """Verify at most max_requests eligible Greenhouse candidates.

    Return copied job records in their original order.
    Failed or deferred verification remains unverified.
    """
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("max_requests must be a nonnegative integer")

    boards = {}

    for source in sources:
        if source.get("ats") != "Greenhouse":
            continue

        company = source.get("company")
        token = source.get("token_or_site")

        if (
            isinstance(company, str)
            and company.strip()
            and isinstance(token, str)
            and re.fullmatch(r"[A-Za-z0-9_-]+", token)
        ):
            boards[company] = token

    results = []
    attempted = 0
    cache = {}

    for job in jobs:
        record = dict(job)
        results.append(record)

        if record.get("source") != "Greenhouse":
            continue

        company = record.get("company")
        token = boards.get(company)
        job_id = str(record.get("source_id") or "")

        if not token or not re.fullmatch(r"[0-9]+", job_id):
            continue

        if (
            record.get("original_publication_verified") is True
            and _valid_publication_date(
                record.get("original_published_at")
            )
        ):
            continue

        key = (token, job_id)

        if key not in cache:
            if attempted >= max_requests:
                continue

            attempted += 1
            verified_date = None

            try:
                response = requests.get(
                    f"https://boards-api.greenhouse.io/v1/boards/"
                    f"{token}/jobs/{job_id}",
                    timeout=(4, 7),
                )

                if response.ok:
                    candidate = response.json().get("first_published")

                    if _valid_publication_date(candidate):
                        verified_date = candidate

            except (requests.RequestException, ValueError, TypeError, AttributeError):
                pass

            cache[key] = verified_date

        verified_date = cache[key]

        if verified_date is not None:
            record["original_published_at"] = verified_date
            record["original_publication_verified"] = True
        else:
            record["original_published_at"] = ""
            record["original_publication_verified"] = False

    return results
