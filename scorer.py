"""JobPilot v0.2 scoring precision â€” general vs. specialized experience.

Conservative and explainable heuristic. A score is NOT a hiring probability.
Works with the existing JobPilot v0.2 app.py and profile.json.
"""

import re
from candidate_matching import assess_candidate
from geography import evaluate_geography

US_STATES = {
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
    'WI':'Wisconsin','WY':'Wyoming','DC':'District of Columbia'
}

FOREIGN_LOCATIONS = (
    'ireland','dublin','australia','sydney','melbourne','united kingdom','london',
    'emea','apac','europe','germany','berlin','france','paris','spain','madrid',
    'netherlands','amsterdam','singapore','india','bengaluru','bangalore',
    'canada','toronto','vancouver','mexico city','new zealand','auckland',
    'brazil','poland','warsaw', 'czech republic'
)

PREFERRED_HEADERS = (
    'preferred qualifications','preferred experience','desired qualifications',
    'desired experience','nice to have','nice-to-have','bonus points',
    'it would be great if','it would be nice if','what would set you apart',
    'you might also have','additional qualifications', 'preferred skills'
)
REQUIRED_HEADERS = (
    'required qualifications','minimum qualifications','basic qualifications',
    'minimum requirements','requirements','what you will need',
    "what you'll need",'must have','what you bring','your qualifications',
    'required experience', "what you'll bring", 'what youâ€™ll bring'
)

YEAR_REGEX = re.compile(
    r'(?<!\d)(\d{1,2})(?:\s*(?:[-â€“]|to)\s*(\d{1,2}))?\s*\+?\s*'
    r'(?:years?|yrs?)\b',
    re.I,
)


# Additional clearly identifiable entry-level titles; used only if the matching
# role family already exists in the user's editable profile.json.
# A title match is NOT evidence that all job requirements are satisfied.
ROLE_TITLE_ALIASES = {
    'IT Support': (
        'it technician', 'it service technician', 'it service desk',
        'helpdesk technician', 'helpdesk analyst', 'computer support',
        'desktop technician', 'desktop services', 'end user support',
        'end-user support', 'technical assistance center', 'field support technician',
        'information technology support', 'technical service representative',
    ),
    'Customer/Client Support': (
        'patient access', 'patient service', 'patient support', 'patient services',
        'patient scheduling', 'patient coordinator', 'patient care coordinator',
        'patient experience', 'customer experience', 'customer care',
        'call center', 'contact center', 'member support', 'member service',
        'client service', 'client success', 'client relations',
        'customer success', 'care specialist', 'advanced care specialist',
        'intake specialist', 'intake coordinator', 'appointment scheduler',
        'patient scheduler', 'front desk', 'front office', 'receptionist',
    ),
    'Operations': (
        'office support', 'office assistant', 'office coordinator',
        'administrative assistant', 'administrative coordinator',
        'administrative specialist', 'business office representative',
        'business office clerk', 'business office associate',
        'operations assistant', 'service coordinator', 'data entry',
        'records specialist', 'records clerk',
    ),
    'Sales / Business Development': (
        'sales representative', 'inside sales', 'sales associate',
        'outbound sales', 'sales support', 'business development associate',
    ),
    'Data/Reporting': (
        'reporting specialist', 'data reporting', 'data analyst i',
        'data quality specialist', 'data coordinator',
    ),
    'Cybersecurity': (
        'soc technician', 'soc operator', 'security operations center',
        'identity and access analyst', 'identity access specialist',
        'information security specialist', 'it security specialist',
    ),
}


def _role_family_for_title(title, profile):
    """Choose the most specific title match; don't call patient care IT support.

    The old implementation took the first matching phrase, e.g. generic
    `support specialist` often misclassified `Patient Support Specialist` as IT.
    """
    choices=[]
    for family, phrases in (profile.get('target_role_families') or {}).items():
        for phrase in phrases:
            if phrase_in_title(phrase, title):
                # Unqualified support-specialist label is ambiguous, not IT.
                if family == 'IT Support' and norm(phrase) == 'support specialist' \
                        and not any(has_phrase(title, word) for word in ('it', 'technical', 'computer', 'desktop', 'help desk', 'helpdesk')):
                    continue
                choices.append((len(norm(phrase)), 1, family, phrase))
        for phrase in ROLE_TITLE_ALIASES.get(family, ()):
            if phrase_in_title(phrase, title):
                choices.append((len(norm(phrase)), 0, family, phrase))
    if not choices:
        return None, None, False
    # Longest role-specific match wins; existing profile phrases break ties.
    _, is_configured, family, phrase = max(choices, key=lambda x: (x[0],x[1]))
    return family, phrase, bool(is_configured)


