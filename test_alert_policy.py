"""Offline tests of the conservative Stage 5D first-seen mail eligibility gate."""
import unittest
from datetime import datetime, timedelta, timezone
from alert_policy import email_eligible
from scheduled_scan import select_relevant_first_seen
from discovery_history import job_fingerprint


class AlertPolicyTests(unittest.TestCase):
    def test_status_gate(self):
        published = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        for status in ('local', 'remote_ca', 'relocation'):
            with self.subTest(status=status):
                self.assertTrue(email_eligible({
                    'eligibility': status,
                    'original_published_at': published,
                    'original_publication_verified': True,
                }))
        for status in ('review', 'blocked', '', None, 'unknown'):
            with self.subTest(status=status):
                self.assertFalse(email_eligible({
                    'eligibility': status,
                    'original_published_at': published,
                    'original_publication_verified': True,
                }))

    def test_scanner_keeps_only_qualified_first_seen(self):
        published = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        jobs = [dict(company='Example', title='IT Support', location='San Diego, CA',
                     url=f'https://example.org/jobs/{i}',
                     original_published_at=published,
                     original_publication_verified=True) for i in range(4)]
        statuses = ['review', 'blocked', 'local', 'relocation']
        profile = {}
        def fake_scorer(job, _profile):
            idx = int(job['url'].rsplit('/', 1)[-1])
            return dict(score=80, matched_family='IT Support', geo_status=statuses[idx],
                        geo_reason='test', recommendation='STRONG APPLY')
        new_ids = {job_fingerprint(j) for j in jobs}
        selected = select_relevant_first_seen(((j, profile) for j in jobs), new_ids,
                                              saved_rows=[], scorer=fake_scorer)
        self.assertEqual(
            {row['eligibility'] for row in selected},
            {'review', 'local', 'relocation'}
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {row['eligibility'] for row in selected if email_eligible(row)},
            {'local', 'relocation'}
        )

if __name__ == '__main__':
    unittest.main()
