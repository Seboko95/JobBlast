import inspect
import asyncio
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.app import create_app
from bot.authorization import is_authorized, parse_allowed_users
from core.defaults import default_profile
from core.intelligence import canonical_job_key, match_snapshot
from core.profile import profile_for_matching
from jobblast_ats import http_get, score_job_fit
from services.documents import migrate_legacy_storage, save_resume_bytes, save_supporting_document
from services.job_views import filter_jobs
from services.scanner import run_scan
from services.upload_validation import MAX_UPLOAD_BYTES, UploadValidationError, validate_upload
from storage.repository import Repository


TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def temp_repo():
    path = TEST_TMP_DIR / f"phase0-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class Phase0AndIntelligenceTests(unittest.TestCase):
    def test_hard_excluded_keyword_overrides_positive_match(self):
        profile = profile_for_matching({
            "target_roles": ["Medical Representative"],
            "keywords": ["pharmaceutical"],
            "excluded_keywords": ["internship"],
            "preferred_locations": ["Gauteng"],
        })
        self.assertEqual(score_job_fit("Medical Representative Internship", "pharmaceutical sales Gauteng", profile), 0)
        snapshot = match_snapshot({"title": "Medical Representative Internship", "description": "pharmaceutical sales"}, profile)
        self.assertFalse(snapshot["eligibility"]["eligible"])
        self.assertIn("excluded_keyword", snapshot["eligibility"]["reasons"])

    def test_canonical_job_key_ignores_tracking_query(self):
        first = {"title": "Sales Representative", "company": "Example", "location": "JHB", "listing_url": "https://example.com/jobs/1?utm_source=x"}
        second = {"title": "Sales Representative", "company": "Example", "location": "JHB", "listing_url": "https://example.com/jobs/1?trackingId=y"}
        self.assertEqual(canonical_job_key(first), canonical_job_key(second))

    def test_tls_bypass_is_absent(self):
        self.assertNotIn("verify=False", inspect.getsource(http_get))

    def test_upload_validation_rejects_size_and_signature_mismatch(self):
        with self.assertRaises(UploadValidationError):
            validate_upload("resume.pdf", b"not a pdf", kind="resume")
        with self.assertRaises(UploadValidationError):
            validate_upload("resume.pdf", b"x" * (MAX_UPLOAD_BYTES + 1), kind="resume")

    def test_document_paths_are_user_scoped(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            ok, _, _ = save_resume_bytes(repo, 101, "resume.txt", b"not allowed")
            self.assertFalse(ok)
            saved = save_supporting_document(repo, 101, "certificate.txt", b"certificate content")
            self.assertIn("data/users/101/documents/", saved["filename"])
            self.assertTrue((temp_dir / saved["filename"]).is_file())
            self.assertFalse((temp_dir / "uploaded_cv.pdf").exists())

    def test_legacy_resume_is_copied_into_scoped_storage(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            repo.get_profile(101)
            # A small valid PDF header is insufficient for parsing, but migration is a filesystem operation.
            (temp_dir / "uploaded_cv.pdf").write_bytes(b"legacy resume")
            result = migrate_legacy_storage(repo, 101)
            self.assertEqual(result["resumes"], 1)
            profile = repo.get_profile(101)
            self.assertIn("data/users/101/resumes/", profile["active_resume_path"])
            self.assertTrue((temp_dir / profile["active_resume_path"]).is_file())

    def test_authorization_is_allowlist_only(self):
        allowed = parse_allowed_users("101, 202")
        self.assertTrue(is_authorized(101, allowed))
        self.assertFalse(is_authorized(303, allowed))
        self.assertEqual(parse_allowed_users(""), frozenset())

    def test_defaults_do_not_contain_personal_identity(self):
        profile = default_profile()
        self.assertEqual(profile["name"], "")
        self.assertEqual(profile["email"], "")
        self.assertEqual(profile["phone"], "")

    def test_api_rejects_oversized_upload(self):
        with temp_repo() as temp_dir:
            import os
            old_token = os.environ.get("JOBBLAST_API_TOKEN")
            old_user = os.environ.get("JOBBLAST_API_USER_ID")
            os.environ["JOBBLAST_API_TOKEN"] = "test-token"
            os.environ["JOBBLAST_API_USER_ID"] = "101"
            try:
                client = TestClient(create_app(temp_dir))
                response = client.post(
                    "/v1/documents",
                    headers={"Authorization": "Bearer test-token"},
                    files={"file": ("large.txt", b"x" * (MAX_UPLOAD_BYTES + 1), "text/plain")},
                )
                self.assertEqual(response.status_code, 413)
            finally:
                if old_token is None:
                    os.environ.pop("JOBBLAST_API_TOKEN", None)
                else:
                    os.environ["JOBBLAST_API_TOKEN"] = old_token
                if old_user is None:
                    os.environ.pop("JOBBLAST_API_USER_ID", None)
                else:
                    os.environ["JOBBLAST_API_USER_ID"] = old_user

    def test_application_history_and_scan_run_are_durable(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            repo.save_job(101, {"id": "job-1", "title": "Role", "company": "Example", "state": "saved"})
            updated = repo.update_job_state(101, "job-1", "interview")
            self.assertEqual(updated["state_history"][-1]["to"], "interview")
            run_id = repo.start_scan_run(101)
            repo.finish_scan_run(run_id, "completed", {"new_count": 1})
            self.assertEqual(repo.list_scan_runs(101)[0]["status"], "completed")

    def test_scan_reconciles_and_separates_new_jobs(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 101
            repo.get_profile(user_id)
            discovered = {
                "title": "Example Role",
                "company": "Example Employer",
                "location": "Johannesburg",
                "listing_url": "https://example.com/jobs/example-role",
                "description": "A role with clear responsibilities.",
                "source_id": "test-source",
            }
            source_results = [{"source_id": "test-source", "matched": 1, "new": 1}]
            with patch(
                "services.scanner.scan_enabled_sources",
                new=AsyncMock(return_value=([dict(discovered)], source_results)),
            ):
                first = asyncio.run(run_scan(repo, user_id))
            self.assertEqual(len(first["new_jobs"]), 1)
            self.assertEqual(len(first["reconciled_jobs"]), 0)

            with patch(
                "services.scanner.scan_enabled_sources",
                new=AsyncMock(return_value=([dict(discovered)], source_results)),
            ):
                second = asyncio.run(run_scan(repo, user_id))
            self.assertEqual(len(second["new_jobs"]), 0)
            self.assertEqual(len(second["reconciled_jobs"]), 1)
            saved = repo.get_job(user_id, second["reconciled_jobs"][0]["id"])
            self.assertEqual(saved["fit_score"], saved["match_snapshot"]["overall_score"])
            self.assertEqual(repo.list_scan_runs(user_id)[0]["status"], "completed")

    def test_scan_marks_post_source_failure(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 101
            repo.get_profile(user_id)
            discovered = {
                "title": "Example Role",
                "company": "Example Employer",
                "listing_url": "https://example.com/jobs/example-role",
                "description": "A role.",
                "source_id": "test-source",
            }
            with patch(
                "services.scanner.scan_enabled_sources",
                new=AsyncMock(return_value=([discovered], [])),
            ), patch(
                "services.scanner.shape_job_record",
                side_effect=RuntimeError("test shaping failure"),
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(run_scan(repo, user_id))
            self.assertEqual(repo.list_scan_runs(user_id)[0]["status"], "failed")

    def test_top_matches_require_strong_eligible_status_after_rescan(self):
        jobs = [
            {
                "id": "review-only",
                "title": "Medical Representative",
                "eligibility_status": "eligible",
                "strong_match": True,
                "actionable": False,
                "fit_score": 88,
            },
            {
                "id": "actionable",
                "title": "Medical Representative",
                "eligibility_status": "eligible",
                "strong_match": True,
                "actionable": True,
                "fit_score": 80,
            },
            {
                "id": "ineligible",
                "title": "Medical Representative Internship",
                "eligibility_status": "ineligible",
                "strong_match": False,
                "actionable": False,
                "fit_score": 99,
            },
            {
                "id": "stale",
                "title": "Medical Representative",
                "eligibility_status": "eligible",
                "strong_match": True,
                "actionable": False,
                "freshness_status": "stale_unverified",
                "fit_score": 98,
            },
        ]
        self.assertEqual([job["id"] for job in filter_jobs(jobs, "top")], ["review-only", "actionable"])

    def test_top_matches_do_not_promote_structured_legacy_without_actionability(self):
        jobs = [{
            "id": "legacy-structured",
            "eligibility_status": "eligible",
            "strong_match": True,
            "freshness_status": "active_unverified",
            "trust_status": "unverified",
            "match_snapshot": {"overall_score": 88},
            "fit_score": 88,
        }]
        self.assertEqual(filter_jobs(jobs, "top"), [])

    def test_notification_deduplication_is_durable(self):
        with temp_repo() as temp_dir:
            repo = Repository(temp_dir)
            self.assertTrue(repo.record_notification(101, "strong:job-1", "strong_match", {"job_id": "job-1"}))
            self.assertFalse(repo.record_notification(101, "strong:job-1", "strong_match", {"job_id": "job-1"}))
            self.assertTrue(repo.notification_sent(101, "strong:job-1"))


if __name__ == "__main__":
    unittest.main()
