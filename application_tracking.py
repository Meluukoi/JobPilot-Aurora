"""Read-only matching of current ATS jobs to jobs already saved in JobPilot.

No database writes, network calls or third-party dependencies.

'Untracked' means not in the user's Saved Jobs table; it does NOT prove
that the employer posted the job recently or that it is still open.
"""
from collections import defaultdict
from urllib.parse import parse_qs, urlsplit, unquote
import re
from stage8_workday_identity import workday_key


ALREADY_APPLIED_STATUSES = frozenset({"applied", "interview", "rejected", "offer", "archived"})


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _name(value):
    """Conservative comparison for company/title, not a fuzzy semantic match."""
    return re.sub(r"[^a-z0-9]+", " ", _clean(value)).strip()


def _url_identity(url):
    raw = str(url or "").strip()
    if not raw:
        return ()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ()
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = unquote(parsed.path).rstrip("/").casefold()
    if not host:
        return ()
    # Stable public ATS IDs are stronger than the original URL host or referral parameters.
    gh = re.search(r"/jobs/(\d+)(?:$|/)", path)
    if "greenhouse.io" in host and gh:
        return ("gh", gh.group(1))
    params = parse_qs(parsed.query, keep_blank_values=False)
    jid = next((v[0] for k, v in params.items() if k.casefold() in {"gh_jid", "job_id"} and v), "")
    if "greenhouse.io" in host and jid.isdigit():
        return ("gh", jid)
    lever = re.search(r"^/([^/]+)/([0-9a-f-]{30,})(?:$|/)", path)
    if host.endswith("lever.co") and lever:
        return ("lever", lever.group(1), lever.group(2))
    # Ignore tracking query strings on standard clean job URLs, but retain
    # meaningful identity query keys for non-ATS systems.
    identity_params = tuple(sorted((k.casefold(), v[0].casefold()) for k, v in params.items()
                                   if k.casefold() in {"id", "jobid", "job_id", "gh_jid", "reqid", "req_id"} and v))
    return ("url", host, path, identity_params)


def exact_keys(job):
    source = _clean(job.get("source"))

    # Workday requires a trusted employer, board, and requisition.
    # Never infer its identity from a changing URL or job title.
    if source == "workday":
        identity = workday_key(job)
        return (identity,) if identity else ()

    keys = []
    url_key = _url_identity(job.get("url"))
    if url_key:
        keys.append(url_key)
    source = _clean(job.get("source"))
    source_id = _clean(job.get("source_id"))
    if source_id:
        if source.startswith("greenhouse") and source_id.isdigit():
            keys.append(("gh", source_id))
        elif source.startswith("lever") and len(source_id) >= 20:
            # Company included to avoid cross-board collisions; URL already
            # provides the stable Lever board/ID key when it is available.
            keys.append(("lever_source", _name(job.get("company")), source_id))
    return tuple(dict.fromkeys(keys))


def possible_key(job):
    company, title = _name(job.get("company")), _name(job.get("title"))
    return (company, title) if company and title else ()


def already_applied(status):
    return _clean(status) in ALREADY_APPLIED_STATUSES


class SavedJobIndex:
    """Snapshot of the existing Saved Jobs table for one Streamlit rerun."""
    def __init__(self, rows):
        self.by_exact = defaultdict(list)
        self.by_title = defaultdict(list)
        for row in rows:
            for key in exact_keys(row):
                self.by_exact[key].append(row)
            key = possible_key(row)
            if key:
                self.by_title[key].append(row)

    def lookup(self, job):
        """Return (exact_saved_job or None, possible_duplicates).

        Only unambiguous ATS/job URL identities are treated as exact. Same
        company + title with different identifiers is a REVIEW warning, never
        an automatic merge (multiple vacancies might genuinely exist).
        """
        for key in exact_keys(job):
            matches = self.by_exact.get(key, ())
            if matches:
                # Stable selection when DB has existing duplicates.
                # A previously handled exact posting takes precedence
                # over Saved/Untracked duplicates, regardless of row order.
                handled = next(
                    (row for row in matches
                     if already_applied(row.get("status"))),
                    None,
                )
                return handled if handled is not None else matches[0], ()
        candidate_key = possible_key(job)
        return None, tuple(self.by_title.get(candidate_key, ())) if candidate_key else ()


def visible_for_status(saved_job, mode):
    if mode == "Hide already applied":
        return not (saved_job and already_applied(saved_job.get("status")))
    if mode == "Untracked only":
        return saved_job is None
    if mode == "Tracked only":
        return saved_job is not None
    if mode == "Applied / advanced only":
        return bool(saved_job and already_applied(saved_job.get("status")))
    return True


def tracked_sort_key(saved_job):
    """Untracked first, then saved-but-interested, then previously applied."""
    if saved_job is None:
        return 0
    return 2 if already_applied(saved_job.get("status")) else 1


class HistoricalWarningIndex:
    """Non-authoritative historical matches. Never use for automatic suppression.

    Imported records lack verified posting identities; company/title agreement
    only indicates a POSSIBLE previous application, not the same requisition.
    """
    def __init__(self, rows):
        self.by_title = defaultdict(list)
        for row in rows:
            key = possible_key(row)
            if key and already_applied(row.get("status")):
                self.by_title[key].append(row)

    def warning(self, job):
        matches = self.by_title.get(possible_key(job), ())
        if not matches:
            return ""
        statuses = ", ".join(sorted({str(row.get("status", "")).strip() for row in matches}))
        return f"Possible previous application ({statuses}; {len(matches)} historical record(s)). Verify posting identity before applying; do not auto-exclude."
