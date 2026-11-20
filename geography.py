"""Conservative job-location eligibility for San Diego-based JobPilot user.

This is a heuristic, not legal or payroll eligibility verification. A U.S.
remote listing is NOT assumed to support California residents.
"""
from __future__ import annotations
import re

LOCAL_PLACES = (
    'san diego','chula vista','national city','la mesa','el cajon','santee',
    'poway','rancho bernardo','del mar','encinitas','carlsbad','oceanside',
    'escondido','vista','san marcos','solana beach','coronado','imperial beach',
    'lemon grove','la jolla','pacific beach','mission valley','kearny mesa',
    'miramar','otay mesa','rancho santa fe', 'san diego county'
)
NONLOCAL_CA = (
    'los angeles','san francisco','san jose','sacramento','fresno',
    'oakland','irvine','orange county','riverside','san bernardino','bakersfield',
    'silicon valley','palo alto','mountain view','san mateo','santa clara',
    'long beach','anaheim','santa monica','ventura','san luis obispo',
)
FOREIGN = (
    'ireland','dublin','united kingdom','london','england','europe','emea',
    'germany','berlin','france','paris','spain','madrid','netherlands',
    'amsterdam','poland','warsaw','singapore','india','bengaluru',
    'australia','sydney','melbourne','canada','toronto','vancouver',
    'mexico city','new zealand','auckland','brazil','apac',
    'uk','scotland','wales','northern ireland','costa rica','delhi',
    'british columbia','bc'
)

# Only treat explicit *location qualifiers* in titles as country hints.
# Country terms in job descriptions can name customers or company offices
# and must not by themselves disqualify a US-based candidate.
FOREIGN_TITLE_LOCATION = re.compile(
    r'(?:\(\s*(?:UK|United Kingdom|Canada)\s*\)'
    r'|\[\s*(?:UK|United Kingdom|Canada)\s*\]'
    r'|(?:[-–—|])\s*(?:UK|United Kingdom|Canada)\s*$'
    r'|\b(?:remote|based)\s+(?:in\s+)?(?:the\s+)?(?:UK|United Kingdom|Canada)\b'
    r'|\b(?:UK|Canada)[- ]based\b)',
    re.I,
)
STATES = {
    'AL':'Alabama','AK':'Alaska','AZ':'Arizona','AR':'Arkansas','CA':'California',
    'CO':'Colorado','CT':'Connecticut','DE':'Delaware','FL':'Florida','GA':'Georgia',
    'HI':'Hawaii','ID':'Idaho','IL':'Illinois','IN':'Indiana','IA':'Iowa','KS':'Kansas',
    'KY':'Kentucky','LA':'Louisiana','ME':'Maine','MD':'Maryland','MA':'Massachusetts',
    'MI':'Michigan','MN':'Minnesota','MS':'Mississippi','MO':'Missouri','MT':'Montana',
    'NE':'Nebraska','NV':'Nevada','NH':'New Hampshire','NJ':'New Jersey',
    'NM':'New Mexico','NY':'New York','NC':'North Carolina','ND':'North Dakota',
    'OH':'Ohio','OK':'Oklahoma','OR':'Oregon','PA':'Pennsylvania','RI':'Rhode Island',
    'SC':'South Carolina','SD':'South Dakota','TN':'Tennessee','TX':'Texas',
    'UT':'Utah','VT':'Vermont','VA':'Virginia','WA':'Washington','WV':'West Virginia',
    'WI':'Wisconsin','WY':'Wyoming','DC':'District of Columbia',
}


def has(text, phrase):
    return re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I) is not None


def states_in(text):
    """Full names case-insensitive; two-letter abbreviations case-sensitive.

    Avoid the notorious 'IN'/'OR' English word false matches.
    """
    found=set()
    for code, name in STATES.items():
        if has(text, name) or re.search(r'(?<![A-Za-z])'+code+r'(?![A-Za-z])', text):
            found.add(code)
    return found


