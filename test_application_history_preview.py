"""Stage 6A offline preview tests; no live databases are modified."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from unittest.mock import patch
import application_history_preview as m

class ApplicationHistoryPreviewTests(unittest.TestCase):
    def test_greenhouse_tracking_query_is_ignored(self):
        a='https://job-boards.greenhouse.io/example/jobs/1234567001?gh_src=abc&utm_source=x'
        b='https://job-boards.greenhouse.io/example/jobs/1234567001'
        self.assertEqual(m.url_identity(a), m.url_identity(b))

    def test_different_ats_ids_never_auto_merge(self):
        old=[{'company':'Example','title':'Support Specialist','url':'https://job-boards.greenhouse.io/a/jobs/1234567001','status':'Applied'}]
        new=[{'_row':2,'Company':'Example','Job Title':'Support Specialist','Status':'Rejected', 'Job URL':'https://job-boards.greenhouse.io/a/jobs/1234567002'}]
        result=m.reconcile(new,old)
        self.assertEqual(result[0]['match_result'],'Possible company/title match')
        self.assertIn('do not auto-merge',result[0]['action'])

    def test_status_conflict_is_not_overwritten(self):
        old=[{'company':'Company','title':'Engineer','url':'https://example.com/jobs/abc','status':'Applied'}]
        new=[{'_row':2,'Company':'Company','Job Title':'Engineer','Status':'Rejected','Job URL':'https://example.com/jobs/abc'}]
        result=m.reconcile(new,old)
        self.assertEqual(result[0]['action'],'Status differs — manual review')
        self.assertEqual(old[0]['status'],'Applied')

    def test_missing_url_manual_review_and_duplicate(self):
        row={'_row':2,'Company':'Company','Job Title':'Support','Status':'Applied','Job URL':''}
        results=m.reconcile([row,dict(row,_row=3)],[])
        self.assertTrue(all('duplicates' in r['action'].lower() for r in results))

    def test_read_only_db_missing_does_not_create(self):
        with TemporaryDirectory() as td:
            p=Path(td)/'missing.db'
            with self.assertRaises(FileNotFoundError):m.read_saved(p)
            self.assertFalse(p.exists())

    def test_read_only_db_preserves_bytes(self):
        with TemporaryDirectory() as td:
            p=Path(td)/'jobpilot.db'
            con=sqlite3.connect(p)
            con.execute('CREATE TABLE jobs(company TEXT,title TEXT,status TEXT,url TEXT)')
            con.execute("INSERT INTO jobs VALUES('A','Support','Applied','https://example.com/jobs/1')")
            con.commit(); con.close()
            before=p.read_bytes()
            self.assertEqual(len(m.read_saved(p)),1)
            self.assertEqual(before,p.read_bytes())

    def test_formula_injection_prevented(self):
        self.assertEqual(m._csv_text('=HYPERLINK("bad")'),"'=HYPERLINK(\"bad\")")

if __name__=='__main__': unittest.main()
