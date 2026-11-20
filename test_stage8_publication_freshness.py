import unittest
from datetime import datetime, timedelta, timezone

from alert_policy import email_eligible


def posting(days_old, verified=True, eligibility="local"):
    published = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "eligibility": eligibility,
        "original_published_at": published.isoformat(),
        "original_publication_verified": verified,
    }


class PublicationFreshnessTests(unittest.TestCase):

    def test_verified_recent_publication_allowed(self):
        self.assertTrue(email_eligible(posting(3)))

    def test_verified_old_publication_blocked(self):
        self.assertFalse(email_eligible(posting(30)))

    def test_unverified_publication_blocked(self):
        self.assertFalse(email_eligible(posting(3, verified=False)))

    def test_missing_original_date_blocked(self):
        self.assertFalse(email_eligible({
            "eligibility": "local",
            "posted_at": "Last updated yesterday",
        }))

    def test_recent_update_cannot_override_old_publication(self):
        row = posting(40)
        row["posted_at"] = "Last updated today"
        self.assertFalse(email_eligible(row))

    def test_invalid_publication_date_blocked(self):
        row = posting(3)
        row["original_published_at"] = "Unknown"
        self.assertFalse(email_eligible(row))

    def test_future_publication_date_blocked(self):
        self.assertFalse(email_eligible(posting(-3)))

    def test_uncertain_geography_still_blocked(self):
        self.assertFalse(
            email_eligible(posting(3, eligibility="review"))
        )


if __name__ == "__main__":
    unittest.main()