def hiring_state_list(desc):
    """A whitelist only when explicitly introduced and with 2+ states."""
    introductions = (
        r'(?:approved|eligible|hiring|authorized|permitted)\s+states?\s*(?:include|are|:|-)?',
        r'(?:we\s+(?:can|currently|are able to)\s+hire\s+(?:in|from))',
        r'(?:candidates|applicants|residents)\s+(?:must\s+)?(?:be\s+located|live|reside)\s+in\s+(?:one of\s+)?(?:the\s+following\s+)?states?',
        r'(?:following\s+states|states\s+listed\s+below)\s*[:\-]?',
    )
    matches=[m for p in introductions for m in re.finditer(p, desc, re.I)]
    for m in sorted(matches, key=lambda x:x.start()):
        after=desc[m.end():m.end()+380]
        after=re.split(r'(?<=[.;\n])\s*(?:please|note|the\s+role|benefits|salary|compensation|requirements|responsibilities)\b', after, maxsplit=1, flags=re.I)[0]
        found=states_in(after)
        if len(found)>=2:
            return found
    return None


def california_exclusion(text):
    # Covers exclusions and employer state restrictions, not incidental 'not CA'.
    p=(
        r'(?:excluding|exclude(?:s|d)?|except|not available in|cannot hire in|'
        r'unable to hire in|not hiring in|ineligible in|not eligible in|'
        r'no applications from|not accepting applications from|cannot employ in)'
        r'\s+(?:the\s+state\s+of\s+)?(?:california|CA)\b',
        r'(?:california|CA)\s+(?:residents?|candidates?|applicants?)\s+'
        r'(?:are\s+)?(?:not\s+eligible|excluded|ineligible|cannot apply)',
    )
    return any(re.search(s,text,re.I) for s in p)


def salary_bounds_annual(job):
    """Parse explicitly annual *base pay* range only, not company revenue.

    Returns (low, high) or None. Hourly amounts and benefits are not assumed
    to reach a relocation threshold even when annualized.
    """
    salaryfield=str(job.get('salary') or job.get('compensation') or '')
    desc=str(job.get('description') or '')
    segments=[salaryfield] if salaryfield else []
    for match in re.finditer(r'\b(?:salary|compensation|base pay|base salary|pay range|annual pay|annualized salary)\b',desc,re.I):
        segments.append(desc[max(0, match.start()-12):match.end()+230])
    # Some job boards show "$110k - $140k per year" with no salary label.
    for match in re.finditer(r'\$\s*\d[\d,]*(?:\.\d+)?\s*[kK]?\s*[-–—to]{1,4}\s*\$?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?\s*(?:/\s*year|per\s+year|annually|/\s*yr)',desc,re.I):
        segments.append(match.group(0))
    nums=[]
    dollar=re.compile(r'\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s*[kK])\b')
    for s in segments:
        if not s or re.search(r'\b(?:hour|hourly|/hr|per hr|per hour)\b',s,re.I):
            continue
        values=[]
        for m in dollar.finditer(s):
            raw=m.group(1).replace(',','').replace(' ','')
            val=float(raw[:-1])*1000 if raw.lower().endswith('k') else float(raw)
            if 10000<=val<=1000000:
                values.append(int(val))
        if values:
            nums.extend(values[:2])
            break
    return (min(nums),max(nums)) if nums else None


