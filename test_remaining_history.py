import csv
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from remaining_history import plan, apply

ROOT = Path(__file__).parent
PREVIEW = ROOT / 'application_history_preview.csv'

class RemainingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / 'jobpilot.db'
        self.preview = self.dir / 'application_history_preview.csv'
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, title TEXT, url TEXT, status TEXT)')
            c.execute("INSERT INTO jobs(company,title,url,status) VALUES ('Existing','Role','https://example.com','Applied')")
            c.commit()
        with self.preview.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['export_row','company','title','external_status','added_date','job_url','action'])
            writer.writeheader()
            writer.writerows([
                dict(export_row=2,company='A',title='Help Desk',external_status='Rejected',added_date='Oct 1, 2026',job_url='',action='Manual review — no usable URL'),
                dict(export_row=3,company='A',title='Help Desk',external_status='Rejected',added_date='Oct 1, 2026',job_url='',action='Review tracker duplicates before any import'),
                dict(export_row=4,company='B',title='Analyst',external_status='Applied',added_date='Oct 2, 2026',job_url='https://example.com/role',action='Already tracked — no change')])
    def test_plan_does_not_write(self):
        before = self.db.read_bytes()
        p = plan(self.preview,self.db)
        self.assertEqual((p['unmatched'],len(p['inserts']),p['duplicate_flags']),(2,2,1))
        self.assertEqual(before,self.db.read_bytes())
    def test_wrong_token_does_not_write(self):
        before = self.db.read_bytes()
        with self.assertRaises(ValueError): apply(self.preview,self.db,'0000000000000000')
        self.assertEqual(before,self.db.read_bytes())
    def test_backup_idempotence_and_jobs_untouched(self):
        p=plan(self.preview,self.db)
        backup,n,u=apply(self.preview,self.db,p['token'])
        self.assertTrue(backup.is_file())
        self.assertEqual((n,u),(2,0))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM rabbitresume_unmatched_history').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        p2=plan(self.preview,self.db)
        self.assertEqual((len(p2['inserts']),len(p2['updates'])),(0,0))
        self.assertEqual(apply(self.preview,self.db,p2['token']),(None,0,0))
    def test_change_invalidates_old_token(self):
        p=plan(self.preview,self.db)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE jobs SET status='Rejected' WHERE id=1")
            c.commit()
        with self.assertRaisesRegex(ValueError,'Plan token'): apply(self.preview,self.db,p['token'])

if __name__ == '__main__': unittest.main()
