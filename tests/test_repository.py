import gc
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from storage.repository import Repository

TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def repo_temp_dir():
    path = TEST_TMP_DIR / f"repo-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        gc.collect()
        shutil.rmtree(path, ignore_errors=True)


class RepositoryTests(unittest.TestCase):
    def test_expired_jobs_are_retained_as_closed_history(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "expired-job",
                        "title": "Old Role",
                        "company": "Example Pharma",
                        "state": "matched",
                        "fit_score": 12,
                        "is_new": True,
                        "public_service": False,
                        "closing_date": "01-01-2000",
                    },
                    {
                        "id": "active-job",
                        "title": "Current Role",
                        "company": "Example Pharma",
                        "state": "matched",
                        "fit_score": 30,
                        "is_new": True,
                        "public_service": False,
                        "closing_date": "01-01-2099",
                    },
                ],
            )

            jobs = repo.list_jobs(user_id, include_hidden=True)

            self.assertEqual(len(jobs), 2)
            expired = next(job for job in jobs if job["id"] == "expired-job")
            self.assertEqual(expired["state"], "closed")
            self.assertEqual(expired["availability_status"], "expired")
            self.assertEqual(len(repo.list_jobs(user_id)), 1)

    def test_duplicate_jobs_collapse_into_single_record(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "job-a",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://www.linkedin.com/jobs/view/sales-representative-at-ingrain-4393946362/?refId=abc",
                        "state": "matched",
                        "fit_score": 41,
                        "is_new": True,
                    },
                    {
                        "id": "job-b",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://www.linkedin.com/jobs/view/sales-representative-at-ingrain-4393946362/?trackingId=xyz&pageNum=0",
                        "state": "saved",
                        "fit_score": 44,
                        "is_new": False,
                    },
                ],
            )

            jobs = repo.list_jobs(user_id, include_hidden=True)

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["state"], "saved")
            self.assertEqual(jobs[0]["fit_score"], 44)

    def test_duplicate_merge_preserves_source_provenance(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "job-a",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://careers.example/jobs/1",
                        "source_id": "career-site",
                        "external_id": "req-1",
                        "state": "matched",
                        "source_listings": [{"source_id": "career-site", "listing_url": "https://careers.example/jobs/1"}],
                    },
                    {
                        "id": "job-b",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://aggregator.example/jobs/1",
                        "source_id": "aggregator",
                        "external_id": "req-1",
                        "state": "matched",
                        "source_listings": [{"source_id": "aggregator", "listing_url": "https://aggregator.example/jobs/1"}],
                    },
                ],
            )

            jobs = repo.list_jobs(user_id, include_hidden=True)

            self.assertEqual(len(jobs), 1)
            self.assertEqual({item["source_id"] for item in jobs[0]["source_listings"]}, {"career-site", "aggregator"})

    def test_duplicate_merge_keeps_snapshot_and_score_consistent(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_jobs(
                user_id,
                [
                    {
                        "id": "job-a",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://careers.example/jobs/1",
                        "external_id": "req-1",
                        "state": "matched",
                        "fit_score": 95,
                        "match_snapshot": {"overall_score": 72},
                    },
                    {
                        "id": "job-b",
                        "title": "Sales Representative",
                        "company": "Ingrain",
                        "location": "Johannesburg",
                        "listing_url": "https://aggregator.example/jobs/1",
                        "external_id": "req-1",
                        "state": "matched",
                        "fit_score": 68,
                        "match_snapshot": {"overall_score": 68, "score_coverage": 0.46},
                    },
                ],
            )

            job = repo.list_jobs(user_id, include_hidden=True)[0]

            self.assertEqual(job["fit_score"], 68)
            self.assertEqual(job["fit_score"], job["match_snapshot"]["overall_score"])
            self.assertIn("score_coverage", job["match_snapshot"])

    def test_cover_letter_generation_marks_job_ready(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_job(
                user_id,
                {
                    "id": "job-1",
                    "title": "Medical Representative",
                    "company": "Example Pharma",
                    "state": "matched",
                    "fit_score": 42,
                    "is_new": True,
                    "public_service": False,
                    "missing_docs": [],
                    "required_docs": ["Resume / CV"],
                },
            )

            repo.save_cover_letter(
                user_id,
                "job-1",
                {
                    "job_id": "job-1",
                    "cover_letter": "Tailored letter",
                    "motivation_summary": "Summary",
                    "suggested_answers": ["Answer 1"],
                    "cv_alignment_notes": ["Note 1"],
                },
            )

            job = repo.get_job(user_id, "job-1")
            self.assertTrue(job["cover_letter_ready"])
            self.assertTrue(job["generated_answers_ready"])
            self.assertEqual(job["state"], "ready_for_manual_apply")
            del repo

    def test_reset_jobs_clears_jobs_and_cover_letters(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_job(
                user_id,
                {
                    "id": "job-1",
                    "title": "Medical Representative",
                    "company": "Example Pharma",
                    "state": "saved",
                    "fit_score": 42,
                },
            )
            repo.save_cover_letter(user_id, "job-1", {"job_id": "job-1", "cover_letter": "Tailored letter"})

            repo.reset_jobs(user_id)

            self.assertEqual(repo.list_jobs(user_id, include_hidden=True), [])
            self.assertEqual(repo.list_cover_letters(user_id), [])

    def test_delete_job_removes_job_and_cover_letter(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 123
            repo.get_profile(user_id)
            repo.save_job(
                user_id,
                {
                    "id": "job-1",
                    "title": "Medical Representative",
                    "company": "Example Pharma",
                    "state": "saved",
                    "fit_score": 42,
                },
            )
            repo.save_cover_letter(user_id, "job-1", {"job_id": "job-1", "cover_letter": "Tailored letter"})

            removed = repo.delete_job(user_id, "job-1")

            self.assertTrue(removed)
            self.assertIsNone(repo.get_job(user_id, "job-1"))
            self.assertEqual(repo.list_cover_letters(user_id), [])


if __name__ == "__main__":
    unittest.main()
