"""Stage 5D: conservative eligibility gate for first-seen *email* alerts.

The broad screening reports still retain uncertain listings. Only explicit local,
CA-eligible remote, or relocation-with-verified-base-minimum rows are emailed.
Archived reports created before installing Stage 5D are subject to the same gate.
No network calls, configuration writes, or changes to saved-job databases.
"""

from datetime import datetime, timedelta, timezone

ALERT_STATUSES = frozenset({'local', 'remote_ca', 'relocation'})
MAX_PUBLICATION_AGE_HOURS = 336


def email_eligible(row, freshness_hours=MAX_PUBLICATION_AGE_HOURS):
    """Require confirmed geography and independently verified fresh publication."""
    if type(freshness_hours) is not int or not 1 <= freshness_hours <= 336:
        raise ValueError("freshness_hours must be an integer from 1 to 336")

    if str(row.get('eligibility') or '').strip().lower() not in ALERT_STATUSES:
        return False

    verified = row.get('original_publication_verified')
    if verified is not True and not (
        isinstance(verified, str)
        and verified.strip().lower() == 'true'
    ):
        return False

    raw = row.get('original_published_at')
    if not isinstance(raw, str) or not raw.strip():
        return False

    try:
        published = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    except ValueError:
        return False

    if published.tzinfo is None or published.utcoffset() is None:
        return False

    now = datetime.now(timezone.utc)
    published = published.astimezone(timezone.utc)

    return now - timedelta(hours=freshness_hours) <= published <= now
