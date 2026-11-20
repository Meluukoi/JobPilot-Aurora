import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from application_tracking import HistoricalWarningIndex, SavedJobIndex
from scheduled_scan import load_historical_warnings_read_only, select_relevant_first_seen
from opportunity_review import build_review_reports
from discovery_history import job_fingerprint

class HistoricalWarningsTests(unittest.TestCase):
    def setUp(self):
        self.history = [{'company': 'Sample Tech, Inc.', 'title': 'IT Support Specialist', 'status': 'Rejected'}]
        self.job = {'company': 'Sample Tech Inc', 'title': 'IT Support Specialist', 'url': 'https://job-boards.greenhouse.io/sample/jobs/123', 'location': 'San Diego, CA'}
        self.result = dict(score=76, matched_family='IT Support', geo_status='local', hard_flags=[], recommendation='APPLY')

    def test_warn_not_suppress(self):
        idx=HistoricalWarningIndex(self.history)
        self.assertIn('Rejected',idx.warning(self.job))
        self.assertEqual(SavedJobIndex([]).lookup(self.job)[0],None)
        self.assertFalse(idx.warning(dict(self.job,title='Systems Administrator')))

    def test_alert_selection_unchanged(self):
        fp=job_fingerprint(self.job)
        kw=dict(jobs=[(self.job,{})],new_ids={fp}, saved_rows=[],min_score=45,scorer=lambda j,p:self.result)
        old=select_relevant_first_seen(**kw)
        new=select_relevant_first_seen(**kw,historical_rows=self.history)
        self.assertEqual(len(old),len(new))
        self.assertTrue(new[0]['historical_application_warning'])
        self.assertFalse(old[0]['historical_application_warning'])

    def test_review_classification_unchanged(self):
        kw=dict(jobs=[self.job],matching_profile={},saved_rows=[],fresh_ids=set(),scorer=lambda j,p:self.result)
        old=build_review_reports(**kw)
        new=build_review_reports(**kw,historical_rows=self.history)
        self.assertEqual(old[2],new[2])
        self.assertEqual(old[1][0]['screening_decision'],new[1][0]['screening_decision'])
        self.assertIn('Rejected',new[1][0]['historical_application_warning'])

    def test_ledger_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'jobpilot.db'
            with closing(sqlite3.connect(db)) as c:
                with c:
                    c.execute('CREATE TABLE rabbitresume_unmatched_history(company TEXT, title TEXT, status TEXT, reason TEXT)')
                    c.execute('INSERT INTO rabbitresume_unmatched_history VALUES (?,?,?,?)',('Sample Tech Inc','IT Support Specialist','Rejected','no url'))
            before=db.read_bytes()
            rows=load_historical_warnings_read_only(d)
            self.assertEqual(rows[0]['status'],'Rejected')
            self.assertEqual(db.read_bytes(),before)

if __name__=='__main__':
    unittest.main()