def _has_general_customer_service_history(candidate):
    """Credit >=1 year of paid *general* customer service only when supported.

    A year-range spanning two full calendar boundaries is a conservative proxy
    for at least 12 months. We do not infer exact months, job-specific healthcare
    work, phone-call center tenure, or professional IT experience from retail.
    """
    if not candidate or not str(candidate.get('status', '')).startswith('approved_by_user'):
        return False
    for job in (candidate.get('professional_experience') or []):
        activities=' '.join(str(a).lower() for a in (job.get('activities') or []))
        role=f"{job.get('title','')} {activities}".lower()
        dates=str(job.get('dates') or '')
        years=re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', dates)
        if len(years)<2 or int(years[1])-int(years[0])<2:
            continue
        if any(t in role for t in ('customer', 'retail', 'cashier', 'refund', 'sales associate')):
            return True
    return False


def _plausible_two_years_general_customer_service(candidate):
    """Calendar-year dates may be consistent with >=2 years but don't prove it.

    Employment dates stated only as 2023-2025 lack months, so it can support manual review of
    a 2-year general customer-service requirement, NOT automatically certify it.
    Do not count lab time as paid employment or infer full-time desk tenure.
    """
    if not candidate or not str(candidate.get('status', '')).startswith('approved_by_user'):
        return False
    for employment in candidate.get('professional_experience') or []:
        years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', str(employment.get('dates') or ''))
        activity = (' '.join(employment.get('activities') or []) + ' ' +
                    str(employment.get('title') or '')).lower()
        if len(years) >= 2 and int(years[1]) - int(years[0]) >= 2 and any(
            token in activity for token in ('customer','cashier','retail','refund','sales associate')
        ):
            return True
    return False


def _plain_customer_service_requirement(clause):
    """Whether a stated tenure requirement accepts ordinary customer service.

    Inspect only the requirement clause, never the entire job description; a
    later sentence mentioning insurance or IT must not change this requirement.
    Do not equate retail with *technical*, healthcare, or call-center tenure.
    """
    generic = bool(re.search(
        r"\b(customer service|customer support|customer experience|customer[- ]facing|"
        r"retail|client service|client support|"
        r"customer success or (?:a )?(?:customer )?service environment)\b",
        clause, re.I,
    ))
    specialized = bool(re.search(
        r"\b(healthcare|health care|patient access|patient accounting|billing|"
        r"claims|authorization|insurance|call center|contact center|"
        r"technical support|technical customer service|technical customer|help desk|service desk|information technology|"
        r"it support|cybersecurity|security operations|salesforce administration|"
        r"software development|saas support|b2b saas)\b", clause, re.I,
    ))
    return generic and not specialized


def _requirement_clause(text, match):
    """Return only the local line/semicolon sentence containing a tenure.

    Requirements are frequently individual bullets. Long flattened paragraphs
    cannot safely prove whether nearby technology words describe the same
    experience requirement; those ambiguous cases remain for review.
    """
    left = max(text.rfind('\n', 0, match.start()),
               text.rfind(';', 0, match.start()),
               text.rfind('.', 0, match.start())) + 1
    right_candidates = [text.find(sep, match.end()) for sep in ('\n', ';', '.')]
    right = min((x for x in right_candidates if x >= 0), default=len(text))
    return text[left:min(right, match.end()+170)].strip()


