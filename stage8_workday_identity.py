"""Stage 8: conservative Workday identity, independent of existing ATS matching.

No DB, network, imports from JobPilot, or writes. A caller must explicitly opt in.
The approved registry is intentionally small; add tenants only after verification.
"""
import re
from urllib.parse import urlsplit, unquote

# Trusted employer/tenant/board relationships. Do not infer a tenant from a title.
TRUSTED_BOARDS = {
    ("crowdstrike", "crowdstrikecareers"): "crowdstrike",
}
_HOST = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", re.I)
_REQ = re.compile(r"_([A-Za-z]+\d+)(?:-\d+)?$", re.I)
_VALID_ID = re.compile(r"^[A-Za-z]+\d+$", re.I)


def workday_key(job, trusted_boards=None):
    """Return ('workday', tenant, board, requisition) or None if untrusted.

    Requires trusted board and employer name, https official URL, a /job/ path,
    and an explicit source_id consistent with its URL suffix. This refuses to
    guess identities from title/location and does not perform network requests.
    """
    if str(job.get('source', '')).strip().casefold() != 'workday':
        return None
    registry = TRUSTED_BOARDS if trusted_boards is None else trusted_boards
    try:
        url = urlsplit(str(job.get('url', '')).strip())
        match_host = _HOST.fullmatch(url.hostname or '')
    except ValueError:
        return None
    if (url.scheme != 'https' or not match_host or url.port is not None or
            url.username is not None or url.password is not None):
        return None
    tenant = match_host.group('tenant').casefold()
    path = unquote(url.path)
    parts = path.strip('/').split('/')
    if len(parts) < 4 or parts[1] != 'job' or not all(parts):
        return None
    board = parts[0].casefold()
    # Extra employer check prevents an accidental cross-company association.
    expected_company = registry.get((tenant, board))
    company = re.sub(r'[^a-z0-9]', '', str(job.get('company', '')).casefold())
    if not expected_company or company != expected_company:
        return None
    source_id = str(job.get('source_id', '')).strip()
    match_path = _REQ.search(parts[-1])
    if not _VALID_ID.fullmatch(source_id) or not match_path:
        return None
    if match_path.group(1).casefold() != source_id.casefold():
        return None
    return ('workday', tenant, board, source_id.upper())
