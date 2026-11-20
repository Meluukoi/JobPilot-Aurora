import unittest

from stage8_workday_multi_employer_adapter import (
    EMPLOYERS,
    collect_preview,
)


DESCRIPTION = (
    "Responsibilities include responding to technical support tickets, "
    "investigating connectivity issues, documenting incidents, "
    "and escalating problems. Qualifications include customer service "
    "experience, troubleshooting skills, and technical support knowledge."
)


class FakeClient:
    def __init__(self, count):
        self.count = count
        self.requests = []

    def get_json(self, url, payload=None):
        self.requests.append((url, payload))

        if payload is not None:
            offset = payload["offset"]
            limit = payload["limit"]

            rows = []
            for i in range(offset, min(offset + limit, self.count)):
                rows.append({
                    "externalPath": f"/job/USA---Remote/IT-Support_R{30000+i}-1",
                    "title": "IT Support Specialist",
                    "locationsText": "USA - Remote",
                })

            return {
                "total": self.count,
                "jobPostings": rows,
            }

        req = url.rsplit("_", 1)[-1].split("-")[0]

        return {
            "jobPostingInfo": {
                "jobReqId": req,
                "title": "IT Support Specialist",
                "location": "USA - Remote",
                "additionalLocations": [],
                "jobDescription": DESCRIPTION,
            }
        }


class WorkdayCollectorContractTests(unittest.TestCase):

    def test_partial_index_never_claims_completion(self):
        client = FakeClient(65)

        result = collect_preview(
            EMPLOYERS["crowdstrike"],
            client,
            max_pages=2,
            max_details=2,
        )

        self.assertFalse(result["index_complete"])
        self.assertEqual(len(result["index"]), 40)
        self.assertEqual(len(result["ready_records"]), 2)

    def test_index_preserves_all_requisitions_in_sample(self):
        client = FakeClient(40)

        result = collect_preview(
            EMPLOYERS["crowdstrike"],
            client,
            max_pages=2,
            max_details=0,
        )

        self.assertEqual(len(result["index"]), 40)
        self.assertEqual(len(result["ready_records"]), 0)
        self.assertEqual(
            len({r["identity"] for r in result["index"]}),
            40,
        )

    def test_only_enriched_records_are_score_ready(self):
        client = FakeClient(3)

        result = collect_preview(
            EMPLOYERS["crowdstrike"],
            client,
            max_pages=1,
            max_details=2,
        )

        self.assertEqual(len(result["ready_records"]), 2)
        self.assertEqual(
            sum(r["score_ready"] for r in result["index"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
