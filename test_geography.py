"""Run: python -m unittest -v test_geography.py
No API calls or changes to your saved data."""
import unittest
from geography import evaluate_geography, salary_bounds_annual
from scorer import score_job
import json
from pathlib import Path

CANDIDATE=json.loads((Path(__file__).parent/'candidate_profile.json').read_text())
PROFILE=json.loads((Path(__file__).parent/'profile.json').read_text())
PROFILE['candidate_profile']=CANDIDATE
PROFILE['citizenship_status']='us_citizen'


def item(location, description='',title='IT Support Specialist'):
    return {'title':title,'location':location,'description':description,
            'company':'ExampleCo','source':'Manual','source_id':'1'}

class EligibilityTests(unittest.TestCase):
    def assert_status(self, expected, location, description=''):
        actual=evaluate_geography(item(location,description))
        self.assertEqual(expected, actual['status'], actual)

    def test_local(self):
        self.assert_status('local','San Diego, CA (Onsite)')
        self.assert_status('local','Oceanside, California (Hybrid)')
    def test_distant_ca_is_not_local(self):
        self.assert_status('review','Los Angeles, CA')
        self.assert_status('review','San Francisco, CA')
    def test_us_remote_not_verified(self):
        self.assert_status('review','Remote (United States)')
        self.assert_status('review','Anywhere - Remote')
    def test_remote_california(self):
        self.assert_status('remote_ca','Remote - California')
        self.assert_status('remote_ca','Remote (USA)', 'Eligible states: CA, TX, NY')
    def test_exclusions(self):
        self.assert_status('blocked','Remote (United States)','Not hiring in California.')
        self.assert_status('blocked','Remote','Eligible states: TX, NY, FL')
    def test_non_us_remote(self):
        self.assert_status('blocked','Dublin, Ireland (Remote)')
        self.assert_status('blocked','Remote UK | remote')
        self.assert_status('blocked','Remote Costa Rica')
        self.assert_status('blocked','Delhi | hybrid')
        self.assert_status('review','Remote UK; United States')
    def test_country_region_in_title_when_location_only_says_remote(self):
        for title in ("Sales Development Representative (UK)",
                      "Customer Support Specialist [UK]",
                      "Implementation Specialist - Canada",
                      "Customer Support — United Kingdom"):
            with self.subTest(title=title):
                result=evaluate_geography(item('Remote', title=title), 75000)
                self.assertEqual('blocked', result['status'], result)
        self.assert_status('review', 'Remote')  # No title country restriction
        self.assertEqual('review', evaluate_geography(item(
            'Remote - United States', title='Sales Development Representative (UK)'), 75000)['status'])
        # This mentions a customer market, not an employment location.
        self.assertEqual('review', evaluate_geography(item(
            'Remote', title='Customer Support Specialist for UK customers'), 75000)['status'])

    def test_canadian_province_and_multicountry_locations(self):
        for location in ('British Columbia', 'Vancouver, BC',
                         'Remote - British Columbia, Canada', 'Victoria, B.C.'):
            with self.subTest(location=location):
                self.assertEqual('blocked', evaluate_geography(item(location),75000)['status'])
        for location in ('San Diego, CA / British Columbia',
                         'Remote UK; United States', 'Remote USA / BC'):
            with self.subTest(location=location):
                result=evaluate_geography(item(location), 75000)
                self.assertEqual('review', result['status'], result)
        self.assertEqual('review', evaluate_geography(item('Ontario, CA'),75000)['status'])

    def test_foreign_hints_propagate_to_score_and_review(self):
        for j in (item('Remote', title='Sales Development Representative (UK)'),
                  item('British Columbia', title='Technical Support Specialist')):
            with self.subTest(title=j['title']):
                a=score_job(j, PROFILE)
                self.assertEqual('blocked',a['geo_status'])
                self.assertEqual('SKIP / NOT ELIGIBLE',a['recommendation'])
                self.assertLessEqual(a['score'],20)
                from opportunity_review import classify
                self.assertEqual('excluded',classify(j,a,None)[1])

    def test_unknown_and_multisite(self):
        self.assert_status('review','California')
        self.assert_status('review','San Diego, CA / Austin, TX')
    def test_relocation_salary(self):
        self.assert_status('blocked','Portland, OR','Base salary: $65,000 - $90,000 per year.')
        self.assert_status('relocation','Portland, OR','Base salary: $110,000 - $145,000 per year.')
        self.assert_status('review','Portland, OR','Salary: $90,000 - $115,000 annually.')
        self.assert_status('review','Portland, OR','Pay: $32 per hour')
        self.assert_status('review','Portland, OR','Competitive compensation')
    def test_scorer_respects_75000_relocation_floor(self):
        from copy import deepcopy
        p=deepcopy(PROFILE)
        p['candidate_profile']['preferences']['relocation']['minimum_annual_salary_usd']=75000
        # A salary below the configured minimum is blocked for relocation.
        a=score_job(item('Portland, OR','Base salary: $55,000 - $65,000 annually.'),p)
        self.assertEqual('blocked',a['geo_status'])
        # A range overlapping $75k must be reviewed, not automatically rejected.
        b=score_job(item('Portland, OR','Base salary: $65,000 - $85,000 annually.'),p)
        self.assertEqual('review',b['geo_status'])
        # Entire advertised range at or above the floor meets the pay screen.
        c=score_job(item('Portland, OR','Base salary: $80,000 - $95,000 annually.'),p)
        self.assertEqual('relocation',c['geo_status'])

    def test_salary_not_revenue(self):
        self.assertIsNone(salary_bounds_annual(item('Portland, OR','Our company has $150,000 in annual revenue.')))
    def test_scorer_blocks_and_caps(self):
        # Use the user's actual relocation floor rather than assuming $100,000.
        # Both $75,000 and $100,000 configurations should pass this test.
        floor=int(PROFILE['candidate_profile']['preferences']['relocation']['minimum_annual_salary_usd'])
        low=max(10000, floor-20000)
        high=max(20000, floor-10000)
        a=score_job(item('Portland, OR',f'Base salary: ${low:,} - ${high:,} annually.'),PROFILE)
        self.assertEqual('blocked',a['geo_status'])
        self.assertEqual('SKIP / NOT ELIGIBLE',a['recommendation'])
        self.assertLessEqual(a['score'],20)
        b=score_job(item('Remote (USA)'),PROFILE)
        self.assertEqual('review',b['geo_status'])
        self.assertEqual('VERIFY ELIGIBILITY',b['recommendation'])
        self.assertLessEqual(b['score'],69)
        c=score_job(item('San Diego, CA'),PROFILE)
        self.assertEqual('local',c['geo_status'])

if __name__=='__main__':
    unittest.main()