def evaluate_geography(job, min_relocation_salary=100000):
    """Return status, human explanation, and blocking/verification attributes.

    status: local, remote_ca, relocation, review, blocked
    """
    location=str(job.get('location') or '').strip()
    desc=str(job.get('description') or '')
    loc=location.lower()
    txt=f'{location}\n{desc}'
    remote=(has(loc,'remote') or has(loc,'work from home') or has(loc,'wfh') or
            bool(re.search(r'\b(?:fully|100%)\s+remote\b',desc,re.I)))
    # Description-only remote status is uncertain if listing says the contrary.
    if re.search(r'\b(?:not remote|no remote work|remote work (?:is )?not available)\b',desc,re.I):
        remote=False

    if california_exclusion(txt):
        return {'status':'blocked','label':'Not eligible','reason':'Listing explicitly excludes California applicants.', 'verified':True}
    approved=hiring_state_list(desc)
    if approved is not None and 'CA' not in approved:
        return {'status':'blocked','label':'Not eligible','reason':'Employer hiring-state whitelist does not include California.', 'verified':True}

    foreign=(any(has(loc,p) for p in FOREIGN) or
             bool(re.search(r'(?<![A-Za-z])B\.\s*C\.(?![A-Za-z])', location, re.I)))
    us_in_loc=(any(has(loc,p) for p in ('united states','usa','u.s.','us','california','san diego'))
               or bool(states_in(location)))
    foreign_title=bool(FOREIGN_TITLE_LOCATION.search(str(job.get('title') or '')))
    if foreign and us_in_loc:
        return {'status':'review','label':'Multiple country locations',
                'reason':'Location mentions both U.S. and non-U.S. locations; verify the assigned work country and CA eligibility.',
                'verified':False}
    if foreign:
        return {'status':'blocked','label':'Outside U.S.',
                'reason':'Job location specifies a location outside the United States.', 'verified':True}
    if foreign_title and us_in_loc:
        return {'status':'review','label':'Conflicting title and location',
                'reason':'Title specifies a non-U.S. region, but location also mentions the United States; verify the actual hiring region.',
                'verified':False}
    if foreign_title:
        return {'status':'blocked','label':'Outside U.S. — title region',
                'reason':'Job title explicitly specifies a non-U.S. region without a U.S. work location; confirm on employer posting.',
                'verified':False}

    if remote:
        if approved is not None and 'CA' in approved:
            return {'status':'remote_ca','label':'Remote — CA allowed','reason':'Remote listing explicitly includes California in its eligible hiring states.', 'verified':True}
        # CA mentioned as an *office* (e.g. "San Francisco, CA, NY, or US Remote")
        # is not proof that employees may work remotely anywhere in California.
        nonlocal_ca=any(has(loc, city) for city in NONLOCAL_CA) or has(loc,'bay area')
        other_state=any(code!='CA' for code in states_in(location))
        local=any(has(loc, place) for place in LOCAL_PLACES)
        state_ca=has(loc,'california') or bool(re.search(r'(?<!\w)CA(?!\w)',location))
        if local and not (nonlocal_ca or other_state):
            return {'status':'remote_ca','label':'Remote — San Diego','reason':'Remote job location explicitly specifies the San Diego area; confirm remote arrangement.', 'verified':True}
        if state_ca and not(nonlocal_ca or other_state or local):
            return {'status':'remote_ca','label':'Remote — California','reason':'Job location explicitly names California as the remote region; confirm employer hiring policy.', 'verified':True}
        return {'status':'review','label':'Verify CA remote eligibility','reason':'U.S.-remote role or nonlocal CA office does not explicitly confirm California or San Diego-based remote hiring; check employer restrictions.', 'verified':False}

    # No remote indication. Local exact city/area wins only when no conflicting
    # non-local named site occurs; multisite listings require human review.
    local=any(has(loc,p) for p in LOCAL_PLACES)
    nonlocal_ca=any(has(loc,p) for p in NONLOCAL_CA)
    other_state=any(code!='CA' for code in states_in(location))
    if local and not(nonlocal_ca or other_state):
        return {'status':'local','label':'San Diego area','reason':'Office location appears in the San Diego area; commute/onsite details should be confirmed.', 'verified':True}
    if local and (nonlocal_ca or other_state):
        return {'status':'review','label':'Multiple job locations','reason':'Listing mentions both local and nonlocal locations; verify the actual assigned office.', 'verified':False}

    has_ca=has(loc,'california') or bool(re.search(r'(?<!\w)CA(?!\w)',location))
    known_nonlocal=(nonlocal_ca or other_state or any(has(loc,x) for x in ('new york city','washington dc','portland','boston','seattle','denver','chicago','austin','houston','atlanta','phoenix','pittsburgh','baltimore','miami','philadelphia','nashville','new york','dallas')))
    if known_nonlocal:
        pay=salary_bounds_annual(job)
        if pay and pay[1] < min_relocation_salary:
            return {'status':'blocked','label':'Relocation pay too low','reason':f'Nonlocal job lists annual pay up to ${pay[1]:,}, below ${min_relocation_salary:,} relocation minimum.', 'verified':True}
        if pay and pay[0] >= min_relocation_salary:
            return {'status':'relocation','label':'Relocation pay meets threshold','reason':f'Nonlocal role has listed annual base pay from ${pay[0]:,}; verify offer and employment terms.', 'verified':False}
        if pay:
            msg=f'Nonlocal job salary spans ${pay[0]:,}–${pay[1]:,}; an offer of ${min_relocation_salary:,}+ is not guaranteed.'
        else:
            msg=f'Nonlocal role: cannot verify an annual base salary of at least ${min_relocation_salary:,}.'
        return {'status':'review','label':'Verify relocation pay','reason':msg, 'verified':False}

    if has_ca:
        return {'status':'review','label':'Verify CA city','reason':'California is listed but a San Diego-area worksite is not established.', 'verified':False}
    return {'status':'review','label':'Verify job location','reason':'Could not establish whether role is local, California-eligible remote, or meets relocation requirements.', 'verified':False}