def _domain_qualification_hits(title, description, terms):
    """Penalize unrelated specializations only when *the applicant* needs them.

    A SaaS company serving attorneys does not need applicants to be attorneys.
    Presence of a domain word in product/customer descriptions is not a job
    requirement. Wording is intentionally conservative and auditable.
    """
    hits=[]
    for term in terms:
        if not has_phrase(description, term) and not has_phrase(title, term):
            continue
        if has_phrase(title, term):
            # 'Attorney Success Specialist' can be a customer role, not law practice.
            if norm(term)=='attorney' and any(has_phrase(title, word) for word in (
                    'success','support','relations','services','coordinator')):
                continue
            hits.append(term)
            continue
        for m in re.finditer(r'(?<!\w)' + re.escape(norm(term)) + r'(?!\w)',
                             norm(description)):
            before = norm(description)[max(0,m.start()-110):m.start()]
            after = norm(description)[m.end():m.end()+80]
            # E.g. 'must be a licensed attorney', 'CPA license required'.
            # A general 'working with attorneys' is NOT sufficient evidence.
            specific_required = bool(re.search(
                r'(?:must\s+(?:be|have|hold)|required\s+to\s+(?:be|have|hold)|'
                r'requires?\s+(?:a|an)?|valid|active|licensed|'
                r'current\s+license|\bcertified)\b[^.;\n]{0,70}$', before
            ) or re.search(
                r'^.{0,30}\b(?:license|licensure|certification|bar admission)\s+'
                r'(?:is\s+)?(?:required|mandatory)\b', after
            ))
            # Look for direct qualifications rather than general firm description.
            nearby = before[-60:] + ' ' + norm(term) + ' ' + after[:60]
            qualifications = bool(re.search(
                r'(?:required|minimum qualifications?|must have|you (?:have|bring)|'
                r'experience (?:in|with)|expertise (?:in|with))', nearby
            ))
            if specific_required or (qualifications and norm(term) != 'attorney'):
                hits.append(term)
                break
    return hits


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '').lower()).strip()


def has_phrase(text, phrase):
    """Match whole keywords, so e.g. SQL does not match an unrelated substring."""
    return re.search(r'(?<!\w)' + re.escape(norm(phrase)) + r'(?!\w)', norm(text)) is not None


def phrase_in_title(phrase, title):
    return has_phrase(title, phrase)


def _state_list(text):
    found = set()
    for abbr, state in US_STATES.items():
        if has_phrase(text, state) or re.search(r'(?<!\w)' + abbr + r'(?!\w)', text, re.I):
            found.add(abbr)
    return found


def _approved_states(desc):
    """Look for explicitly restricted lists; never treat random state mentions as a list."""
    txt = norm(desc)
    patterns = (
        'approved hiring states','approved states','eligible states',
        'hiring states','we can hire in','we are able to hire in',
        'candidates in the following states', 'residents of the following states',
        'available in the following states','we currently hire in',
        'we currently employ in','applications from the following states'
    )
    positions = [txt.find(p) for p in patterns if txt.find(p) != -1]
    if not positions:
        return None
    # Constrain to the list immediately following the trigger.
    window = txt[min(positions):min(positions)+520]
    # Only parse the stated list, not later paragraphs about other states.
    window = re.split(r'[.;]|\b(?:note:|please note|applications? from|candidates from)\b', window, maxsplit=1)[0]
    states = _state_list(window)
    return states if len(states) >= 3 else None


