"""Stage 3 / Stage 5 additive full-review reports: offline regression tests."""
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from opportunity_review import (REVIEW_FLOOR, build_review_reports, classify,
                                exploratory_title_track, save_review_reports)
from discovery_history import job_fingerprint


def job(i, title="IT Support Technician", location="San Diego, CA"):
    return {"source": "Greenhouse", "source_id": str(i), "company": "ExampleCo",
            "title": title, "location": location,
            "url": f"https://job-boards.greenhouse.io/exampleco/jobs/{i}",
            "description": "Support work", "posted_at": "Unknown"}


def score(item, profile):
    name = item["title"]
    if "Accounting" in name:
        return dict(score=90, matched_family=None, geo_status="local", recommendation="STRONG APPLY")
    if "Clearance" in name:
        return dict(score=85, matched_family="IT Support", geo_status="local", hard_flags=["Active clearance required"], recommendation="SKIP / NOT ELIGIBLE")
    if "Senior" in name:
        return dict(score=44, matched_family="IT Support", geo_status="local", hard_flags=["Senior-level title"], gaps=["More experience requested"], recommendation="SKIP / LOW PRIORITY")
    if "Blocked" in name:
        return dict(score=20, matched_family="IT Support", geo_status="blocked", geo_reason="Outside U.S.", recommendation="SKIP / NOT ELIGIBLE")
    if "Stretch" in name:
        return dict(score=34, matched_family="IT Support", geo_status="local", gaps=["Requires experience to verify"], recommendation="SKIP / LOW PRIORITY")
    if "Remote" in name:
        return dict(score=63, matched_family="IT Support", geo_status="review", geo_reason="CA eligibility unverified", recommendation="VERIFY ELIGIBILITY")
    if "Low" in name:
        return dict(score=22, matched_family="IT Support", geo_status="local", recommendation="SKIP / LOW PRIORITY")
    return dict(score=82, matched_family="IT Support", geo_status="local", recommendation="STRONG APPLY")


class OpportunityReviewTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [job(1), job(2, "Stretch Support"), job(3, "Senior Support"),
                     job(4, "Remote Support"), job(5, "Blocked Support"),
                     job(6, "Accounting Manager"), job(7, "Clearance Support"),
                     job(8, "Low Support"), job(9, "Already Applied Support")]
        self.saved = [dict(self.jobs[-1], status="Applied")]

    def test_full_audit_and_review_includes_old_and_low_scored_stretch(self):
        fresh = {job_fingerprint(self.jobs[1])}
        review, audit, count = build_review_reports(self.jobs, {}, self.saved, fresh, scorer=score)
        self.assertEqual(len(audit), 9)
        self.assertEqual(len(review), 4)
        self.assertEqual(count["collected"], 9)
        self.assertEqual(count["first_seen_review"], 1)
        self.assertEqual(count["stretch_review"], 2)
        self.assertEqual(count["requires_verification"], 2)
        self.assertIn("Stretch Support", [j["title"] for j in review])
        self.assertIn("IT Support Technician", [j["title"] for j in review])
        self.assertTrue(next(j for j in review if j["title"] == "Stretch Support")["first_seen_in_this_scan"] == "Yes")
        reasons = {r["title"]: r["decision_explanation"] for r in audit}
        self.assertIn("Already applied", reasons["Already Applied Support"])
        self.assertIn("Geographic", reasons["Blocked Support"])
        self.assertIn("outside configured", reasons["Accounting Manager"])
        self.assertIn("clearance", reasons["Clearance Support"].lower())
        self.assertEqual(REVIEW_FLOOR, 30)

    def test_billing_and_technical_support_titles_get_separate_exploratory_review(self):
        titles = [
            ("Billing Associate - US Remote", "Prepare invoices and customer account billing inquiries", "Billing administration"),
            ("Level 2 Support Representative", "Resolve SaaS customer support tickets and troubleshoot software", "Technical product support"),
            ("Implementation Specialist", "Onboard clients to our software platform", "Client implementation / onboarding"),
            ("Technical Operations Support Analyst", "Investigate application issues and software troubleshooting", "Technical product support"),
            ("Field Support Specialist", "On-site computer and laptop hardware troubleshooting for IT support", "IT field services"),
        ]
        jobs = []
        for i, (title, description, track) in enumerate(titles, start=101):
            j = job(i, title, location="Remote")
            j["description"] = description
            self.assertEqual(exploratory_title_track(j), track)
            jobs.append(j)
        def unrecognized_scorer(j, _profile):
            return dict(score=0, matched_family=None, geo_status="review", geo_reason="Verify US remote eligibility",
                        recommendation="SKIP / LOW PRIORITY", gaps=["3+ years of specialist experience unclear"], hard_flags=[])
        review, audit, count = build_review_reports(jobs, {}, [], set(), scorer=unrecognized_scorer)
        self.assertEqual(len(review), 5)
        self.assertEqual(len(audit), 5)
        self.assertEqual(count["exploratory_review"], 5)
        self.assertEqual(count["experience_gap_reviews"], 5)
        self.assertEqual(count["stretch_review"], 5)
        self.assertEqual(count["stretch_labeled"], 0)  # Exploratory gap signal != stretch priority label
        self.assertTrue(all(j["priority"] == "Exploratory review" for j in review))
        self.assertTrue(all(j["score"] == 0 for j in review))
        self.assertTrue(all("not calibrated" in j["review_rationale"] for j in review))
        self.assertTrue(all(j["role_family"].startswith("Exploratory:") for j in review))

    def test_exploratory_titles_do_not_override_real_blocks_or_promote_senior(self):
        billing = job(201, "Billing Associate", "Remote")
        billing["description"] = "Billing, invoicing, accounts receivable"
        applied = dict(billing, source_id="202", url="https://example.com/202")
        irrelevant = job(203, "Field Support Specialist", "Remote")
        irrelevant["description"] = "Mailroom, printing, workplace guest services"
        senior = job(204, "Senior Implementation Specialist", "Remote")
        senior["description"] = "Customer implementation and onboarding"
        no_technical = job(205, "Level 2 Support Representative", "Remote")
        no_technical["description"] = "Shipping boxes and handling mailroom packages"
        self.assertIsNone(exploratory_title_track(irrelevant))
        self.assertIsNone(exploratory_title_track(senior))
        self.assertIsNone(exploratory_title_track(no_technical))
        a = dict(score=0, matched_family="Other", geo_status="review", gaps=[], hard_flags=[])
        self.assertEqual(classify(billing, a, {"status":"Applied"})[1], "excluded")
        self.assertEqual(classify(billing, dict(a, geo_status="blocked"), None)[1], "excluded")
        self.assertEqual(classify(billing, dict(a, hard_flags=["Active clearance required"]), None)[1], "excluded")
        self.assertEqual(classify(senior, a, None)[1], "excluded")
        self.assertEqual(classify(irrelevant, a, None)[1], "excluded")

    def test_player_support_second_chance_is_conservative(self):
        sony = job(6234948004, "Community/Player Support Specialist", "San Diego, CA")
        sony["company"] = "Sony Interactive Entertainment"
        sony["description"] = "Help players with customer support inquiries and troubleshooting"
        unrecognized = dict(score=22, matched_family=None, geo_status="local", geo_reason="Local San Diego",
                            gaps=["Title outside selected target roles"], hard_flags=[], recommendation="SKIP / LOW PRIORITY")
        self.assertEqual(exploratory_title_track(sony), "Player / community support")
        priority, decision, explanation = classify(sony, unrecognized, None)
        self.assertEqual((priority, decision), ("Exploratory review", "review"))
        self.assertIn("not calibrated", explanation)
        # Title alone cannot rescue irrelevant descriptions.
        irrelevant = dict(sony, description="Build game rendering engines and character animations")
        self.assertIsNone(exploratory_title_track(irrelevant))
        # Senior positions, ineligible locations, clearances and already applied stay excluded.
        senior = dict(sony, title="Senior Community/Player Support Specialist")
        self.assertIsNone(exploratory_title_track(senior))
        self.assertEqual(classify(sony, dict(unrecognized, geo_status="blocked"), None)[1], "excluded")
        self.assertEqual(classify(sony, dict(unrecognized, hard_flags=["Active clearance required"]), None)[1], "excluded")
        self.assertEqual(classify(sony, unrecognized, {"status":"Rejected"})[1], "excluded")

    def test_simultaneous_experience_gap_and_remote_eligibility_are_visible(self):
        j = job(301, "Technical Support Specialist", "US Remote")
        result = dict(score=63, matched_family="IT Support", geo_status="review",
                      geo_reason="California work eligibility unknown",
                      recommendation="VERIFY ELIGIBILITY", hard_flags=[],
                      gaps=["Requires 3+ years of relevant experience; not verified by profile",
                            "California eligibility unknown"])
        review, audit, count = build_review_reports([j], {}, [], set(), scorer=lambda *args: result)
        self.assertEqual(review[0]["priority"], "Stretch + location verification")
        self.assertIn("both independently", review[0]["review_rationale"])
        self.assertEqual(count["experience_gap_reviews"], 1)
        self.assertEqual(count["stretch_review"], 1)
        self.assertEqual(count["requires_verification"], 1)
        self.assertEqual(audit[0]["screening_decision"], "review")

    def test_specialist_roles_are_manual_verification_not_priority_by_score(self):
        jobs = [job(411, "Security Operations Analyst", "Boston, MA"),
                job(412, "Technical Support Engineer I", "Boston, MA"),
                job(413, "Customer Support Specialist", "San Diego, CA")]
        def score(_item, _profile):
            family = "Cybersecurity" if "Security" in _item["title"] else (
                "IT Support" if "Engineer" in _item["title"] else "Customer/Client Support")
            return dict(score=74, matched_family=family, geo_status="relocation",
                        gaps=[], hard_flags=[], recommendation="APPLY / REVIEW")
        review, audit, count = build_review_reports(jobs, {}, [], set(), scorer=score)
        flagged = [r for r in review if r["priority"] == "Specialist skills verification"]
        self.assertEqual(len(flagged), 2)
        self.assertEqual(count["specialist_review"], 2)
        self.assertEqual(count["review"], 3)
        self.assertTrue(all("verify" in r["review_rationale"] for r in flagged))
        self.assertIn("Customer Support Specialist", [r["title"] for r in review
                                                  if r["priority"] != "Specialist skills verification"])

    def test_workplace_ambassador_gets_manual_review_when_customer_facing(self):
        x = job(421, "Workplace Experience Ambassador", "San Diego, CA")
        x["description"] = "Reception and visitor hospitality with customer service"
        self.assertEqual(exploratory_title_track(x), "Workplace customer services")
        x["description"] = "Warehouse equipment maintenance and forklifts"
        self.assertIsNone(exploratory_title_track(x))

    def test_single_writing_to_reports_preserves_other_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for n in ("jobpilot.db", "discovery_history.db", "profile.json", "email_settings.json"):
                (root / n).write_bytes(b"original bytes")
            before = {n: (root / n).read_bytes() for n in ("jobpilot.db", "discovery_history.db", "profile.json", "email_settings.json")}
            review, audit, counts = build_review_reports(self.jobs, {}, self.saved, set(), scorer=score)
            report = save_review_reports(root, review, audit, counts, scan_id=17)
            self.assertEqual(report["scan_id"], 17)
            self.assertEqual(before, {n: (root / n).read_bytes() for n in before})
            with (root / "monitoring_reports" / "latest_screening_audit.csv").open(encoding="utf-8-sig", newline="") as f:
                all_rows = list(csv.DictReader(f))
            self.assertEqual(len(all_rows), 9)
            with (root / "monitoring_reports" / "latest_review_opportunities.csv").open(encoding="utf-8-sig", newline="") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 4)
            summary = json.loads((root / "monitoring_reports" / "latest_review_summary.json").read_text())
            self.assertEqual(summary["review"], 4)

    def test_untrusted_csv_formula_cannot_execute_as_formula(self):
        with TemporaryDirectory() as tmp:
            row = dict(company="=1+1", title="@X", location="\t=HYPERLINK", score=40, url="https://example.org")
            save_review_reports(tmp, [row], [dict(row, screening_decision="review", decision_explanation="ok")],
                                {"collected": 1, "review": 1}, scan_id=1)
            with open(Path(tmp) / "monitoring_reports" / "latest_review_opportunities.csv", encoding="utf-8-sig") as f:
                parsed = next(csv.DictReader(f))
            self.assertTrue(parsed["company"].startswith("'="))
            self.assertTrue(parsed["title"].startswith("'@"))
            self.assertTrue(parsed["location"].startswith("'\t"))


if __name__ == "__main__":
    unittest.main()
