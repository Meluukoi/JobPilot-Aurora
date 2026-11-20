import unittest

from discovery_history import job_fingerprint
from application_tracking import SavedJobIndex


BASE = "https://crowdstrike.wd5.myworkdayjobs.com"


def workday_job(req, title, suffix):
    return {
        "source": "Workday",
        "source_id": req,
        "company": "CrowdStrike",
        "title": title,
        "location": "United States",
        "url": (
            f"{BASE}/crowdstrikecareers/job/USA---Remote/"
            f"{title.replace(' ', '-')}_{req}{suffix}"
        )
    }


class WorkdayIntegrationTests(unittest.TestCase):

    def test_same_requisition_has_stable_fingerprint(self):
        a = workday_job("R30431", "IT Support", "-1")
        b = workday_job("R30431", "Technical Support", "-2")

        self.assertEqual(job_fingerprint(a), job_fingerprint(b))

    def test_different_requisitions_remain_separate(self):
        a = workday_job("R30431", "IT Support", "-1")
        b = workday_job("R30432", "IT Support", "-1")

        self.assertNotEqual(job_fingerprint(a), job_fingerprint(b))

    def test_saved_application_matches_changed_workday_url(self):
        saved = workday_job("R30431", "IT Support", "-1")
        saved["status"] = "Applied"

        current = workday_job("R30431", "Technical Support", "-2")

        exact, possible = SavedJobIndex([saved]).lookup(current)

        self.assertIsNotNone(exact)
        self.assertEqual(exact["status"], "Applied")
        self.assertFalse(possible)


if __name__ == "__main__":
    unittest.main()