def assess_location(location, description, profile):
    loc, desc = norm(location), norm(description)
    home = str(profile.get('home_state', 'CA')).upper()
    reasons, gaps, hard = [], [], []
    points = 0
    verified = False

    exclude_ca = (
        r'(?:excluding|except|excludes|exclude|not available in|cannot hire in|'
        r'unable to hire in|not hiring in|ineligible in|not eligible in|no applications from|not accepting applications from)\s+'
        r'(?:the state of\s+)?(?:california|ca)\b',
    )
    if any(re.search(p, loc + ' ' + desc) for p in exclude_ca):
        return -45, reasons, gaps, ['Hiring location explicitly excludes California'], False

    allowed = _approved_states(desc)
    if allowed is not None:
        if home not in allowed:
            return -45, reasons, gaps, [f'Allowed hiring-state list does not include {home}'], False
        reasons.append(f'Hiring-state list includes {home}')
        verified = True
        points += 8

    # Explicit job geography takes precedence over the word 'remote'.
    explicit_us = bool(re.search(
        r'(?<!\w)(?:us|usa|u\.s\.|united states)\s*[-,/() ]*remote\b|'
        r'\bremote\s*[-,/() ]*(?:us|usa|u\.s\.|united states)\b|'
        r'\b(?:united states|usa|u\.s\.)\s*\(remote\)', loc
    )) or 'united states' in loc
    explicit_ca = any(x in loc for x in ('california', 'san diego', 'san francisco', 'los angeles'))

    # 'Ireland (Remote)' should not be considered eligible just because it is remote.
    foreign = any(has_phrase(loc, x) for x in FOREIGN_LOCATIONS)
    if foreign and not (explicit_us or explicit_ca):
        return -45, reasons, gaps, [f'Role lists non-U.S. location: {location}'], False

    if explicit_ca:
        reasons.append('California location matches search')
        return points + 8, reasons, gaps, hard, True

    if explicit_us:
        if not verified and ('remote' in loc or 'virtual' in loc):
            gaps.append('U.S.-remote listing; confirm California is eligible')
        reasons.append('U.S. location matches search')
        return points + (5 if not verified else 0), reasons, gaps, hard, verified

    if 'north america' in loc or 'remote na' in loc or 'na remote' in loc:
        gaps.append('North America remote; California eligibility unverified')
        return points, reasons, gaps, hard, verified

    if 'remote' in loc or 'virtual' in loc:
        gaps.append('Remote location unspecified; verify U.S./California eligibility')
        return points, reasons, gaps, hard, verified

    if loc:
        gaps.append('Location needs manual verification')
    else:
        gaps.append('Location not provided')
    return points, reasons, gaps, hard, verified


def _section_kind(text, match_start):
    """Choose the nearest recognizable required/preferred heading before a term.

    Job-board descriptions may be flattened to one line. If no clear heading,
    classification is 'unclear', not automatically 'required'.
    """
    before = text[max(0, match_start - 1500):match_start]
    candidates = []
    for heading in PREFERRED_HEADERS:
        idx = before.rfind(heading)
        if idx >= 0:
            candidates.append((idx, 'preferred'))
    for heading in REQUIRED_HEADERS:
        idx = before.rfind(heading)
        if idx >= 0:
            candidates.append((idx, 'required'))
    if candidates:
        return max(candidates)[1]
    return 'unclear'


def _experience_requirements(description):
    # Keep line breaks to avoid conflating distinct required and preferred bullets.
    text = re.sub(r'[ \t]+', ' ', str(description or '').lower())
    result=[]
    for match in YEAR_REGEX.finditer(text):
        years=int(match.group(1))  # lower bound of a 1-3-year requirement
        if years > 15:
            continue
        clause = _requirement_clause(text, match)
        # If no experience or relevant work domain appears, this can simply be
        # '20 years in business' or a company history statement.
        if not re.search(r'\b(experience|working|supporting|using|background|'
                         r'customer service|customer support|customer success|front office|'
                         r'front desk|revenue cycle|preauthorization|radiology|scheduling|'
                         r'customer experience|implementation|finance|banking|risk|'
                         r'patient access|retail|technical support|help desk|'
                         r'service desk|healthcare|billing|security|software|'
                         r'operations|sales|accounting|administration)\b', clause):
            continue
        if re.search(r'\b(?:company|brand|organization|founded|in business|serving clients)' , clause) and \
                not re.search(r'\b(?:required|qualifications|preferred|must have|candidate)\b',clause):
            continue
        kind=_section_kind(text,match.start())
        # Explicit indicators in the same clause outrank a distant heading.
        # '2 years experience, ideally in banking' still requires two years.
        # 'Ideally' modifies the industry, not the tenure requirement.
        preferred_marker = bool(re.search(r'\b(preferred|desired|a plus|nice to have|bonus)\b',clause))
        ideally_tenure = bool(re.search(r'\bideally\s+(?:\d|\w+\s+years?\b)', clause))
        if preferred_marker or ideally_tenure:
            kind='preferred'
        elif re.search(r'\b(required|must have|minimum|at least|need to have)\b',clause):
            kind='required'
        result.append((years,kind,_plain_customer_service_requirement(clause)))
    return result


