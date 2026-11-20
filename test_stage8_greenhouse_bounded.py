import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from stage8_greenhouse_verification import verify_candidates


class BoundedGreenhouseTests(unittest.TestCase):

    def setUp(self):
        self.fresh = (
            datetime.now(timezone.utc) - timedelta(days=3)
        ).isoformat()

        self.sources = [{
            "ats": "Greenhouse",
            "company": "Example",
            "token_or_site": "example",
        }]

    def job(self, job_id, source="Greenhouse"):
        return {
            "source": source,
            "source_id": str(job_id),
            "company": "Example",
            "title": "IT Support Specialist",
            "url": f"https://example.org/jobs/{job_id}",
            "original_publication_verified": False,
        }

    def response(self, published=None, status=200):
        result = Mock()
        result.ok = status == 200
        result.status_code = status
        result.json.return_value = {
            "first_published": published
        }
        return result

    def test_strict_request_budget(self):
        jobs = [self.job(i) for i in range(10)]

        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=self.response(self.fresh),
        ) as get:
            result = verify_candidates(
                jobs, self.sources, max_requests=2
            )

        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            sum(bool(j["original_publication_verified"])
                for j in result),
            2,
        )

    def test_verified_date_propagates(self):
        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=self.response(self.fresh),
        ):
            result = verify_candidates(
                [self.job(123)], self.sources, max_requests=1
            )

        self.assertEqual(
            result[0]["original_published_at"], self.fresh
        )
        self.assertIs(
            result[0]["original_publication_verified"], True
        )

    def test_failed_request_preserves_candidate(self):
        with patch(
            "stage8_greenhouse_verification.requests.get",
            return_value=self.response(status=503),
        ):
            result = verify_candidates(
                [self.job(123)], self.sources, max_requests=1
            )

        self.assertEqual(len(result), 1)
        self.assertFalse(
            result[0]["original_publication_verified"]
        )

    def test_non_greenhouse_candidate_not_requested(self):
        with patch(
            "stage8_greenhouse_verification.requests.get"
        ) as get:
            result = verify_candidates(
                [self.job(123, source="Lever")],
                self.sources,
                max_requests=2,
            )

        get.assert_not_called()
        self.assertEqual(len(result), 1)

    def test_invalid_job_id_not_requested(self):
        with patch(
            "stage8_greenhouse_verification.requests.get"
        ) as get:
            result = verify_candidates(
                [self.job("../invalid")],
                self.sources,
                max_requests=2,
            )

        get.assert_not_called()
        self.assertFalse(
            result[0]["original_publication_verified"]
        )


if __name__ == "__main__":
    unittest.main()
