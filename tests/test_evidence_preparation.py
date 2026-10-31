import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from core.evidence import audit_requirements, build_evidence_ledger, validate_generated_claims
from core.jobs import shape_job_record
from storage.repository import Repository


class EvidencePreparationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parent / ".tmp" / f"evidence-{uuid4().hex}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.repo = Repository(self.path)
        self.user_id = 777

    def tearDown(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def test_ledger_has_provenance_and_persists(self):
        profile = {"name": "Synthetic User", "target_roles": ["Sales Representative"]}
        resume = {"sha256": "abc123", "skills": ["Excel"], "education": ["Diploma"]}
        answers = {"drivers_license": "Yes"}
        facts = build_evidence_ledger(profile, resume, answers)
        self.repo.save_profile(self.user_id, profile)
        self.repo.save_resume_profile(self.user_id, resume)
        self.repo.save_answers(self.user_id, answers)
        self.repo.replace_evidence_facts(self.user_id, facts)

        persisted = self.repo.get_evidence_facts(self.user_id)
        skill = next(item for item in persisted if item["fact_type"] == "skill")
        self.assertEqual(skill["source_reference"], "abc123")
        self.assertIn("fact_id", skill)
        self.assertIn("allowed_in_generation", skill)

    def test_requirement_synonym_is_partial_without_exact_confirmation(self):
        candidate = {
            "answer_bank": {},
            "evidence_ledger": build_evidence_ledger({}, {"skills": ["Excel"]}, {}),
        }
        audit = audit_requirements(
            {"description": "Microsoft Office experience is required."},
            candidate,
        )
        self.assertEqual(audit[0]["candidate_status"], "LIKELY_MET")
        self.assertEqual(audit[0]["evidence_status"], "PARTIAL")

    def test_confirmed_negative_requirement_is_not_actionable(self):
        candidate = {"answer_bank": {"drivers_license": "no"}, "evidence_ledger": []}
        audit = audit_requirements({"description": "A driver's licence is required."}, candidate)
        self.assertEqual(audit[0]["candidate_status"], "NOT_MET")
        self.assertTrue(audit[0]["blocking"])

    def test_claim_validator_returns_machine_readable_status(self):
        ledger = build_evidence_ledger({}, {"skills": ["customer service"]}, {})
        result = validate_generated_claims(
            {"cover_letter": "I am registered with SAPC and have a PhD."},
            ledger,
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["claims"][0]["status"], "USER_CONFIRMATION_REQUIRED")

    def test_preparation_plan_separates_manual_submission(self):
        shaped = shape_job_record(
            self.user_id,
            {
                "title": "Medical Representative",
                "company": "Synthetic Pharma",
                "location": "Gauteng",
                "description": "Pharmaceutical sales and customer service.",
                "apply_url": "https://example.org/apply",
            },
            {
                "target_roles": ["Medical Representative"],
                "skills": ["pharmaceutical sales"],
                "locations": ["Gauteng"],
                "resume_profile": {"raw_text": "Pharmaceutical sales experience."},
            },
            [],
            True,
        )
        self.assertIn("manual_items", shaped["preparation_plan"])
        self.assertIn("submit manually", shaped["preparation_plan"]["manual_items"][0])

    def test_application_artifact_snapshot_is_preserved(self):
        self.repo.save_job(self.user_id, {"id": "job-1", "title": "Role", "listing_url": "https://example.org/1", "state": "saved"})
        updated = self.repo.update_job_state(self.user_id, "job-1", "applied_by_user", applied_at="2026-08-27T07:00:00Z")
        self.assertEqual(updated["application_artifact_snapshot"]["application_url"], "https://example.org/1")
        self.assertEqual(updated["state_history"][0]["to"], "applied_by_user")

    def test_user_confirmation_is_stored_and_removable(self):
        self.repo.get_profile(self.user_id)
        self.repo.save_confirmed_fact(self.user_id, "drivers_license", "yes")
        answers = self.repo.get_answers(self.user_id)
        self.assertEqual(answers["drivers_license"], "yes")
        self.assertTrue(any(item["fact_type"] == "drivers_license" for item in self.repo.get_evidence_facts(self.user_id)))
        self.repo.remove_confirmed_fact(self.user_id, "drivers_license")
        self.assertNotIn("drivers_license", self.repo.get_answers(self.user_id).get("confirmed_facts", {}))


if __name__ == "__main__":
    unittest.main()
