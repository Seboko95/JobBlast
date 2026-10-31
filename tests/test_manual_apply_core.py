import unittest
from datetime import date

from core.jobs import build_job_id, is_job_expired, shape_job_record
from core.profile import apply_profile_input


class ManualApplyCoreTests(unittest.TestCase):
    def test_apply_profile_input_normalizes_lists(self):
        profile = {"target_roles": []}
        updated, error = apply_profile_input(profile, "target_roles", "Medical Representative, Product Specialist, Medical Representative")
        self.assertIsNone(error)
        self.assertEqual(updated["target_roles"], ["Medical Representative", "Product Specialist"])

    def test_public_service_jobs_require_z83(self):
        job = {
            "id": "raw-job",
            "title": "Administration Clerk",
            "company": "Department of Health",
            "description": "Submit a detailed CV and signed Z83 form.",
            "apply_url": "https://department.gov.za/apply/1",
            "portal_type": "public_portal",
            "application_requirements": {
                "public_service": True,
                "z83_required": True,
                "signed_z83_required": True,
                "application_method": "online_portal",
            },
        }
        candidate = {"target_roles": ["Administration Clerk"], "skills": [], "locations": []}
        shaped = shape_job_record(1, job, candidate, docs=[], resume_available=True)

        self.assertTrue(shaped["public_service"])
        self.assertIn("Z83 form", shaped["required_docs"])
        self.assertIn("Z83 form", shaped["missing_docs"])
        self.assertEqual(shaped["state"], "needs_documents")
        self.assertEqual(shaped["application_method_label"], "Online portal")
        self.assertEqual(shaped["actionability_state"], "prep_needed")
        self.assertEqual(shaped["application_readiness"]["application_document_status"], "missing")
        self.assertTrue(shaped["application_readiness"]["manual_user_action_required"])

    def test_build_job_id_ignores_querystring_variants(self):
        base_job = {
            "title": "Sales Representative",
            "company": "Ingrain",
            "location": "Johannesburg",
            "listing_url": "https://www.linkedin.com/jobs/view/sales-representative-at-ingrain-4393946362/?refId=abc",
        }
        variant_job = {
            **base_job,
            "listing_url": "https://www.linkedin.com/jobs/view/sales-representative-at-ingrain-4393946362/?trackingId=xyz&pageNum=0",
        }

        self.assertEqual(build_job_id(base_job), build_job_id(variant_job))

    def test_is_job_expired_uses_closing_date(self):
        today = date(2026, 4, 27)
        self.assertTrue(is_job_expired({"closing_date": "26-04-2026"}, today=today))
        self.assertFalse(is_job_expired({"closing_date": "28-04-2026"}, today=today))


if __name__ == "__main__":
    unittest.main()
