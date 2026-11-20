"""Durable CSV archiving for verified Greenhouse opportunities.

Does not alter discovery history, send emails, or mark queue handoffs.
"""

import csv
import io
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from alert_policy import email_eligible
from jobpilot_config import load_policy
from email_notifications import _url_key
from scheduled_scan import COLUMNS, _csv_text


def _read_rows(path):
    if not path.exists():
        return []

    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def _atomic_write(path, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=COLUMNS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()

    for row in rows:
        writer.writerow({
            column: _csv_text(row.get(column, ""))
            for column in COLUMNS
        })

    temporary = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".tmp_greenhouse_archive_",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(output.getvalue().encode("utf-8-sig"))
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary, path)

    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def archive_verified_matches(root, scan_id, matches):
    """Archive newly verified matches without duplicating eligible URLs.

    Previously unverified archive rows do not suppress a newly
    verified match. Existing archive rows are preserved.

    Returns the archive path when written or already containing
    a matching eligible record; otherwise returns None.
    """
    if type(scan_id) is not int or scan_id < 0:
        raise ValueError("scan_id must be a nonnegative integer")

    folder = Path(root) / "monitoring_reports"
    archive = folder / f"first_seen_matches_scan_{scan_id:06d}.csv"

    if not matches:
        return None

    freshness_hours = load_policy(root)["publication_freshness_hours"]
    eligible = [
        dict(row) for row in matches
        if email_eligible(row, freshness_hours=freshness_hours)
    ]

    if not eligible:
        return None

    existing_archives = (
        sorted(folder.glob("first_seen_matches_scan_*.csv"))
        if folder.is_dir() else []
    )

    seen = set()
    current_rows = []

    for existing in existing_archives:
        rows = _read_rows(existing)

        if existing == archive:
            current_rows = rows

        for row in rows:
            if email_eligible(row, freshness_hours=freshness_hours) and row.get("url"):
                seen.add(_url_key(row["url"]))

    additions = []

    for row in eligible:
        url = row.get("url")

        if not isinstance(url, str) or not url.strip():
            continue

        key = _url_key(url)

        if key in seen:
            continue

        seen.add(key)
        additions.append(row)

    if not additions:
        return archive if archive.exists() else None

    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write(archive, current_rows + additions)

    return archive
