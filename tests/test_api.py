import os
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app

TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def api_temp_dir():
    path = TEST_TMP_DIR / f"api-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    old_token = os.environ.get("JOBBLAST_API_TOKEN")
    old_user = os.environ.get("JOBBLAST_API_USER_ID")
    os.environ["JOBBLAST_API_TOKEN"] = "test-token"
    os.environ["JOBBLAST_API_USER_ID"] = "123"
    try:
        yield path
    finally:
        if old_token is None:
            os.environ.pop("JOBBLAST_API_TOKEN", None)
        else:
            os.environ["JOBBLAST_API_TOKEN"] = old_token
        if old_user is None:
            os.environ.pop("JOBBLAST_API_USER_ID", None)
        else:
            os.environ["JOBBLAST_API_USER_ID"] = old_user
        shutil.rmtree(path, ignore_errors=True)


class ApiTests(unittest.TestCase):
    def make_client(self, base_dir: Path) -> TestClient:
        app = create_app(base_dir)
        repo = app.state.context.repo
        repo.get_profile(123)
        repo.save_job(
            123,
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
                "why_match": ["Healthcare background"],
            },
        )
        return TestClient(app)

    def auth_headers(self):
        return {"Authorization": "Bearer test-token"}

    def test_auth_is_required(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.get("/v1/home")

            self.assertEqual(response.status_code, 401)

    def test_home_and_job_lists_use_repository_data(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            home = client.get("/v1/home", headers=self.auth_headers())
            jobs = client.get("/v1/jobs?list_name=top", headers=self.auth_headers())
            status = client.get("/v1/status", headers=self.auth_headers())

            self.assertEqual(home.status_code, 200)
            self.assertEqual(home.json()["counts"]["matched"], 1)
            self.assertEqual(jobs.status_code, 200)
            self.assertEqual(jobs.json()["jobs"], [])
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["counts"]["total"], 1)

    def test_pipeline_export_csv_endpoint(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.get("/v1/jobs/export.csv", headers=self.auth_headers())

            self.assertEqual(response.status_code, 200)
            self.assertIn("text/csv", response.headers["content-type"])
            self.assertIn("job-1", response.text)

    def test_job_state_action_updates_existing_job(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.post("/v1/jobs/job-1/save", headers=self.auth_headers())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["job"]["state"], "saved")

    def test_profile_update_merges_settings(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.put(
                "/v1/profile",
                headers=self.auth_headers(),
                json={"profile": {"target_roles": ["Sales Representative"], "minimum_match_threshold": 25}},
            )

            self.assertEqual(response.status_code, 200)
            profile = response.json()["profile"]
            self.assertEqual(profile["target_roles"], ["Sales Representative"])
            self.assertEqual(profile["minimum_match_threshold"], 25)

    def test_document_upload_saves_supporting_document(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.post(
                "/v1/documents",
                headers=self.auth_headers(),
                files={"file": ("certificate.txt", b"certificate content", "text/plain")},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["document"]["original_name"], "certificate.txt")

    def test_cover_letter_endpoint_generates_fallback_package(self):
        with api_temp_dir() as temp_dir:
            client = self.make_client(temp_dir)

            response = client.post("/v1/jobs/job-1/cover-letter", headers=self.auth_headers())

            self.assertEqual(response.status_code, 200)
            self.assertIn("Dear Hiring Manager", response.json()["text"])
            self.assertEqual(response.json()["package"]["job_id"], "job-1")


if __name__ == "__main__":
    unittest.main()
