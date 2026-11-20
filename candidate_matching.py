"""Evidence-driven candidate matching for JobPilot v0.2.

Uses ONLY the local, user-approved candidate_profile.json. Does not read
resumes, GitHub, accounts, or internet services automatically.
This is a transparent heuristic, NOT an interview/hiring probability.
"""
import re


def _contains(text, term):
    return re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)", text.lower()) is not None


# A matching term counts only when grounded in the approved evidence record.
# It never gets interpreted as professional employment with that technology.
LAB_TERMS = {
    "Splunk Enterprise SPL searches": ["splunk", "spl", "siem"],
    "Sysmon and Windows event analysis": ["sysmon", "windows event", "event log"],
    "Active Directory administration and monitoring": ["active directory", "windows server", "group policy"],
    "Nessus vulnerability triage": ["nessus", "vulnerability management", "cvss"],
    "Wireshark and core network protocols": ["wireshark", "tcp/ip", "dns"],
    "Microsoft Entra ID basics": ["entra id", "microsoft entra", "azure ad", "identity access"],
    "Microsoft Sentinel and Azure Log Analytics": ["microsoft sentinel", "azure log analytics"],
    "Excel analysis": ["excel", "pivot table", "xlookup"],
    "SQL / SQLite": ["sql", "sqlite", "sql query"],
}
EXPOSURE_TERMS = {
    "Salesforce CRM": ["salesforce", "crm"],
    "Jira Service Management": ["jira", "ticketing"],
    "ServiceNow": ["servicenow"],
    "Python / Streamlit / SQLite JobPilot": ["python", "streamlit"],
}

STATE_LOCATIONS = {
    'AZ':'arizona', 'CA':'california', 'CO':'colorado', 'FL':'florida',
    'IL':'illinois', 'MA':'massachusetts', 'NY':'new york', 'OR':'oregon',
    'PA':'pennsylvania', 'TX':'texas', 'WA':'washington', 'VA':'virginia',
    'GA':'georgia', 'NC':'north carolina', 'OH':'ohio', 'TN':'tennessee',
    'WI':'wisconsin', 'MI':'michigan', 'NV':'nevada', 'UT':'utah', 'MN':'minnesota',
    'MO':'missouri', 'NJ':'new jersey', 'MD':'maryland', 'DC':'district of columbia'
}


def _annual_salary_numbers(description):
    """Return clear >=10k annual-looking USD amounts; conservative if absent."""
    pattern = r'\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3}\s*[kK])\b'
    numbers = []
    for m in re.finditer(pattern, description or ''):
        value = m.group(1).replace(',', '').replace(' ', '')
        try:
            number = int(value[:-1]) * 1000 if value.lower().endswith('k') else int(value)
        except ValueError:
            continue
        if 10000 <= number <= 1000000:
            numbers.append(number)
    return numbers


def _clearance_required(text):
    """Require explicit *current/active* clearance; ability to obtain is different."""
    t = text.lower()
    if not ('clearance' in t or 'ts/sci' in t):
        return False
    patterns = [
        r'\b(?:active|current|existing)\s+(?:(?:secret|top secret|ts/sci)\s+)?(?:security\s+)?clearance',
        r'\b(?:active|current|existing)\s+(?:secret|top secret|ts/sci)\b',
        r'\b(?:must have|requires?|possess|hold)\s+(?:an?\s+)?(?:active|current|existing)\b.{0,85}\bclearance',
    ]
    found = any(re.search(p, t) for p in patterns)
    if not found:
        return False
    if re.search(r'(?:ability|eligible|willing|need)\s+to\s+obtain.{0,65}(?:clearance|secret|top secret)',t) and not re.search(r'\b(?:must have|currently hold|already hold)\b.{0,80}\bclearance', t):
        # Mixed phrasing requires manual evaluation rather than a hard rejection.
        return False
    return True


