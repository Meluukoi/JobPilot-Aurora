from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
import html
import json
import re
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "JobPilot/0.2 personal portfolio project"}


def clean_html(raw):
    if not raw:
        return ""
    soup = BeautifulSoup(html.unescape(raw), "html.parser")
    return " ".join(soup.stripped_strings)


def parse_source_url(url):
    """Return (ats, token/site) for common public Greenhouse/Lever URLs."""
    u = url.strip()
    p = urlparse(u)
    host = p.netloc.lower()
    parts = [x for x in p.path.split("/") if x]

    if "lever.co" in host and parts:
        return "Lever", parts[0]

    if ("greenhouse.io" in host or "greenhouse.com" in host) and parts:
        if "embed" not in parts:
            return "Greenhouse", parts[0]

    return None, None


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _jsonld_objects(soup):
    out = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
            out.extend(data["@graph"])
        elif isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return [x for x in out if isinstance(x, dict)]


def _location_from_jsonld(obj):
    parts = []
    if obj.get("jobLocationType"):
        parts.append(str(obj.get("jobLocationType")))

    def add_addr(addr):
        if not isinstance(addr, dict):
            return
        address = addr.get("address") if isinstance(addr.get("address"), dict) else addr
        vals = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
        txt = ", ".join(str(v) for v in vals if v)
        if txt:
            parts.append(txt)

    jl = obj.get("jobLocation")
    if isinstance(jl, list):
        for x in jl:
            add_addr(x)
    elif isinstance(jl, dict):
        add_addr(jl)

    alr = obj.get("applicantLocationRequirements")
    if isinstance(alr, list):
        for x in alr:
            if isinstance(x, dict):
                name = x.get("name")
                if name:
                    parts.append(str(name))
    elif isinstance(alr, dict) and alr.get("name"):
        parts.append(str(alr.get("name")))

    # Preserve order while deduplicating.
    return " | ".join(dict.fromkeys(parts))


def _fetch_greenhouse_web_job(url, company_name, cutoff):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    description = ""
    posted_at = "Unknown"
    location = ""

    # Prefer structured JobPosting data when present.
    for obj in _jsonld_objects(soup):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if "JobPosting" in types:
            title = obj.get("title") or title
            description = clean_html(obj.get("description") or "") or description
            posted_at = obj.get("datePosted") or obj.get("dateModified") or posted_at
            location = _location_from_jsonld(obj) or location
            break

    if posted_at != "Unknown":
        dt = _parse_iso(posted_at)
        if dt and dt < cutoff:
            return None

    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""

    if not location:
        loc_node = soup.select_one(".location, [class*='location']")
        if loc_node:
            location = loc_node.get_text(" ", strip=True)

    if not description:
        # Use visible page text but stop before the application form.
        main = soup.find("main") or soup.body or soup
        text = " ".join(main.stripped_strings)
        for marker in ["Apply for this job", "Apply for this Job", "Submit application"]:
            if marker in text:
                text = text.split(marker, 1)[0]
        description = text

    # If location still wasn't structured, try the short text immediately around the heading.
    if not location and title:
        text = " ".join((soup.body or soup).stripped_strings)
        idx = text.find(title)
        if idx >= 0:
            after = text[idx + len(title): idx + len(title) + 180].strip()
            if after:
                location = after.split("Apply", 1)[0][:120].strip()

    m = re.search(r'/jobs/(\d+)', url)
    source_id = m.group(1) if m else url
    return {
        "source": "Greenhouse-web",
        "source_id": source_id,
        "company": company_name,
        "title": title,
        "location": location,
        "url": url,
        "description": description,
        "posted_at": posted_at
    }


def _fetch_greenhouse_web(board_token, company_name=None, max_age_days=14):
    """Fallback for public Greenhouse boards whose legacy API token returns 404."""
    token = board_token.strip().strip("/")
    board_url = f"https://job-boards.greenhouse.io/{token}"
    r = requests.get(board_url, headers=UA, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        full = urljoin(board_url, href)
        if re.search(r'/jobs/\d+(?:$|[?#])', full):
            links.append(full.split("#", 1)[0])
    links = list(dict.fromkeys(links))

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    jobs = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(_fetch_greenhouse_web_job, u, company_name or token, cutoff) for u in links]
        for fut in as_completed(futs):
            try:
                job = fut.result()
                if job:
                    jobs.append(job)
            except Exception:
                # A single malformed job page should not kill the board scan.
                pass
    return jobs


