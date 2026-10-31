import asyncio
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from telegram.error import TimedOut

from bot.handlers import ManualApplyBot
from bot import views
from core.intelligence import match_snapshot
from services.job_views import filter_jobs
from services.official_verification import apply_verification, verify_job, verify_priority_jobs
from storage.repository import Repository


TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"


class FakeQuery:
    def __init__(self, data, user_id=101):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=user_id), message_id=10, edit_text=AsyncMock())

    async def answer(self, *args, **kwargs):
        return None


class FakeUpdate:
    def __init__(self, data, user_id=101):
        self.callback_query = FakeQuery(data, user_id)
        self.effective_user = self.callback_query.from_user
        self.effective_chat = self.callback_query.message.chat


class TelegramProductTests(unittest.TestCase):
    def setUp(self):
        self.path = TEST_TMP_DIR / f"telegram-product-{uuid4().hex}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.repo = Repository(self.path)
        self.user_id = 101
        self.repo.get_profile(self.user_id)

    def tearDown(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def test_home_exposes_normal_product_paths(self):
        callbacks = [button.callback_data for row in views.home_keyboard().inline_keyboard for button in row]
        self.assertIn("nav:list:ready:0", callbacks)
        self.assertIn("nav:applications", callbacks)
        self.assertIn("nav:documents", callbacks)
        self.assertIn("nav:evidence", callbacks)
        self.assertIn("nav:government", callbacks)

    def test_preparation_screen_exposes_material_and_manual_apply_actions(self):
        job = {"id": "job-1", "listing_url": "https://example.org/job", "preparation_plan": {"confirmation_items": []}}
        callbacks = [button.callback_data for row in views.preparation_keyboard(job, "top", 0).inline_keyboard for button in row if button.callback_data]
        self.assertIn("job:cover:job-1:top:0", callbacks)
        self.assertIn("job:pack:job-1:top:0", callbacks)
        self.assertIn("job:mark:job-1:top:0", callbacks)

    def test_application_lists_and_ready_list_are_state_safe(self):
        self.repo.save_job(self.user_id, {"id": "ready", "title": "Ready Role", "company": "A", "listing_url": "https://a.example/1", "state": "matched", "actionable": True, "actionability_state": "actionable", "fit_score": 90})
        self.repo.save_job(self.user_id, {"id": "applied", "title": "Applied Role", "company": "B", "listing_url": "https://b.example/1", "state": "applied_by_user", "actionable": False, "fit_score": 90})
        self.assertEqual([job["id"] for job in filter_jobs(self.repo.list_jobs(self.user_id), "ready")], ["ready"])
        self.assertEqual([job["id"] for job in filter_jobs(self.repo.list_jobs(self.user_id), "applied")], ["applied"])

    def test_lifecycle_metadata_survives_restart_like_repository_reload(self):
        self.repo.save_job(self.user_id, {"id": "job-1", "title": "Role", "company": "Employer", "state": "applied_by_user"})
        self.repo.update_job_fields(
            self.user_id,
            "job-1",
            application_date="2026-08-27",
            application_method="official portal",
            follow_up_at="2026-09-03",
            contact={"name": "Recruitment", "email": "candidate@example.com"},
            interview_at="2026-09-10 09:00",
            offer={"compensation": "R30,000", "decision_deadline": "2026-09-15"},
        )
        reloaded = Repository(self.path).get_job(self.user_id, "job-1")
        self.assertEqual(reloaded["follow_up_at"], "2026-09-03")
        self.assertEqual(reloaded["contact"]["email"], "candidate@example.com")
        self.assertEqual(reloaded["offer"]["compensation"], "R30,000")

    def test_callback_routes_application_dashboard_and_clears_pending_input(self):
        self.repo.save_ui_state(self.user_id, pending_input={"type": "job_field", "field": "application_date", "job_id": "missing"})
        controller = ManualApplyBot(self.repo, frozenset({self.user_id}))
        update = FakeUpdate("nav:applications", self.user_id)
        asyncio.run(controller.callback(update, SimpleNamespace()))
        rendered = update.callback_query.message.edit_text.await_args.args[0]
        self.assertIn("APPLICATIONS", rendered)
        self.assertIsNone(self.repo.get_ui_state(self.user_id).get("pending_input"))

    def test_stale_callback_is_user_friendly_and_does_not_raise(self):
        controller = ManualApplyBot(self.repo, frozenset({self.user_id}))
        update = FakeUpdate("job:view:gone:top:0", self.user_id)
        asyncio.run(controller.callback(update, SimpleNamespace()))
        rendered = update.callback_query.message.edit_text.await_args.args[0]
        self.assertIn("no longer available", rendered)

    def test_role_matching_ignores_generic_single_word_role(self):
        candidate = {"target_roles": ["sales"], "locations": [], "skills": [], "keywords": [], "excluded_keywords": [], "blocked_employers": []}
        snapshot = match_snapshot({"title": "Customer Service Manager", "description": "Customer service support role."}, candidate)
        self.assertEqual(snapshot["dimensions"]["role_fit"], 10)

    def test_official_verification_records_title_match_and_tls_failure(self):
        class GoodClient:
            async def get(self, url, **kwargs):
                return SimpleNamespace(status_code=200, url=url, text="Post-Basic Pharmacist Assistant careers vacancy")

        job = {"id": "official-1", "title": "Post-Basic Pharmacist Assistant", "source_kind": "public_jobs_portal", "apply_url": "https://jobs.example.gov.za/vacancy/1"}
        verified = asyncio.run(verify_job(job, GoodClient()))
        self.assertEqual(verified["status"], "source_verified")
        self.assertTrue(verified["attempted"])

        class TLSClient:
            async def get(self, url, **kwargs):
                request = httpx.Request("GET", url)
                raise httpx.ConnectError("certificate verify failed", request=request)

        degraded = asyncio.run(verify_job(job, TLSClient()))
        self.assertEqual(degraded["status"], "degraded")
        self.assertEqual(degraded["method"], "tls_failure")

    def test_feedback_undo_removes_only_latest_event(self):
        first = self.repo.save_feedback(self.user_id, "job-1", "relevant")
        latest = self.repo.save_feedback(self.user_id, "job-1", "not_for_me", reason="wrong_location")
        self.assertTrue(self.repo.delete_feedback(self.user_id, latest["id"]))
        events = self.repo.list_feedback(self.user_id)
        self.assertEqual([event["id"] for event in events], [first["id"]])
        callbacks = [button.callback_data for row in views.feedback_saved_keyboard(latest["id"], "job-1", "top", 0).inline_keyboard for button in row if button.callback_data]
        self.assertIn(f"job:feedbackundo:{latest['id']}:job-1:top:0", callbacks)

    def test_verification_cache_skips_recent_unchanged_job(self):
        job = {
            "id": "job-cache",
            "title": "Pharmacist Assistant",
            "source_kind": "public_jobs_portal",
            "apply_url": "https://jobs.example.gov.za/vacancy/1",
            "content_hash": "content-1",
            "revision": 2,
            "strong_match": True,
            "verification_status": "source_verified",
            "verification_method": "official_page_title_match",
        }
        checked = "2026-08-27T12:00:00Z"
        cached_job = apply_verification(job, {"status": "source_verified", "method": "official_page_title_match", "authoritative_url": job["apply_url"]}, checked)
        cached_job["verification_checked_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = asyncio.run(verify_priority_jobs([cached_job]))
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["cache_hits"], 1)
        self.assertEqual(result["verified"], 1)

    def test_z83_preparation_is_structured_and_manual(self):
        job = {"id": "public-1", "title": "Officer", "company": "Department", "reference_number": "REF-1", "closing_date": "2099-12-31", "application_requirements": {"z83_required": True}}
        text = views.z83_preparation_text(job, {"name": "Candidate", "email": "candidate@example.com"})
        self.assertIn("Vacancy fields to verify", text)
        self.assertIn("Manual confirmation required", text)
        self.assertIn("never signs, certifies, or submits", text)

    def test_telegram_delivery_timeout_does_not_raise_from_transient_message(self):
        class Bot:
            send_message = AsyncMock(side_effect=TimedOut())

        controller = ManualApplyBot(self.repo, frozenset({self.user_id}))
        context = SimpleNamespace(bot=Bot())
        asyncio.run(controller._reply_transient(None, context, self.user_id, "progress"))


if __name__ == "__main__":
    unittest.main()
