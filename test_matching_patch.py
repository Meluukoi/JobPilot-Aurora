"""Run offline: python test_matching_patch.py"""
import json, unittest
from pathlib import Path
from scorer import score_job, _experience_requirements, _role_family_for_title

# Synthetic fixture only; does NOT read or overwrite user's personal profile.
PROFILE = {
    'target_role_families': {
        'IT Support': ['it support','help desk','service desk','support specialist'],
        'Cybersecurity': ['security analyst','security operations','soc analyst'],
        'Customer/Client Support': ['customer support','customer service','client services'],
        'Operations': ['operations coordinator','operations associate'],
        'Sales / Business Development': ['sales development representative'],
        'Data/Reporting': ['data analyst'],
    },
    'skills': ['windows','excel','customer service','troubleshooting'],
    'exclude_title_terms': ['senior','director','principal','staff'],
    'soft_penalty_title_terms': ['lead','manager'],
    'degree_status': 'no_bachelors',
    'candidate_profile': {
        'status': 'approved_by_user_test_fixture',
        'professional_experience': [
            {'employer':'Example Retail Store','title':'Front-End Cashier',
             'dates':'2022-2024', 'activities':['Customer service desk and phone refunds']}
        ],
        'preferences': {'relocation':{'open':True,'minimum_annual_salary_usd':100000},
                        'active_us_security_clearance':False},
        'repository_supported_capabilities': [],
        'conversation_reported_exposure': [],
    },
}


def job(title, description='', location='San Diego, CA'):
    return {'title':title, 'description':description, 'location':location}

class MatchingTests(unittest.TestCase):
    def test_patient_support_titles_are_not_it(self):
        for title in ('Patient Access Specialist', 'Patient Support Specialist', 'Customer Support Specialist', 'Client Services Associate', 'Advanced Care Specialist'):
            with self.subTest(title=title):
                result=score_job(job(title, 'Customer service and handling inbound calls'),PROFILE)
                self.assertEqual(result['matched_family'],'Customer/Client Support',result)
                self.assertGreaterEqual(result['score'],50,result)

    def test_local_office_jobs_discoverable(self):
        for title in ('Business Office Representative Clerk','Administrative Assistant', 'Front Desk Receptionist'):
            with self.subTest(title=title):
                result=score_job(job(title,'Good communication skills, high school diploma'),PROFILE)
                self.assertIn(result['matched_family'],('Operations','Customer/Client Support'))
                self.assertGreaterEqual(result['score'],45,result)

    def test_it_helpdesk_found(self):
        result=score_job(job('IT Service Desk Technician I','Windows technical troubleshooting'),PROFILE)
        self.assertEqual(result['matched_family'],'IT Support')
        self.assertGreaterEqual(result['score'],55)

    def test_1_year_general_customer_service(self):
        result=score_job(job('Patient Access Specialist','Minimum qualifications: At least 1 year of customer service experience required.'),PROFILE)
        self.assertGreaterEqual(result['score'],50,result)
        self.assertTrue(any('1-year general customer-service' in r for r in result['reasons']),result)
        self.assertFalse(any('Requires 1+' in g for g in result['gaps']),result)

    def test_specialist_years_not_converted(self):
        result=score_job(job('Patient Access Specialist','Required qualifications: 2 years of patient access and insurance authorization experience required.'),PROFILE)
        self.assertTrue(any('Requires 2+' in g for g in result['gaps']),result)
        self.assertNotEqual(result['recommendation'],'STRONG APPLY')

    def test_security_high_pay_experience(self):
        result=score_job(job('Security Operations Analyst', 'Required qualifications: 5 years security operations experience required. Annual base salary: $147,000 to $170,000.', 'Seattle, WA'), PROFILE)
        self.assertIn(result['geo_status'],('relocation','review'),result)
        self.assertNotIn(result['recommendation'], ('STRONG APPLY', 'APPLY / REVIEW'),result)

    def test_preferred_not_required(self):
        result=score_job(job('Client Services Associate', 'Preferred qualifications: 3 years of customer service experience preferred.'), PROFILE)
        self.assertFalse(any('Requires 3+' in g for g in result['gaps']),result)
        self.assertNotEqual(result['recommendation'],'SKIP / NOT ELIGIBLE')

    def test_ca_exclusion_never_top(self):
        result=score_job(job('Customer Service Representative','Not accepting applications from California residents.','Remote - United States'),PROFILE)
        self.assertEqual(result['geo_status'],'blocked')
        self.assertEqual(result['recommendation'],'SKIP / NOT ELIGIBLE')
        self.assertLessEqual(result['score'],20)

    def test_careers_require_license_not_generic(self):
        result=score_job(job('Registered Nurse', 'Requires active registered nurse license, clinical nursing experience.'),PROFILE)
        self.assertLess(result['score'],40,result)
        self.assertIsNone(result['matched_family'])

    def test_no_inferred_engineer_skill(self):
        result=score_job(job('Senior Software Engineer', 'Required qualifications: Bachelor degree. 7 years of professional software development experience.'),PROFILE)
        self.assertLess(result['score'],30,result)
        self.assertNotEqual(result['recommendation'],'STRONG APPLY')

    def test_year_parsing(self):
        self.assertTrue(_experience_requirements('Minimum qualifications: 1 year of customer service experience required'))
        self.assertTrue(_experience_requirements("Required: 1-2 years' experience in patient access"))
        self.assertTrue(_experience_requirements('Must have 3 years of healthcare experience'))

if __name__=='__main__': unittest.main(verbosity=2)