def fetch_greenhouse(board_token, company_name=None, max_age_days=14, verify_first_published=False, allow_web_fallback=True):
    token = board_token.strip().strip("/")
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    r = requests.get(url, headers=UA, timeout=(5, 12))

    # Full HTML fallback can require one request per job. Skip it during batch
    # scans so a single incompatible board cannot stall all other companies.
    if r.status_code == 404:
        if allow_web_fallback:
            return _fetch_greenhouse_web(token, company_name, max_age_days)
        raise ValueError("Greenhouse API returned 404; HTML fallback skipped in fast batch scan")

    r.raise_for_status()
    items = r.json().get("jobs", [])

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    jobs = []

    for item in items:
        updated_raw = item.get("updated_at")
        updated_dt = _parse_iso(updated_raw)
        if updated_dt and updated_dt < cutoff:
            continue

        # In fast mode the board listing is enough. updated_at means when the
        # employer last edited the listing, NOT necessarily when it was posted.
        # Only make an extra detail request if the user enables date verification.
        first_published = None
        if verify_first_published:
            try:
                d = requests.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{item['id']}",
                    headers=UA, timeout=(4, 7)
                )
                if d.ok:
                    first_published = d.json().get("first_published")
            except requests.RequestException:
                pass
            # Preserve older publications in discovery history.
            # Freshness eligibility is enforced separately at the email gate.
            fp = _parse_iso(first_published)

        jobs.append({
            "source": "Greenhouse",
            "source_id": str(item.get("id", "")),
            "company": company_name or token,
            "title": item.get("title", ""),
            "location": (item.get("location") or {}).get("name", ""),
            "url": item.get("absolute_url", ""),
            "description": clean_html(item.get("content", "")),
            "posted_at": (f"First published: {first_published}" if first_published else
                          f"Last updated (original posting date unverified): {updated_raw}" if updated_raw
                          else "Unknown posting date"),
            "original_published_at": (
                first_published if verify_first_published and fp is not None else ""
            ),
            "original_publication_verified": bool(
                verify_first_published
                and fp is not None
                and fp.tzinfo is not None
                and fp.utcoffset() is not None
            )
        })
    return jobs


def fetch_lever(site, company_name=None):
    site = site.strip().strip("/")
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"
    r = requests.get(url, headers=UA, timeout=(5, 12))
    r.raise_for_status()
    items = r.json()

    jobs = []
    for item in items:
        cats = item.get("categories") or {}
        location = cats.get("location", "")
        workplace = item.get("workplaceType")
        if workplace:
            location = f"{location} | {workplace}"
        jobs.append({
            "source": "Lever",
            "source_id": str(item.get("id", "")),
            "company": company_name or site,
            "title": item.get("text", ""),
            "location": location,
            "url": item.get("hostedUrl") or item.get("applyUrl") or "",
            "description": item.get("descriptionPlain") or clean_html(item.get("description", "")),
            "posted_at": "Unknown"
        })
    return jobs


def fetch_source(source, max_age_days=14, verify_first_published=False):
    ats = source["ats"]
    token = source["token_or_site"]
    company = source.get("company") or token
    if ats == "Greenhouse":
        return fetch_greenhouse(token, company, max_age_days,
                                verify_first_published=verify_first_published,
                                allow_web_fallback=False)
    if ats == "Lever":
        return fetch_lever(token, company)
    return []


def batch_fetch(sources, max_age_days=14, workers=8, progress_callback=None,
                verify_first_published=False):
    """Fetch company boards concurrently; report each source as it finishes.

    progress_callback(completed, total, company, job_count, error_message) is
    invoked in the calling thread, not inside a worker thread.
    """
    jobs, errors = [], []
    total = len(sources)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as ex:
        futs = {
            ex.submit(fetch_source, s, max_age_days, verify_first_published): s
            for s in sources
        }
        for completed, fut in enumerate(as_completed(futs), 1):
            src = futs[fut]
            name = src.get("company") or src.get("token_or_site") or "Unknown"
            count = 0
            err = None
            try:
                fetched = fut.result()
                count = len(fetched)
                jobs.extend(fetched)
            except Exception as exc:
                err = f"{name}: {exc}"
                errors.append(err)
            if progress_callback:
                progress_callback(completed, total, name, count, err)

    deduped = {}
    for j in jobs:
        if j.get("url"):
            deduped[j["url"]] = j
    return list(deduped.values()), errors