def _assess_experience(description, profile, family):
    requirements=_experience_requirements(description)
    if not requirements:
        return 0, [], [], False
    candidate=profile.get('candidate_profile') or {}
    has_customer_history=_has_general_customer_service_history(candidate)
    reasons,gaps=[],[]
    role_years=(profile.get('role_experience_years') or {})
    min_years=role_years.get(family) if isinstance(role_years,dict) else None
    required=[x for x in requirements if x[1]=='required']
    preferred=[x for x in requirements if x[1]=='preferred']
    unclear=[x for x in requirements if x[1]=='unclear']

    if required:
        unmet=[]
        uncertain_tenure=False
        for years,kind,is_general_customer in required:
            if is_general_customer and has_customer_history:
                if years <= 1:
                    reasons.append('Paid retail work supports a 1-year general customer-service requirement')
                    continue
                if years == 2 and _plausible_two_years_general_customer_service(candidate):
                    uncertain_tenure=True
                    reasons.append('Paid retail customer service is relevant to this general support role')
                    gaps.append('2+ years general customer service requested; exact paid tenure and employer acceptance need verification')
                    continue
            if min_years is not None and min_years >= years:
                reasons.append(f'Approved profile shows {min_years} years in this role family')
                continue
            unmet.append(years)
        if not unmet:
            # Requirement seems relevant, but the exact 2-year duration is not
            # verified by year-only dates. Do not label a perfect match.
            if uncertain_tenure:
                return -2, reasons, gaps, True
            return 4, reasons, gaps, False
        years=max(unmet)
        penalty=10 if years<=1 else 19 if years==2 else min(37,22+(years-3)*4)
        gaps.append(f'Requires {years}+ years of relevant experience; not verified by profile')
        return -penalty, reasons, gaps, True

    if preferred:
        unverified=[years for years,_,generic in preferred
                    if not ((generic and years<=1 and has_customer_history)
                            or (min_years is not None and min_years >= years))]
        if unverified:
            years=max(unverified)
            gaps.append(f'Prefers {years}+ years of relevant experience (not automatically disqualifying)')
            return (-5 if years<=2 else -8), reasons, gaps, False
        return 2, ['Paid or profile-supported experience aligns with stated preference'], gaps, False

    years=max(x[0] for x in unclear)
    if all(x[2] and x[0]<=1 for x in unclear) and has_customer_history:
        return 1, ['Paid general customer-service experience aligns with 1-year mention'], gaps, False
    if all(x[2] and x[0]<=2 for x in unclear) and has_customer_history \
            and _plausible_two_years_general_customer_service(candidate):
        gaps.append('2-year general service experience mentioned; confirm minimum requirement and exact tenure')
        return -2, ['Paid retail customer service is relevant'], gaps, True
    gaps.append(f'Mentions {years}+ years of experience; required vs. preferred unclear')
    return (-8 if years<=2 else -14), reasons, gaps, True


def _specialized_role_gaps(title, description, candidate):
    """Detect domain-specific training not proven by general customer service.

    Only job-title-led specialties are penalized here. A casual mention of
    'fraud', 'risk', or 'payroll' in a company's marketing text is not enough.
    """
    title=norm(title)
    desc=norm(description)
    issues=[]
    delta=0
    caution=False
    # Fincrime/risk customer support is materially different from standard
    # customer service even if they share most soft skills.
    financial_risk_title = bool(re.search(r'\b(risk|fraud|aml|kyc|sanctions)\b', title))
    financial_risk_content = any(has_phrase(desc, x) for x in (
        'anti-money laundering', 'aml', 'sanctions', 'fincrime',
        'risk teams', 'financial crime', 'transaction monitoring',
    ))
    if financial_risk_title and financial_risk_content:
        delta-=22
        issues.append('Specialized financial risk/AML knowledge and operations are required; paid experience not demonstrated')
        caution=True
    if has_phrase(title,'payroll') and any(has_phrase(desc,x) for x in (
        'payroll', 'tax withholding', 'payroll tax', 'payroll compliance', 'garnishment'
    )):
        delta-=20
        issues.append('Specialized payroll operations/compliance experience not demonstrated')
        caution=True
    # A retail cashier has customer service and payment experience, but that
    # is not the same as prospecting and quota-carrying B2B sales.
    if (any(has_phrase(title,x) for x in (
        'business development representative', 'sales development representative',
        'business development rep', 'sales development rep'
    )) and any(has_phrase(desc,x) for x in (
        'outbound', 'prospecting', 'cold calling', 'cold outreach', 'lead generation',
        'sales pipeline', 'quota', 'lead qualification'
    ))):
        delta-=8
        issues.append('Outbound B2B prospecting experience not demonstrated by cashier/customer-service work')
    return delta,issues,caution


