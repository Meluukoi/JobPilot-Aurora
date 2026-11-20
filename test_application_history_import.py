import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest
from application_history_import import make_plan, apply_plan

ROOT=Path(__file__).resolve().parent
TRACKER=ROOT / 'job-tracker-export (1).xlsx'
SAVED_DB=ROOT / 'jobpilot.db'
PRE_IMPORT_DB=ROOT / 'jobpilot_pre_stage6b_20261030T171145Z.db'

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'jobpilot.db'
        if not TRACKER.is_file() or not SAVED_DB.is_file():
            self.skipTest('Requires tracker spreadsheet and existing JobPilot database in testing folder')
        source = (
            PRE_IMPORT_DB
            if self._testMethodName in (
                'test_plan_read_only',
                'test_apply_backups_and_idempotent',
            )
            else SAVED_DB
        )
        if not source.is_file():
            self.skipTest('Required historical fixture is missing')
        shutil.copy2(source, self.db)
    def test_plan_read_only(self):
        before=self.db.read_bytes()
        p=make_plan(TRACKER,self.db)
        self.assertEqual((p['tracker_rows'],len(p['inserts']),len(p['updates'])),(145,49,1))
        self.assertEqual(before,self.db.read_bytes())
    def test_wrong_confirmation_never_writes(self):
        before=self.db.read_bytes()
        with self.assertRaises(ValueError): apply_plan(TRACKER,self.db,'0000000000000000')
        self.assertEqual(before,self.db.read_bytes())
    def test_apply_backups_and_idempotent(self):
        p=make_plan(TRACKER,self.db)
        backup,n,u=apply_plan(TRACKER,self.db,p['token'])
        self.assertTrue(backup.exists());self.assertEqual((n,u),(49,1))
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],51)
            self.assertEqual(con.execute("SELECT status FROM jobs WHERE source_id='8033739003'").fetchone()[0],'Rejected')
        p2=make_plan(TRACKER,self.db)
        self.assertEqual((len(p2['inserts']),len(p2['updates'])),(0,0))
        self.assertEqual(apply_plan(TRACKER,self.db,p2['token']),(None,0,0))
    def test_changed_db_invalidates_token(self):
        p=make_plan(TRACKER,self.db)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE jobs SET status=CASE WHEN status = 'Rejected' THEN 'Applied' ELSE 'Rejected' END WHERE id=1")
            con.commit()
        with self.assertRaises(ValueError): apply_plan(TRACKER,self.db,p['token'])
if __name__=='__main__':unittest.main()
