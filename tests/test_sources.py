import asyncio
import json
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from jobblast_ats import classify_source_issue, sanitize_sources, validate_source_input
from sources import registry
from services.source_health import cooldown_active, source_health_status, update_source_health
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
        shutil.rmtree(path, ignore_errors=True)


class SourceUtilityTests(unittest.TestCase):
    def test_validate_source_input_rejects_multiple_urls(self):
        with self.assertRaises(ValueError):
            validate_source_input(
                "Broken Paste",
                "https://example.com/jobs https://second.example.com/jobs",
            )

    def test_sanitize_sources_salvages_first_url_and_dedupes(self):
        raw = [
            {
                "id": "bad-1",
                "name": "Adcock Ingram",
                "kind": "company_portal",
                "seed_url": "https://www.adcock.com/Careers /addportal Aspen | https://www.aspenpharma.com/careers/",
            },
            {
                "id": "good-1",
                "name": "Adcock Ingram",
                "kind": "company_portal",
                "seed_url": "https://www.adcock.com/Careers",
            },
        ]

        cleaned = sanitize_sources(raw)
        adcock = [item for item in cleaned if "adcock.com" in item.get("seed_url", "")]

        self.assertEqual(len(adcock), 1)
        self.assertNotIn("/addportal", adcock[0]["seed_url"].lower())

    def test_http_failures_are_classified_as_warnings(self):
        self.assertEqual(classify_source_issue("Client error '403 Forbidden'"), "warning")
        self.assertEqual(classify_source_issue("certificate verify failed"), "warning")
        self.assertEqual(classify_source_issue("unexpected parser bug"), "error")

    def test_source_health_tracks_success_and_cooldown(self):
        source = {}
        update_source_health(source, matched=0, new=0, warning="blocked", failure_category="http_403")
        update_source_health(source, matched=0, new=0, warning="blocked", failure_category="http_403")
        update_source_health(source, matched=0, new=0, warning="blocked", failure_category="http_403")
        self.assertTrue(cooldown_active(source))
        self.assertEqual(source_health_status(source), "cooldown")
        update_source_health(source, matched=2, new=1)
        self.assertFalse(cooldown_active(source))
        self.assertEqual(source["consecutive_failures"], 0)
        self.assertTrue(source["last_success"])


class RepositorySourceCleanupTests(unittest.TestCase):
    def test_list_sources_sanitizes_bad_rows_and_adds_defaults(self):
        with repo_temp_dir() as temp_dir:
            repo = Repository(temp_dir)
            user_id = 77
            repo.get_profile(user_id)
            bad_source = {
                "id": "broken-1",
                "name": "Broken Paste",
                "kind": "company_portal",
                "seed_url": "https://www.adcock.com/Careers /addportal Aspen | https://www.aspenpharma.com/careers/",
                "enabled": True,
                "schedule_enabled": True,
                "scan_mode": "assist_apply",
                "last_checked": None,
                "last_result": "",
                "last_error": "",
                "added_at": "2026-04-27T00:00:00Z",
            }
            with repo.connect() as conn:
                conn.execute("DELETE FROM sources WHERE user_id=?", (user_id,))
                conn.execute(
                    "INSERT INTO sources(id, user_id, enabled, sort_index, data) VALUES(?, ?, ?, ?, ?)",
                    (bad_source["id"], user_id, 1, 0, json.dumps(bad_source)),
                )

            sources = repo.list_sources(user_id)

            self.assertTrue(any(item.get("name") == "Careers24" for item in sources))
            self.assertFalse(any("/addportal" in item.get("seed_url", "").lower() for item in sources))


class RegistryWarningTests(unittest.TestCase):
    def test_scan_registry_keeps_warnings_out_of_error_count(self):
        source = {
            "id": "source-1",
            "name": "Blocked Source",
            "kind": "company_portal",
            "seed_url": "https://example.com/jobs",
            "enabled": True,
            "schedule_enabled": True,
        }

        async def fake_scan_source(_source, _candidate):
            raise RuntimeError("Client error '403 Forbidden' for url 'https://example.com/jobs'")

        original = registry.scan_source
        registry.scan_source = fake_scan_source
        try:
            discovered, results = asyncio.run(registry.scan_enabled_sources([source], {}, {}, scheduled_only=False))
        finally:
            registry.scan_source = original

        self.assertEqual(discovered, [])
        self.assertEqual(results[0]["error"], "")
        self.assertIn("403 Forbidden", results[0]["warning"])
        self.assertTrue(source["last_result"].startswith("warning:"))


if __name__ == "__main__":
    unittest.main()