def _assess_degree(description, profile):
    if profile.get('degree_status') != 'no_bachelors':
        return 0, [], False
    text = norm(description)
    if not re.search(r"\bbachelor(?:'s|s)?\b|\bbs\s*degree\b", text):
        return 0, [], False
    pos = re.search(r"\bbachelor(?:'s|s)?\b|\bbs\s*degree\b", text).start()
    surrounding = text[max(0,pos-85):pos+140]
    kind = _section_kind(text,pos)
    if re.search(r'\b(preferred|nice to have|bonus|a plus)\b', surrounding) or kind=='preferred':
        return -2, ["Bachelor's degree preferred; not necessarily required"], False
    if re.search(r'\b(required|must have|minimum|mandatory)\b', surrounding) or kind=='required':
        return -28, ["Bachelor's degree appears required; check equivalency language"], True
    return -5, ["Bachelor's degree mentioned; check whether required"], False



def requires_international_travel(description):
    """Detect explicit mandatory international travel requirements.

    Conservative rule: ambiguous, optional, and preferred travel
    references are not treated as confirmed exclusions.
    """
    text = norm(description or "")

    patterns = (
        r"\binternational travel (?:is |will be )?(?:required|mandatory|essential)\b",
        r"\b(?:required|mandatory) international travel\b",
        r"\bmust (?:be willing to |have the ability to )?travel internationally\b",
        r"\b(?:required|expected) to travel internationally\b",
        r"\btravel internationally (?:is )?(?:required|mandatory)\b",
        r"\b(?:must|required to) travel (?:outside the (?:united states|u\\.s\\.|usa)|overseas)\b",
    )

    for sentence in re.split(r"[.;\n]+", text):
        if not any(re.search(p, sentence) for p in patterns):
            continue

        if re.search(
            r"\b(?:preferred but not required|optional|not required|"
            r"not mandatory|travel experience)\b",
            sentence,
        ):
            continue

        return True

    return False


