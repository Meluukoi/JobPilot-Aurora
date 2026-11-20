"""Tests for centralized, validated JobPilot configuration."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jobpilot_config import (
    DEFAULTS,
    load_policy,
    validate_policy,
)


class JobPilotConfigTests(unittest.TestCase):

    def test_missing_file_uses_existing_defaults(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(load_policy(directory), DEFAULTS)

    def test_explicit_defaults(self):
        self.assertEqual(validate_policy(DEFAULTS), DEFAULTS)

    def test_editable_preference(self):
        policy = validate_policy({
            "minimum_relevance_score": 55
        })
        self.assertEqual(
            policy["minimum_relevance_score"], 55
        )

    def test_freshness_cannot_exceed_fourteen_days(self):
        with self.assertRaises(ValueError):
            validate_policy({
                "publication_freshness_hours": 337
            })

    def test_twenty_four_hour_freshness(self):
        policy = validate_policy({
            "publication_freshness_hours": 24
        })
        self.assertEqual(
            policy["publication_freshness_hours"], 24
        )

    def test_default_remains_fourteen_days(self):
        self.assertEqual(
            DEFAULTS["publication_freshness_hours"], 336
        )

    def test_request_budget_is_bounded(self):
        with self.assertRaises(ValueError):
            validate_policy({
                "greenhouse_verification_requests": 11
            })

    def test_unknown_setting_rejected(self):
        with self.assertRaises(ValueError):
            validate_policy({"unexpected_setting": 1})

    def test_boolean_not_accepted_as_integer(self):
        with self.assertRaises(ValueError):
            validate_policy({
                "minimum_relevance_score": True
            })

    def test_file_loading_is_read_only(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobpilot_policy.json"
            path.write_text(
                json.dumps({
                    "minimum_relevance_score": 60
                }),
                encoding="utf-8",
            )
            before = path.read_bytes()
            policy = load_policy(directory)

            self.assertEqual(
                policy["minimum_relevance_score"], 60
            )
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
