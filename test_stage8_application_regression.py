import json
import unittest
from pathlib import Path

from application_tracking import SavedJobIndex, already_applied


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "stage8_regression" / "application_history_cases_v1.json"


class Stage8ApplicationRegressionTests(unittest.TestCase):

    def test_application_history_cases(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(data["dataset_version"], 1)
        self.assertEqual(len(data["cases"]), 6)

        for case in data["cases"]:
            with self.subTest(case=case["case_id"]):
                candidate = case["candidate"]
                history = case["saved_history"]

                index = SavedJobIndex(history)
                exact, possible = index.lookup(candidate)

                suppressed = bool(
                    exact and already_applied(exact.get("status"))
                )

                actual = "suppress" if suppressed else "preserve"

                self.assertEqual(
                    actual,
                    case["expected_application_gate"]
                )

                if case["case_id"] == "different_requisition":
                    self.assertIsNone(exact)
                    self.assertEqual(len(possible), 1)

                if case["case_id"] == "tracking_url_changed":
                    self.assertIsNotNone(exact)


if __name__ == "__main__":
    unittest.main()