def score_job(job, profile):
    title = norm(job.get('title'))
    desc = norm(job.get('description'))
    location = job.get('location') or ''
    blob = title + ' ' + desc
    score = 20
    reasons, gaps, hard = [], [], []

    family, family_signal, direct_profile_match = _role_family_for_title(title, profile)
    if family:
        # A clear related job title is important, but it's not a job offer.
        score += 35 if direct_profile_match else 30
        reasons.append(f'Role family: {family} (title: {family_signal})')
    else:
        score -= 10
        gaps.append('Title outside selected target roles')

    # Keyword overlap alone is not proof of professional experience.
    # Treat as keyword overlap ONLY. A future approved profile can assign evidence.
    skills = [s for s in profile.get('skills',[]) if has_phrase(blob, s)]
    if skills:
        score += min(10, len(skills)*2)
        reasons.append('Matched keywords (evidence classified below): ' + ', '.join(skills[:6]))

    candidate = profile.get('candidate_profile') or {}
    relocation_min = (candidate.get('preferences') or {}).get('relocation', {}).get('minimum_annual_salary_usd', 100000)
    geo = evaluate_geography(job, relocation_min)

    international_travel_blocked = requires_international_travel(desc)
    if international_travel_blocked:
        hard.append("Mandatory international travel required")

    loc_verified = geo['verified'] and geo['status'] in ('local', 'remote_ca', 'relocation')
    if geo['status'] in ('local', 'remote_ca'):
        score += 8
        reasons.append(geo['reason'])
    elif geo['status'] == 'relocation':
        reasons.append(geo['reason'])
    elif geo['status'] == 'review':
        score -= 8
        gaps.append(geo['reason'])
    elif geo['status'] == 'blocked':
        score -= 42
        hard.append(geo['reason'])

    senior_terms = next((term for term in profile.get('exclude_title_terms',[]) if phrase_in_title(term,title)), None)
    if senior_terms:
        score -= 40
        hard.append(f'Senior-level title: {senior_terms}')
    else:
        soft = next((t for t in profile.get('soft_penalty_title_terms',[]) if phrase_in_title(t,title)),None)
        if soft:
            score -= 18
            gaps.append(f'Potential seniority mismatch: {soft}')

    exp_delta, exp_reasons, exp_gaps, exp_unverified = _assess_experience(desc, profile, family)
    score += exp_delta
    reasons.extend(exp_reasons)
    gaps.extend(exp_gaps)

    specialized_delta, specialized_gaps, specialized_caution = _specialized_role_gaps(
        title, desc, candidate
    )
    score += specialized_delta
    gaps.extend(specialized_gaps)

    degree_delta, degree_gaps, degree_unverified = _assess_degree(desc, profile)
    score += degree_delta
    gaps.extend(degree_gaps)
    if degree_unverified:
        hard.append('Degree requirement not met/verified')

    domain_hits = _domain_qualification_hits(title, desc, profile.get('domain_penalty_terms', []))
    if domain_hits:
        score -= min(30, 12 + len(domain_hits)*6)
        gaps.append('Required specialized qualification or job domain mismatch: '
                    + ', '.join(domain_hits[:4]))

    advanced = [p for p in profile.get('advanced_specialty_terms',[]) if has_phrase(desc,p)]
    if len(advanced)>=2:
        score -= min(22, 8 + len(advanced)*3)
        gaps.append('Advanced specialty requirements: ' + ', '.join(advanced[:4]))

    citizenship = ("u.s. citizenship required", "us citizenship required", "must be a u.s. citizen", "must be a us citizen")
    if any(x in desc for x in citizenship) and profile.get('citizenship_status','unknown')=='unknown':
        score -= 8
        gaps.append('U.S. citizenship requirement needs verification')

    # Evidence-based candidate enrichment. Never convert labs or tool exposure
    # into paid years of professional employment.
    candidate = profile.get('candidate_profile')
    if candidate and str(candidate.get('status', '')).startswith('approved_by_user'):
        delta, candidate_reasons, candidate_gaps, candidate_hard = assess_candidate(
            dict(job, _geo_handled=True), candidate, family
        )
        score += delta
        reasons.extend(candidate_reasons)
        gaps.extend(candidate_gaps)
        hard.extend(candidate_hard)

    # Title-level signals only; do not scan description for incidental 'junior' references.
    if any(has_phrase(title,c) for c in ('entry level','entry-level','junior','level i','level 1','analyst i','specialist i')):
        score += 8
        reasons.append('Entry-level title')

    score = max(0, min(100, round(score)))
    # A geography mismatch must never become a high-scoring recommendation.
    if geo['status'] == 'blocked':
        score = min(score, 20)
    elif geo['status'] == 'review':
        score = min(score, 69)

    clearance_blocked = any('active clearance' in x.lower() for x in hard)
    if geo['status'] == 'blocked' or clearance_blocked or international_travel_blocked:
        recommendation = 'SKIP / NOT ELIGIBLE'
    elif geo['status'] == 'review':
        recommendation = 'VERIFY ELIGIBILITY'
    elif hard and (senior_terms or degree_unverified):
        recommendation = 'SKIP / LOW PRIORITY'
    elif specialized_caution:
        # Do not recommend financially specialized Risk/Payroll support as a
        # general customer-service match regardless of keyword-heavy score.
        recommendation = 'CONSIDER / STRETCH' if score >= 38 else 'SKIP / LOW PRIORITY'
    elif exp_unverified and any('requires ' in x.lower() and 'not verified' in x.lower() for x in gaps):
        recommendation = 'SKIP / LOW PRIORITY' if score < 65 else 'CONSIDER / STRETCH'
    elif score >= 78 and loc_verified and not exp_unverified and not hard and not gaps:
        recommendation = 'STRONG APPLY'
    elif score >= 55:
        recommendation = 'APPLY / REVIEW'
    elif score >= 38:
        recommendation = 'CONSIDER / STRETCH'
    else:
        recommendation = 'SKIP / LOW PRIORITY'

    return {
        'score': score,
        'recommendation': recommendation,
        'reasons': reasons,
        'gaps': gaps,
        'hard_flags': hard,
        'matched_family': family,
        'geo_status': geo['status'],
        'geo_label': geo['label'],
        'geo_reason': geo['reason'],
    }
