"""Scanner configuration defaults and override precedence."""

import json
import unittest

from scheduled_scan import run_scan
from test_scheduled_scan import (
    ScheduledScanTests, listing, fake_score,
)


class ScannerConfigDefaultsTests(unittest.TestCase):

    def setUp(self):
        fixture = ScheduledScanTests(
            "test_no_writes_from_settings_and_readiness"
        )
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.root = fixture.root
        self.calls = []

    def collector(self, sources, max_age_days, verify_first_published):
        self.calls.append(max_age_days)
        return [listing(10), listing(30)], []

    def set_policy(self, **settings):
        (self.root / "jobpilot_policy.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

    def test_json_defaults_used(self):
        self.set_policy(
            discovery_window_days=30,
            minimum_relevance_score=60,
        )
        result = run_scan(
            self.root,
            collector=self.collector,
            scorer=fake_score,
        )
        self.assertEqual(self.calls, [30])
        self.assertEqual(result["minimum_score"], 60)

    def test_explicit_parameters_override_json(self):
        self.set_policy(
            discovery_window_days=30,
            minimum_relevance_score=60,
        )
        result = run_scan(
            self.root,
            max_age_days=20,
            min_score=50,
            collector=self.collector,
            scorer=fake_score,
        )
        self.assertEqual(self.calls, [20])
        self.assertEqual(result["minimum_score"], 50)

    def test_missing_policy_preserves_defaults(self):
        result = run_scan(
            self.root,
            collector=self.collector,
            scorer=fake_score,
        )
        self.assertEqual(self.calls, [45])
        self.assertEqual(result["minimum_score"], 45)

    def test_invalid_policy_fails_before_collection(self):
        self.set_policy(discovery_window_days=366)
        with self.assertRaises(ValueError):
            run_scan(
                self.root,
                collector=self.collector,
                scorer=fake_score,
            )
        self.assertEqual(self.calls, [])

    def test_invalid_explicit_override_fails_before_collection(self):
        with self.assertRaises(ValueError):
            run_scan(
                self.root,
                max_age_days=0,
                collector=self.collector,
                scorer=fake_score,
            )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
