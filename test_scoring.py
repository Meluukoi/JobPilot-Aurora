"""Standalone regression checks. Run: py test_scoring.py"""
import json
from pathlib import Path
from scorer import score_job

profile=json.loads((Path(__file__).parent/'profile.json').read_text(encoding='utf-8'))

def check(label, title, location, desc, cond):
    result=score_job({'title':title,'location':location,'description':desc},profile)
    print(f"{label:31s} {result['score']:3d}%  {result['recommendation']}")
    for item in result['gaps'] + result['hard_flags']:
        print('   -',item)
    assert cond(result), f'FAILED: {label}'

check('US remote / unknown CA', 'Technical Support Specialist','US-Remote',
      'Troubleshooting customers. Experience with Excel. Two years preferred in technical support.',
      lambda r: 'SKIP / NOT ELIGIBLE' not in r['recommendation'] and any('California' in g for g in r['gaps']))
check('Required 2 years SaaS', 'Technical Support Specialist','US-Remote',
      'Required Qualifications: 2+ years of software support experience using Salesforce.',
      lambda r: r['score'] < 75 and any('Requires 2+' in g for g in r['gaps']))
check('Preferred 2 years SaaS', 'Technical Support Specialist','US-Remote',
      'Preferred Qualifications: 2+ years of software support experience using Salesforce.',
      lambda r: not any('Requires 2+' in g for g in r['gaps']))
check('Required 5 years implementation','Implementation Specialist','United States - Remote',
      'Minimum Qualifications: 5+ years of implementation experience; Jira and Salesforce required.',
      lambda r: r['score'] < 65 and any('Requires 5+' in g for g in r['gaps']))
check('Remote Ireland', 'Security Operations Analyst', 'Ireland (Remote)',
      'Junior security operations; Splunk knowledge.',
      lambda r: r['recommendation']=='SKIP / NOT ELIGIBLE')
check('US remote California excluded','Technical Support Specialist','Remote NA',
      'Candidates only in approved hiring states: AZ, CO, TX, FL, WA, OR, NV. No applications from California.',
      lambda r: r['recommendation']=='SKIP / NOT ELIGIBLE')
check('Junior SDR role','Sales Development Representative','Remote - United States',
      'Entry-level outbound calls and Salesforce CRM. No prior sales experience required.',
      lambda r: r['matched_family']=='Sales / Business Development')
check('Staff GRC','Staff GRC Analyst','Remote - United States',
      'Security compliance and risk audits. 7+ years of GRC experience.',
      lambda r: r['score'] < 40)
check('Bachelor required','IT Support Specialist','California',
      "Required Qualifications: Bachelor's degree required. Windows troubleshooting.",
      lambda r: any('Degree requirement' in h for h in r['hard_flags']))
check('Bachelor preferred','IT Support Specialist','California',
      "Preferred Qualifications: Bachelor's degree in computer science. Windows troubleshooting.",
      lambda r: not any('Degree requirement' in h for h in r['hard_flags']))
print('\nAll tests passed.')
