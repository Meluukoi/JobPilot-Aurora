import unittest

from scorer import score_job
from opportunity_review import classify


PROFILE = {
    "home_state": "CA",
    "target_role_families": {
        "IT Support": ["technical support specialist"],
    },
    "skills": ["technical support", "troubleshooting"],
    "candidate_profile": {
        "preferences": {
            "relocation": {
                "minimum_annual_salary_usd": 75000
            }
        }
    },
}


def make_job(description):
    return {
        "source": "Greenhouse",
        "source_id": "travel-test-1",
        "company": "Test Employer",
        "title": "Technical Support Specialist",
        "location": "San Diego, CA",
        "description": description,
        "url": "https://boards.greenhouse.io/test/jobs/travel-test-1",
    }


class InternationalTravelTests(unittest.TestCase):

    def evaluate(self, description):
        job = make_job(description)
        result = score_job(job, PROFILE)
        priority, decision, reason = classify(job, result, None)
        return result, decision

    def test_explicit_international_travel_required(self):
        result, decision = self.evaluate(
            "International travel is required for this position."
        )
        self.assertEqual(decision, "excluded")
        self.assertTrue(
            any("international travel" in str(flag).lower()
                for flag in result["hard_flags"])
        )

    def test_must_be_willing_to_travel_internationally(self):
        result, decision = self.evaluate(
            "Candidates must be willing to travel internationally."
        )
        self.assertEqual(decision, "excluded")

    def test_domestic_travel_not_blocked(self):
        result, decision = self.evaluate(
            "Occasional domestic travel within the United States is required."
        )
        self.assertFalse(
            any("international travel" in str(flag).lower()
                for flag in result["hard_flags"])
        )

    def test_preferred_international_travel_not_blocked(self):
        result, decision = self.evaluate(
            "International travel experience is preferred but not required."
        )
        self.assertFalse(
            any("international travel" in str(flag).lower()
                for flag in result["hard_flags"])
        )

    def test_international_customers_not_blocked(self):
        result, decision = self.evaluate(
            "Support international customers using remote ticketing tools."
        )
        self.assertFalse(
            any("international travel" in str(flag).lower()
                for flag in result["hard_flags"])
        )


    def test_international_travel_edge_cases(self):
        from scorer import requires_international_travel

        cases = [
            ("International travel is mandatory.", True),
            ("Required international travel up to 25%.", True),
            ("Must travel overseas for client meetings.", True),
            ("Candidates must travel outside the United States.", True),
            ("International travel is not required.", False),
            ("International travel is optional.", False),
            ("International travel experience is preferred.", False),
            ("May occasionally travel internationally.", False),
            ("Domestic travel of up to 30% is required.", False),
            ("Experience with international customers is required.", False),
            (
                "International travel experience is preferred. "
                "International travel is required for this role.",
                True,
            ),
            (
                "International travel is not required. "
                "Domestic travel is required.",
                False,
            ),
        ]

        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(
                    requires_international_travel(description),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
