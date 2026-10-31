import shutil
import unittest
from contextlib import contextmanager
from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from core.feedback import build_feedback_profile
from jobblast_ats import score_job_fit
from services.application_pack import export_application_pack
from services.job_views import bot_status, filter_jobs
from services.pipeline_export import export_jobs_csv
from services.reminders import collect_job_reminders
from services.source_health import source_health_status, update_source_health
from storage.repository import Repository

TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def repo_temp_dir():
    path = TEST_TMP_DIR / f"features-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class AssistantFeatureTests(unittest.TestCase):
    def test_feedback_profile_boosts_similar_jobs(self):
        feedback = build_feedback_profile(
            [
                {
                    "title": "Medical Representative",
                    "description": "pharmaceutical sales otc healthcare",
                    "company": "Example Pharma",
                    "location": "Johannesburg",
                    "portal_type": "linkedin",
                    "source_name": "LinkedIn Jobs",
                    "state": "saved",
                },
                {
                    "title": "Receptionist",
                    "description": "front desk admin",
                    "company": "Example Admin",
                    "location": "Johannesburg",
                    "portal_type": "generic",
                    "source_name": "Other",
                    "state": "skipped",
                },
            ]
        )
        base_profile = {"target_roles": [], "search_terms": [], "skills": [], "certifications": [], "licenses": [], "locations": []}
        scored_profile = {**base_profile, "feedback_profile": feedback}

        base_score = score_job_fit("Medical Representative", "pharmaceutical healthcare role", base_profile)
        boosted_score = score_job_fit("Medical Representative", "pharmaceutical healthcare role", scored_profile, {"portal_type": "linkedin", "source_name": "LinkedIn Jobs"})
        reduced_score = score_job_fit("Receptionist", "front desk admin role", scored_profile, {"portal_type": "generic", "source_name": "Other"})

        self.assertGreater(boosted_score, base_score)
        self.assertLess(reduced_score, boosted_score)

    def test_non_preference_feedback_does_not_penalize_future_matching(self):
        job = {
            "id": "job-1",
            "title": "Medical Representative",
            "description": "pharmaceutical sales healthcare",
            "company": "Example Pharma",
            "location": "Johannesburg",
            "state": "matched",
        }
        feedback = build_feedback_profile([job], [{"job_id": "job-1", "label": "not_for_me", "reason": "duplicate"}])
        self.assertEqual(feedback["negative_terms"], [])

    def test_collect_job_reminders_emits_once_and_persists_flags(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 321
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "deadline-job",
                        "title": "Medical Representative",
                        "company": "Example Pharma",
                        "state": "saved",
                        "closing_date": "30-04-2026",
                        "fit_score": 55,
                    },
                    {
                        "id": "applied-job",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "state": "applied_by_user",
                        "applied_at": "2026-04-20T09:00:00Z",
                        "fit_score": 44,
                    },
                ],
            )

            reminders = collect_job_reminders(repo, user_id, today=date(2026, 4, 27))
            second_run = collect_job_reminders(repo, user_id, today=date(2026, 4, 27))

            self.assertEqual(len(reminders["deadlines"]), 1)
            self.assertEqual(len(reminders["follow_ups"]), 1)
            self.assertEqual(second_run["deadlines"], [])
            self.assertEqual(second_run["follow_ups"], [])

    def test_collect_job_reminders_handles_interview_and_offer_once(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 322
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "interview-job",
                        "title": "Medical Representative",
                        "state": "interview",
                        "interview_at": "2026-04-28T09:00:00Z",
                    },
                    {"id": "offer-job", "title": "Sales Representative", "state": "offer"},
                ],
            )
            reminders = collect_job_reminders(repo, user_id, today=date(2026, 4, 27))
            second_run = collect_job_reminders(repo, user_id, today=date(2026, 4, 27))
            self.assertEqual(len(reminders["interviews"]), 1)
            self.assertEqual(len(reminders["outcomes"]), 1)
            self.assertEqual(second_run["interviews"], [])
            self.assertEqual(second_run["outcomes"], [])

    def test_application_pack_contains_expected_artifacts(self):
        filename, payload = export_application_pack(
            {
                "name": "Test User",
                "address_line": "123 Main Road",
                "city_province_postal": "Johannesburg, Gauteng, 2000",
                "email": "test@example.com",
                "phone": "+27 12 000 0000",
            },
            {
                "title": "Medical Representative",
                "company": "Example Pharma",
                "location": "Johannesburg",
                "apply_url": "https://example.com/jobs/1",
                "source_name": "LinkedIn Jobs",
                "portal_type": "linkedin",
                "fit_score": 88,
                "why_match": ["Role alignment: Medical Representative"],
                "required_docs": ["Resume / CV", "Cover letter / motivation"],
            },
            {
                "cover_letter": "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\nParagraph four.",
                "suggested_answers": ["Answer one"],
                "cv_alignment_notes": ["Lead with OTC experience."],
                "strong_matches": ["Role alignment: Medical Representative"],
                "weaker_matches": ["Location not explicitly confirmed."],
                "missing_evidence": ["Driver's licence should be stated."],
                "cv_tailoring_notes": ["Mirror the vacancy title in the CV headline."],
            },
        )

        self.assertTrue(filename.endswith(".zip"))
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            self.assertIn("01_job_summary.txt", names)
            self.assertIn("02_how_to_apply.txt", names)
            self.assertIn("03_cover_letter.txt", names)
            self.assertIn("05_application_answers.txt", names)
            self.assertIn("06_gap_analysis.txt", names)
            self.assertTrue(any(name.endswith("_cover_letter.docx") for name in names))
            summary = archive.read("01_job_summary.txt").decode("utf-8")
            gap = archive.read("06_gap_analysis.txt").decode("utf-8")
            self.assertIn("Medical Representative", summary)
            self.assertIn("Strong matches:", gap)

    def test_source_health_status_marks_low_yield_after_streak(self):
        source = {"name": "Careers24"}
        update_source_health(source, matched=0, new=0)
        update_source_health(source, matched=0, new=0)
        update_source_health(source, matched=0, new=0)

        self.assertEqual(source_health_status(source), "low_yield")

    def test_urgent_stale_status_and_csv_export(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 654
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "urgent-job",
                        "title": "Medical Representative",
                        "company": "Example Pharma",
                        "state": "matched",
                        "eligibility_status": "eligible",
                        "fit_score": 75,
                        "closing_date": "05-08-2026",
                        "updated_at": "2026-08-01T08:00:00Z",
                    },
                    {
                        "id": "stale-job",
                        "title": "Product Specialist",
                        "company": "Example Devices",
                        "state": "saved",
                        "fit_score": 55,
                        "updated_at": "2026-06-01T08:00:00Z",
                    },
                ],
            )

            jobs = repo.list_jobs(user_id, today=date(2026, 8, 1))
            urgent = filter_jobs(jobs, "urgent", today=date(2026, 8, 1))
            stale = filter_jobs(jobs, "stale", today=date(2026, 8, 1))
            status = bot_status(repo, user_id, today=date(2026, 8, 1))
            csv_payload = export_jobs_csv(jobs).decode("utf-8-sig")

            self.assertEqual([job["id"] for job in urgent], ["urgent-job"])
            self.assertEqual([job["id"] for job in stale], ["stale-job"])
            self.assertEqual(status["counts"]["total"], 2)
            self.assertIn("urgent-job", csv_payload)
            self.assertIn("recommended_next_action", csv_payload)


if __name__ == "__main__":
    unittest.main()
