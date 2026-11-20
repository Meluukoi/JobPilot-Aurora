import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from validate_candidate_sources import CANDIDATES, existing_sources, summarize, main


class SourceValidationTests(unittest.TestCase):
    def test_sources_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'sources.csv'
            f.write_text('enabled,ats,company,token_or_site\nTrue,Greenhouse,Tines,tines\n', encoding='utf-8')
            before = f.read_bytes()
            self.assertIn(('greenhouse', 'tines'), existing_sources(f))
            self.assertEqual(before, f.read_bytes())

    def test_candidate_identities(self):
        self.assertEqual([(s['ats'], s['token_or_site']) for s in CANDIDATES],
                         [('Lever', 'lyrahealth'), ('Greenhouse', 'upshop')])

    def test_shortlist_is_not_eligibility(self):
        jobs = [dict(source='Lever', source_id='1', company='Test', title='Client Support Specialist',url='https://example.org/1',location='Remote'),
                dict(source='Lever', source_id='2', company='Test', title='Director of Sales',url='https://example.org/2',location='Remote')]
        self.assertEqual(len(summarize(jobs)), 1)

    def test_missing_identity_rejected(self):
        with self.assertRaises(ValueError):
            summarize([dict(company='Test', title='Help Desk', source_id='1',url='')])

    def test_default_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'sources.csv'
            f.write_text('enabled,ats,company,token_or_site\nTrue,Greenhouse,Tines,tines\n',encoding='utf-8')
            with patch('validate_candidate_sources.fetch_source', side_effect=AssertionError('Network unexpectedly called')):
                self.assertEqual(main(['--sources', str(f)]), 0)


if __name__=='__main__':
    unittest.main()