def _relocation_job(location):
    """Only flag strong onsite/hybrid non-local signals; ambiguity stays manual."""
    loc = location.lower().strip()
    if not loc or 'remote' in loc or 'virtual' in loc or 'anywhere' in loc:
        return False
    if 'san diego' in loc:
        return False
    # Unspecified "California" does not verify commuting distance.
    if loc in ('california', 'ca', 'united states', 'usa', 'us'):
        return False
    for code, name in STATE_LOCATIONS.items():
        if code == 'CA':
            continue
        if _contains(loc, name) or re.search(r'(?<!\w)'+code+r'(?!\w)',loc,re.I):
            return True
    # Non-local California cities also require relocation; San Diego area exceptions
    # aren't guessed from listings that only name the state.
    if any(_contains(loc, city) for city in ['san francisco','los angeles','sacramento','san jose','fresno','oakland']):
        return True
    return False


def assess_candidate(job, candidate, family):
    """Return (score adjustment, strengths, gaps, confirmed blockers)."""
    title = job.get('title') or ''
    desc = job.get('description') or ''
    loc = job.get('location') or ''
    blob = f'{title} {desc}'.lower()
    prefs = candidate.get('preferences') or {}
    score = 0
    reasons, gaps, hard = [], [], []

    if family in {'Customer/Client Support','IT Support','Operations',
                  'Sales / Business Development'} and candidate.get('professional_experience'):
        score += 7
        reasons.append('Paid front-end retail and customer-service desk work, including phone support (not paid IT help desk)')

    if _contains(blob, 'security+') and any('security+' in c.lower() for c in candidate.get('resume_reported_certifications', [])):
        score += 4
        reasons.append('CompTIA Security+ certified')

    evidenced = {x.get('name') for x in candidate.get('repository_supported_capabilities', [])}
    lab_matches = []
    for label, terms in LAB_TERMS.items():
        if label in evidenced and any(_contains(blob, term) for term in terms):
            lab_matches.append(label)
    if lab_matches:
        score += min(8, len(lab_matches)*2)
        reasons.append('Personal-project evidence: ' + ', '.join(lab_matches[:4]))

    exposed = {x.get('name') for x in candidate.get('conversation_reported_exposure', [])}
    intro = []
    for label, terms in EXPOSURE_TERMS.items():
        if label in exposed and any(_contains(blob, term) for term in terms):
            intro.append(label)
    if intro:
        score += min(3, len(intro))
        reasons.append('Basic or collaborative project exposure (not paid): ' + ', '.join(intro[:4]))

    if _clearance_required(desc) and prefs.get('active_us_security_clearance') is False:
        score -= 42
        hard.append('Existing active clearance required, but none held')
    if prefs.get('citizenship') and re.search(r'(?:(?:u\.?s\.?)|united states)\s+citizen(?:ship)?\s+(?:is\s+)?required|must be (?:a )?(?:u\.?s\.?|united states) citizen', blob):
        reasons.append('U.S. citizenship confirmed')

    if not job.get("_geo_handled") and _relocation_job(loc) and prefs.get('relocation', {}).get('open'):
        min_sal = prefs['relocation'].get('minimum_annual_salary_usd', 100000)
        amounts = _annual_salary_numbers(desc)
        if amounts and max(amounts) < min_sal:
            score -= 45
            hard.append(f'Relocation salary: listed annual amounts top out below ${min_sal:,.0f}')
        elif not amounts:
            score -= 6
            gaps.append(f'Non-local role may require relocation; verify salary at least ${min_sal:,.0f}/year')
        elif min(amounts) < min_sal:
            gaps.append(f'Relocation salary range includes amounts below ${min_sal:,.0f}; verify offer')
        else:
            reasons.append(f'Listed annual salary meets ${min_sal:,.0f} relocation threshold (verify)')

    # $19/hour is approximate, not a strict block, and $25 is NOT a ceiling.
    if not job.get("_geo_handled") and not _relocation_job(loc):
        hourly = []
        for m in re.finditer(r'\$\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:/|per\s*)\s*(?:hour|hr)\b', desc, re.I):
            hourly.append(float(m.group(1)))
        preferred_min = prefs.get('local_remote_pay',{}).get('minimum_hourly_usd_approx',19)
        if hourly and max(hourly) < preferred_min:
            gaps.append(f'Listed hourly pay below approx. ${preferred_min}/hour preference')

    return score, reasons, gaps, hard
