"""Offline guardrail tests for session-only synthetic portfolio data."""
import unittest
from portfolio_demo import DEMO_JOBS, STAGES, search_demo_jobs, set_demo_status, group_demo_applications

class PortfolioDemoTests(unittest.TestCase):
    def test_all_jobs_fictional(self):
        self.assertEqual(len(DEMO_JOBS), 3)
        self.assertTrue(all(j['id'].startswith('DEMO-') for j in DEMO_JOBS))
        self.assertTrue(all('url' not in j and 'email' not in j for j in DEMO_JOBS))
    def test_search_is_local_and_predictable(self):
        self.assertEqual(len(search_demo_jobs()), 3)
        self.assertEqual([j['id'] for j in search_demo_jobs('security')], ['DEMO-003'])
        self.assertEqual(len(search_demo_jobs(arrangement='Remote')), 1)
        self.assertEqual(search_demo_jobs('not a sample title'), [])
    def test_status_changes_are_immutable(self):
        initial = {'DEMO-001':'Interested'}
        updated = set_demo_status(initial, 'DEMO-001', 'Applied')
        self.assertEqual(initial['DEMO-001'], 'Interested')
        self.assertEqual(updated['DEMO-001'], 'Applied')
    def test_rejects_unknown_ids_and_status(self):
        with self.assertRaises(ValueError):
            set_demo_status({}, 'REAL-123', 'Applied')
        with self.assertRaises(ValueError):
            set_demo_status({}, 'DEMO-001', 'Unknown')
    def test_board_only_uses_demo_jobs(self):
        groups=group_demo_applications({'DEMO-001':'Interview'})
        self.assertEqual(set(groups),set(STAGES))
        self.assertEqual(groups['Interview'][0]['id'],'DEMO-001')
        self.assertEqual(sum(len(v) for v in groups.values()),3)

if __name__=='__main__':
    unittest.main()
