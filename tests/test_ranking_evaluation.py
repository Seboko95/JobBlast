import unittest

from core.evidence import build_evidence_ledger, validate_generated_claims
from core.intelligence import eligibility, match_snapshot, title_relationship
from services.ranking_evaluation import evaluate_cases


class RankingEvaluationTests(unittest.TestCase):
    def test_hard_eligibility_precedes_strong_match(self):
        candidate = {"target_roles": ["Medical Representative"], "excluded_keywords": ["internship"]}
        snapshot = match_snapshot(
            {"title": "Medical Representative Internship", "description": "Pharmaceutical sales"},
            candidate,
        )
        self.assertFalse(snapshot["eligibility"]["eligible"])
        self.assertFalse(snapshot["strong_match"])
        self.assertEqual(snapshot["overall_score"], 0)

    def test_unknown_mandatory_qualification_is_not_false_not_met(self):
        result = eligibility(
            {"title": "Medical Representative", "description": "Bachelor degree required"},
            {"target_roles": ["Medical Representative"], "resume_profile": {"education": []}},
        )
        check = next(item for item in result["checks"] if item["type"] == "mandatory_qualification")
        self.assertEqual(check["status"], "UNKNOWN")
        self.assertTrue(result["eligible"])

    def test_title_alias_relationships(self):
        self.assertEqual(title_relationship("QPB Pharmacist Assistant", "Pharmacist Assistant"), "equivalent")

    def test_brief_source_text_does_not_score_unknown_skills_as_failure(self):
        snapshot = match_snapshot(
            {
                "title": "Medical Sales Representative",
                "description": "Medical Sales Representative Gauteng South Africa",
                "location": "Gauteng",
                "discovered_at": "2026-08-27T00:00:00Z",
            },
            {
                "target_roles": ["Medical Sales Representative"],
                "locations": ["Gauteng"],
                "skills": ["negotiation", "customer service"],
                "resume_profile": {"raw_text": "Experienced healthcare professional."},
            },
        )
        self.assertEqual(snapshot["dimension_details"]["skills_fit"]["availability"], "unknown")
        self.assertIsNone(snapshot["dimensions"]["skills_fit"])
        self.assertTrue(snapshot["strong_match"])
        self.assertLess(snapshot["score_coverage"], 0.6)
        self.assertEqual(snapshot["score_confidence"], "low")
        self.assertLess(snapshot["recommendation_score"], snapshot["overall_score"])
        self.assertEqual(snapshot["actionability_state"], "not_actionable")

    def test_unknown_resume_evidence_is_preparation_needed_not_blocked(self):
        snapshot = match_snapshot(
            {
                "title": "Medical Representative",
                "description": "Responsibilities include customer engagement and customer service in territory visits. Requirements include relevant experience.",
                "location": "Gauteng",
                "apply_url": "https://example.org/apply/1",
                "discovered_at": "2026-08-27T00:00:00Z",
            },
            {
                "target_roles": ["Medical Representative"],
                "locations": ["Gauteng"],
                "skills": ["customer service"],
                "resume_profile": {"raw_text": "Experienced healthcare professional."},
            },
        )
        self.assertTrue(snapshot["strong_match"])
        self.assertEqual(snapshot["candidate_requirement_status"], "MET")
        self.assertEqual(snapshot["resume_evidence_status"], "UNKNOWN")
        self.assertTrue(snapshot["strong_match"])
        self.assertEqual(snapshot["actionability_state"], "prep_needed")
        self.assertFalse(snapshot["actionable"])

    def test_confirmed_mandatory_requirement_missing_is_blocked(self):
        snapshot = match_snapshot(
            {
                "title": "Medical Representative",
                "description": "Responsibilities include territory visits. A driver's licence is required.",
                "location": "Gauteng",
                "apply_url": "https://example.org/apply/2",
                "discovered_at": "2026-08-27T00:00:00Z",
            },
            {
                "target_roles": ["Medical Representative"],
                "locations": ["Gauteng"],
                "skills": ["customer service"],
                "answer_bank": {"drivers_license": "no"},
                "resume_profile": {"raw_text": "Customer service experience."},
            },
        )
        self.assertEqual(snapshot["candidate_requirement_status"], "NOT_MET")
        self.assertEqual(snapshot["actionability_state"], "not_actionable")
        self.assertTrue(snapshot["actionability_blockers"])

    def test_known_candidate_with_weak_cv_wording_needs_preparation(self):
        snapshot = match_snapshot(
            {
                "title": "Medical Representative",
                "description": "Responsibilities include customer engagement and customer service. Professional registration with SAPC is required.",
                "location": "Gauteng",
                "apply_url": "https://example.org/apply/3",
                "discovered_at": "2026-08-27T00:00:00Z",
            },
            {
                "target_roles": ["Medical Representative"],
                "locations": ["Gauteng"],
                "skills": ["customer service"],
                "licenses": ["SAPC registration"],
                "experience_years": 5,
                "resume_profile": {"raw_text": "Healthcare experience."},
            },
        )
        self.assertEqual(snapshot["candidate_requirement_status"], "MET")
        self.assertEqual(snapshot["resume_evidence_status"], "UNKNOWN")
        self.assertEqual(snapshot["actionability_state"], "prep_needed")

    def test_strong_match_with_known_evidence_is_actionable(self):
        snapshot = match_snapshot(
            {
                "title": "Medical Representative",
                "description": "Responsibilities include customer service and pharmaceutical sales. Requirements include relevant experience.",
                "location": "Gauteng",
                "apply_url": "https://example.org/apply/4",
                "discovered_at": "2026-08-27T00:00:00Z",
            },
            {
                "target_roles": ["Medical Representative"],
                "locations": ["Gauteng"],
                "skills": ["customer service", "pharmaceutical sales"],
                "resume_profile": {"raw_text": "Customer service and pharmaceutical sales experience."},
            },
        )
        self.assertEqual(snapshot["resume_evidence_status"], "MET")
        self.assertEqual(snapshot["actionability_state"], "actionable")
        self.assertTrue(snapshot["actionable"])

    def test_claim_validation_flags_unsupported_material_claim(self):
        ledger = build_evidence_ledger({}, {"skills": ["customer service"]}, {})
        result = validate_generated_claims({"cover_letter": "I am registered with SAPC and have a PhD."}, ledger)
        self.assertFalse(result["valid"])
        self.assertTrue(result["issues"])

    def test_evaluation_produces_repeatable_quality_metrics(self):
        report = evaluate_cases()
        self.assertEqual(report["case_count"], 50)
        self.assertEqual(report["unique_case_count"], 49)
        self.assertEqual(report["duplicate_exposure_rate"], 0)
        self.assertEqual(report["hard_exclusion_violation_rate"], 0)
        self.assertEqual(report["expired_job_exposure_rate"], 0)
        self.assertIn("precision_at_10", report)
        self.assertIn("ndcg_at_10", report)
        self.assertIn("recall_at_50", report)


if __name__ == "__main__":
    unittest.main()
