"""SQLite-backed repository for the manual-apply JobBlast assistant."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.defaults import default_profile
from core.intelligence import identity_match
from core.jobs import is_job_expired, job_identity_key, shape_job_record
from core.profile import build_candidate, merge_profile
from jobblast_ats import build_answer_bank_defaults, build_source, default_sources, sanitize_sources, validate_source_input


class Repository:
    """Small repository that stores JSON payloads in SQLite tables."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.db_path = base_dir / "jobblast.db"
        self.paths = {
            "profiles": base_dir / "profile.json",
            "answers": base_dir / "answers.json",
            "resume_profiles": base_dir / "resume_profiles.json",
            "docs": base_dir / "docs_meta.json",
            "sources": base_dir / "sources.json",
            "jobs": base_dir / "jobs.json",
            "applications": base_dir / "applications.json",
            "owner": base_dir / "owner.json",
            "career_sites": base_dir / "career_sites.json",
        }
        self.setup()
        self.migrate_from_json()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _dumps(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _loads(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _state_rank(job: dict[str, Any]) -> int:
        ranks = {
            "applied_by_user": 7,
            "accepted": 8,
            "offer": 7,
            "interview": 7,
            "rejected": 1,
            "withdrawn": 1,
            "preparing": 5,
            "ready_to_apply": 6,
            "ready_for_manual_apply": 6,
            "needs_documents": 5,
            "saved": 4,
            "matched": 3,
            "discovered": 2,
            "skipped": 1,
            "closed": 0,
        }
        return ranks.get(str(job.get("state") or "matched"), 3)

    @staticmethod
    def _merge_string_lists(left: list[str] | None, right: list[str] | None) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in list(left or []) + list(right or []):
            value = str(item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(value)
        return merged

    def _merge_duplicate_jobs(self, existing: dict[str, Any], incoming: dict[str, Any], cover_letter_ids: set[str]) -> dict[str, Any]:
        prefer_incoming = (
            self._state_rank(incoming) > self._state_rank(existing)
            or bool(incoming.get("cover_letter_ready")) and existing.get("id") not in cover_letter_ids
            or (int(incoming.get("fit_score") or 0), str(incoming.get("updated_at") or "")) > (int(existing.get("fit_score") or 0), str(existing.get("updated_at") or ""))
        )
        primary = dict(incoming if prefer_incoming else existing)
        secondary = existing if prefer_incoming else incoming
        primary["state"] = primary.get("state") if self._state_rank(primary) >= self._state_rank(secondary) else secondary.get("state")
        primary_snapshot = primary.get("match_snapshot")
        secondary_snapshot = secondary.get("match_snapshot")
        primary_snapshot_is_current = isinstance(primary_snapshot, dict) and "score_coverage" in primary_snapshot
        secondary_snapshot_is_current = isinstance(secondary_snapshot, dict) and "score_coverage" in secondary_snapshot
        if secondary_snapshot_is_current and not primary_snapshot_is_current:
            primary["match_snapshot"] = dict(secondary_snapshot)
            primary_snapshot = primary["match_snapshot"]
        if isinstance(primary_snapshot, dict) and primary_snapshot.get("overall_score") is not None:
            primary["fit_score"] = int(primary_snapshot.get("overall_score") or 0)
            primary["recommendation_score"] = int(primary_snapshot.get("recommendation_score") or primary["fit_score"])
        else:
            primary["fit_score"] = max(int(primary.get("fit_score") or 0), int(secondary.get("fit_score") or 0))
            primary["recommendation_score"] = int(primary.get("recommendation_score") or primary["fit_score"])
        primary["is_new"] = bool(primary.get("is_new", True) or secondary.get("is_new", True))
        primary["public_service"] = bool(primary.get("public_service") or secondary.get("public_service"))
        primary["required_docs"] = self._merge_string_lists(primary.get("required_docs"), secondary.get("required_docs"))
        primary["missing_docs"] = self._merge_string_lists(primary.get("missing_docs"), secondary.get("missing_docs"))
        primary["why_match"] = self._merge_string_lists(primary.get("why_match"), secondary.get("why_match"))
        primary["notes"] = self._merge_string_lists(primary.get("notes"), secondary.get("notes"))
        primary["cover_letter_ready"] = bool(primary.get("cover_letter_ready") or secondary.get("cover_letter_ready") or primary.get("id") in cover_letter_ids or secondary.get("id") in cover_letter_ids)
        primary["generated_answers_ready"] = bool(primary.get("generated_answers_ready") or secondary.get("generated_answers_ready"))
        primary["applied_at"] = primary.get("applied_at") or secondary.get("applied_at")
        primary["closing_date"] = primary.get("closing_date") or secondary.get("closing_date")
        primary["listing_url"] = primary.get("listing_url") or secondary.get("listing_url")
        primary["apply_url"] = primary.get("apply_url") or secondary.get("apply_url")
        primary["source_name"] = primary.get("source_name") or secondary.get("source_name")
        primary["reminder_flags"] = {**dict(secondary.get("reminder_flags") or {}), **dict(primary.get("reminder_flags") or {})}
        primary["source_ids"] = self._merge_string_lists(primary.get("source_ids"), secondary.get("source_ids"))
        listings: dict[str, dict[str, Any]] = {}
        for listing in list(secondary.get("source_listings") or []) + list(primary.get("source_listings") or []):
            if not isinstance(listing, dict):
                continue
            key = str(listing.get("source_id") or listing.get("listing_url") or "")
            if key:
                listings[key] = listing
        primary["source_listings"] = list(listings.values())
        primary["revision"] = max(int(primary.get("revision") or 0), int(secondary.get("revision") or 0))
        primary["revision_changes"] = self._merge_string_lists(primary.get("revision_changes"), secondary.get("revision_changes"))
        primary["trust_score"] = max(int(primary.get("trust_score") or 0), int(secondary.get("trust_score") or 0))
        primary["positive_signals"] = self._merge_string_lists(primary.get("positive_signals"), secondary.get("positive_signals"))
        primary["risk_signals"] = self._merge_string_lists(primary.get("risk_signals"), secondary.get("risk_signals"))
        primary["last_verified"] = max(str(primary.get("last_verified") or ""), str(secondary.get("last_verified") or ""))
        primary["availability_status"] = primary.get("availability_status") or secondary.get("availability_status")
        primary["state_history"] = (list(secondary.get("state_history") or []) + list(primary.get("state_history") or []))[-50:]
        primary["discovered_at"] = min(str(primary.get("discovered_at") or "9999-99-99"), str(secondary.get("discovered_at") or "9999-99-99"))
        if primary["discovered_at"] == "9999-99-99":
            primary["discovered_at"] = ""
        primary["updated_at"] = max(str(primary.get("updated_at") or ""), str(secondary.get("updated_at") or ""))
        return primary

    def cleanup_jobs(self, user_id: int, today: date | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            job_rows = conn.execute("SELECT data FROM jobs WHERE user_id=?", (user_id,)).fetchall()
            cover_rows = conn.execute("SELECT job_id, data FROM cover_letters WHERE user_id=?", (user_id,)).fetchall()

        jobs = [self._loads(row["data"], {}) for row in job_rows]
        cover_letters = {str(row["job_id"]): self._loads(row["data"], {}) for row in cover_rows}
        cover_letter_ids = set(cover_letters.keys())

        deduped: dict[str, dict[str, Any]] = {}
        chosen_ids: dict[str, str] = {}
        cover_letter_targets: dict[str, dict[str, Any]] = {}
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            if is_job_expired(job, today=today):
                job["availability_status"] = "expired"
                if str(job.get("state") or "") not in {"applied_by_user", "rejected", "withdrawn", "offer", "accepted"}:
                    job["state"] = "closed"
            key = job_identity_key(job)
            merge_key = key
            merge_confidence = 1.0
            merge_reason = "matching canonical identity"
            if key not in deduped:
                candidates = []
                for existing_key, existing_job in deduped.items():
                    confidence, reason = identity_match(existing_job, job)
                    if confidence >= 0.90:
                        candidates.append((existing_key, confidence, reason))
                if len(candidates) == 1:
                    merge_key, merge_confidence, merge_reason = candidates[0]
            if merge_key in deduped:
                merged = self._merge_duplicate_jobs(deduped[merge_key], job, cover_letter_ids)
                merged["merge_confidence"] = merge_confidence
                merged["merge_reason"] = merge_reason
                deduped[merge_key] = merged
                chosen_ids[merge_key] = str(merged.get("id") or chosen_ids[merge_key])
            else:
                deduped[merge_key] = dict(job)
                chosen_ids[merge_key] = str(job.get("id"))

        kept_jobs = list(deduped.values())
        kept_ids = {str(job.get("id")) for job in kept_jobs if job.get("id")}

        for key, job in deduped.items():
            candidate_ids = [str(item.get("id")) for item in jobs if isinstance(item, dict) and job_identity_key(item) == key and item.get("id")]
            package = cover_letters.get(str(job.get("id"))) or next((cover_letters[job_id] for job_id in candidate_ids if job_id in cover_letters), None)
            if package:
                package["job_id"] = job["id"]
                cover_letter_targets[str(job["id"])] = package

        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM cover_letters WHERE user_id=?", (user_id,))
            for job in kept_jobs:
                job["state"] = str(job.get("state") or "matched")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO jobs(
                        id, user_id, state, fit_score, is_new, public_service, discovered_at, updated_at, data
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job["id"],
                        user_id,
                        job["state"],
                        int(job.get("fit_score") or 0),
                        int(bool(job.get("is_new", True))),
                        int(bool(job.get("public_service"))),
                        str(job.get("discovered_at") or ""),
                        str(job.get("updated_at") or ""),
                        self._dumps(job),
                    ),
                )
            for job_id, payload in cover_letter_targets.items():
                conn.execute(
                    "INSERT OR REPLACE INTO cover_letters(job_id, user_id, data) VALUES(?, ?, ?)",
                    (job_id, user_id, self._dumps(payload)),
                )
        return kept_jobs

    def reset_jobs(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM cover_letters WHERE user_id=?", (user_id,))

    def delete_job(self, user_id: int, job_id: str) -> bool:
        existing = self.get_job(user_id, job_id)
        if not existing:
            return False
        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE user_id=? AND id=?", (user_id, job_id))
            conn.execute("DELETE FROM cover_letters WHERE user_id=? AND job_id=?", (user_id, job_id))
        return True

    def setup(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resume_profiles (
                    user_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answers (
                    user_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    doc_type TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_facts (
                    fact_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    fact_type TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    user_verified INTEGER NOT NULL DEFAULT 0,
                    allowed_in_generation INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_index INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    fit_score INTEGER NOT NULL DEFAULT 0,
                    is_new INTEGER NOT NULL DEFAULT 1,
                    public_service INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT,
                    updated_at TEXT,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cover_letters (
                    job_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ui_state (
                    user_id INTEGER PRIMARY KEY,
                    menu_chat_id INTEGER,
                    menu_message_id INTEGER,
                    pending_input TEXT,
                    data TEXT
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    notification_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    UNIQUE(user_id, notification_key)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_user_state ON jobs(user_id, state);
                CREATE INDEX IF NOT EXISTS idx_sources_user ON sources(user_id);
                CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_facts_user ON evidence_facts(user_id);
                CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, sent_at);
                """
            )

    def _migration_complete(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='migration_version'").fetchone()
            return bool(row and row["value"] == "manual_apply_v1")

    def _set_migration_complete(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('migration_version', ?)",
                ("manual_apply_v1",),
            )

    def _load_json(self, key: str, default: Any) -> Any:
        path = self.paths[key]
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return default

    def _owner_id(self) -> int:
        owner = self._load_json("owner", {})
        if isinstance(owner, dict) and owner.get("owner_id"):
            return int(owner["owner_id"])
        profiles = self._load_json("profiles", {})
        if isinstance(profiles, dict) and profiles:
            return int(next(iter(profiles.keys())))
        return 0

    def migrate_from_json(self) -> None:
        if self._migration_complete():
            return
        owner_id = self._owner_id()
        profiles = self._load_json("profiles", {})
        answers = self._load_json("answers", {})
        resume_profiles = self._load_json("resume_profiles", {})
        docs_meta = self._load_json("docs", [])
        sources = sanitize_sources(self._load_json("sources", []))
        jobs_json = self._load_json("jobs", {})
        applications = self._load_json("applications", [])
        career_sites = self._load_json("career_sites", [])

        with self.connect() as conn:
            if isinstance(profiles, dict):
                for raw_user_id, payload in profiles.items():
                    user_id = int(raw_user_id)
                    conn.execute(
                        "INSERT OR REPLACE INTO profiles(user_id, data) VALUES(?, ?)",
                        (user_id, self._dumps(merge_profile(payload))),
                    )
            if owner_id and not self.get_profile(owner_id, create=False):
                conn.execute(
                    "INSERT OR REPLACE INTO profiles(user_id, data) VALUES(?, ?)",
                    (owner_id, self._dumps(default_profile())),
                )
            if isinstance(resume_profiles, dict):
                for raw_user_id, payload in resume_profiles.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO resume_profiles(user_id, data) VALUES(?, ?)",
                        (int(raw_user_id), self._dumps(payload)),
                    )
            if isinstance(answers, dict):
                for raw_user_id, payload in answers.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO answers(user_id, data) VALUES(?, ?)",
                        (int(raw_user_id), self._dumps(payload)),
                    )

            if isinstance(docs_meta, list):
                for item in docs_meta:
                    if not isinstance(item, dict):
                        continue
                    doc = {
                        "id": str(item.get("id") or item.get("filename") or "")[:32],
                        "original_name": str(item.get("original_name") or item.get("filename") or "document"),
                        "filename": str(item.get("filename") or item.get("original_name") or "document"),
                        "doc_type": str(item.get("doc_type") or "supporting_doc"),
                        "active": bool(item.get("active", True)),
                        "tags": list(item.get("tags") or []),
                        "parsed_text": str(item.get("parsed_text") or ""),
                        "added_at": str(item.get("added_at") or ""),
                    }
                    conn.execute(
                        "INSERT OR REPLACE INTO documents(id, user_id, doc_type, active, data) VALUES(?, ?, ?, ?, ?)",
                        (doc["id"], owner_id, doc["doc_type"], int(doc["active"]), self._dumps(doc)),
                    )

            if not sources:
                sources = default_sources()
            for index, source in enumerate(sources):
                conn.execute(
                    "INSERT OR REPLACE INTO sources(id, user_id, enabled, sort_index, data) VALUES(?, ?, ?, ?, ?)",
                    (source["id"], owner_id, int(source.get("enabled", True)), index, self._dumps(source)),
                )
            for site in career_sites if isinstance(career_sites, list) else []:
                if not isinstance(site, dict):
                    continue
                source = build_source(
                    str(site.get("name") or "Imported Source"),
                    str(site.get("url") or site.get("seed_url") or "").strip(),
                    "company_portal",
                )
                if not source["seed_url"]:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO sources(id, user_id, enabled, sort_index, data) VALUES(?, ?, ?, ?, ?)",
                    (source["id"], owner_id, 1, 9999, self._dumps(source)),
                )

        app_by_job = {
            str(item.get("job_id")): item
            for item in applications
            if isinstance(item, dict) and item.get("job_id")
        }
        if isinstance(jobs_json, dict):
            jobs_iterable = list(jobs_json.values())
        elif isinstance(jobs_json, list):
            jobs_iterable = jobs_json
        else:
            jobs_iterable = []

        profile = self.get_profile(owner_id)
        answers_for_owner = self.get_answers(owner_id)
        resume_profile = self.get_resume_profile(owner_id)
        candidate = build_candidate(profile, answers_for_owner, resume_profile)
        docs = self.list_documents(owner_id)
        resume_available = bool(profile.get("active_resume_path"))

        with self.connect() as conn:
            for raw in jobs_iterable:
                if not isinstance(raw, dict):
                    continue
                existing = {}
                app = app_by_job.get(str(raw.get("id")))
                if app:
                    state_map = {
                        "ready_to_apply": "ready_for_manual_apply",
                        "needs_answers": "matched",
                        "needs_login": "matched",
                        "review_required": "saved",
                        "manual_review": "matched",
                        "applied": "applied_by_user",
                        "failed": "matched",
                    }
                    existing = {
                        "state": state_map.get(str(app.get("state") or ""), "matched"),
                        "applied_at": app.get("submitted_at"),
                        "notes": [str(app.get("last_error") or "").strip()] if app.get("last_error") else [],
                    }
                    if app.get("application_requirements") and not raw.get("application_requirements"):
                        raw["application_requirements"] = app.get("application_requirements")
                    if app.get("public_service") and not raw.get("public_service"):
                        raw["public_service"] = True
                shaped = shape_job_record(owner_id, raw, candidate, docs, resume_available, existing)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO jobs(
                        id, user_id, state, fit_score, is_new, public_service, discovered_at, updated_at, data
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shaped["id"],
                        owner_id,
                        shaped["state"],
                        int(shaped.get("fit_score") or 0),
                        int(bool(shaped.get("is_new", True))),
                        int(bool(shaped.get("public_service"))),
                        str(shaped.get("discovered_at") or ""),
                        str(shaped.get("updated_at") or ""),
                        self._dumps(shaped),
                    ),
                )
        self._set_migration_complete()

    def get_profile(self, user_id: int, create: bool = True) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
            if row:
                return merge_profile(self._loads(row["data"], {}))
            if not create:
                return {}
            profile = default_profile()
            conn.execute("INSERT OR REPLACE INTO profiles(user_id, data) VALUES(?, ?)", (user_id, self._dumps(profile)))
            return profile

    def save_profile(self, user_id: int, profile: dict[str, Any]) -> dict[str, Any]:
        merged = merge_profile(profile)
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO profiles(user_id, data) VALUES(?, ?)", (user_id, self._dumps(merged)))
        return merged

    def get_resume_profile(self, user_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM resume_profiles WHERE user_id=?", (user_id,)).fetchone()
        return self._loads(row["data"], {}) if row else {}

    def save_resume_profile(self, user_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO resume_profiles(user_id, data) VALUES(?, ?)", (user_id, self._dumps(payload)))

    def get_answers(self, user_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM answers WHERE user_id=?", (user_id,)).fetchone()
            if row:
                return self._loads(row["data"], {})
        defaults = build_answer_bank_defaults(self.get_profile(user_id) | self.get_resume_profile(user_id))
        self.save_answers(user_id, defaults)
        return defaults

    def save_answers(self, user_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO answers(user_id, data) VALUES(?, ?)", (user_id, self._dumps(payload)))

    def replace_evidence_facts(self, user_id: int, facts: list[dict[str, Any]]) -> None:
        """Replace the derived ledger atomically; source documents remain untouched."""
        with self.connect() as conn:
            conn.execute("DELETE FROM evidence_facts WHERE user_id=?", (user_id,))
            for fact in facts:
                fact_id = str(fact.get("fact_id") or fact.get("id") or "").strip()
                if not fact_id:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO evidence_facts(
                        fact_id, user_id, fact_type, normalized_value, confidence,
                        user_verified, allowed_in_generation, data
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fact_id,
                        user_id,
                        str(fact.get("fact_type") or "unknown"),
                        str(fact.get("normalized_value") or fact.get("normalized_claim") or "").lower(),
                        str(fact.get("confidence") or "unknown"),
                        int(bool(fact.get("user_verified"))),
                        int(bool(fact.get("allowed_in_generation", True))),
                        self._dumps(fact),
                    ),
                )

    def get_evidence_facts(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM evidence_facts WHERE user_id=? ORDER BY fact_id", (user_id,)).fetchall()
        facts = [self._loads(row["data"], {}) for row in rows]
        if facts:
            return facts
        from core.evidence import build_evidence_ledger

        facts = build_evidence_ledger(self.get_profile(user_id), self.get_resume_profile(user_id), self.get_answers(user_id), self.list_documents(user_id))
        self.replace_evidence_facts(user_id, facts)
        return facts

    def save_confirmed_fact(self, user_id: int, fact_type: str, value: str) -> None:
        answers = self.get_answers(user_id)
        confirmed = dict(answers.get("confirmed_facts") or {})
        confirmed[str(fact_type).strip()] = str(value).strip()
        answers["confirmed_facts"] = confirmed
        answers[str(fact_type).strip()] = str(value).strip()
        self.save_answers(user_id, answers)
        from core.evidence import build_evidence_ledger

        self.replace_evidence_facts(user_id, build_evidence_ledger(self.get_profile(user_id), self.get_resume_profile(user_id), answers, self.list_documents(user_id)))

    def remove_confirmed_fact(self, user_id: int, fact_type: str) -> None:
        answers = self.get_answers(user_id)
        confirmed = dict(answers.get("confirmed_facts") or {})
        confirmed.pop(str(fact_type).strip(), None)
        answers["confirmed_facts"] = confirmed
        self.save_answers(user_id, answers)
        from core.evidence import build_evidence_ledger

        self.replace_evidence_facts(user_id, build_evidence_ledger(self.get_profile(user_id), self.get_resume_profile(user_id), answers, self.list_documents(user_id)))

    def list_documents(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM documents WHERE user_id=? ORDER BY rowid DESC", (user_id,)).fetchall()
        return [self._loads(row["data"], {}) for row in rows]

    def save_document(self, user_id: int, document: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents(id, user_id, doc_type, active, data) VALUES(?, ?, ?, ?, ?)",
                (document["id"], user_id, document.get("doc_type", "supporting_doc"), int(bool(document.get("active", True))), self._dumps(document)),
            )

    def delete_document(self, user_id: int, document_id: str) -> dict[str, Any] | None:
        document = next((item for item in self.list_documents(user_id) if item.get("id") == document_id), None)
        if not document:
            return None
        with self.connect() as conn:
            conn.execute("DELETE FROM documents WHERE id=? AND user_id=?", (document_id, user_id))
        return document

    def save_feedback(self, user_id: int, job_id: str, label: str, reason: str = "") -> dict[str, Any]:
        if label not in {"relevant", "not_for_me"}:
            raise ValueError("Unsupported feedback label")
        payload = {
            "job_id": str(job_id),
            "label": label,
            "reason": str(reason or "").strip(),
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO feedback(user_id, job_id, label, reason, created_at, data) VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, str(job_id), label, payload["reason"], payload["created_at"], self._dumps(payload)),
            )
            payload["id"] = int(cursor.lastrowid)
        return payload

    def list_feedback(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, data FROM feedback WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        events = []
        for row in rows:
            event = self._loads(row["data"], {})
            event["id"] = int(row["id"])
            events.append(event)
        return events

    def delete_feedback(self, user_id: int, feedback_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM feedback WHERE id=? AND user_id=?", (int(feedback_id), user_id))
        return cursor.rowcount == 1

    def clear_feedback(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM feedback WHERE user_id=?", (user_id,))

    def record_notification(self, user_id: int, notification_key: str, category: str, data: dict[str, Any] | None = None) -> bool:
        sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO notifications(user_id, notification_key, category, sent_at, data) VALUES(?, ?, ?, ?, ?)",
                (user_id, notification_key, category, sent_at, self._dumps(data or {})),
            )
        return cursor.rowcount == 1

    def notification_sent(self, user_id: int, notification_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM notifications WHERE user_id=? AND notification_key=?", (user_id, notification_key)).fetchone()
        return row is not None

    def start_scan_run(self, user_id: int, scheduled_only: bool = False) -> str:
        import uuid

        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"id": run_id, "user_id": user_id, "scheduled_only": scheduled_only, "started_at": started}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO scan_runs(id, user_id, started_at, status, data) VALUES(?, ?, ?, ?, ?)",
                (run_id, user_id, started, "running", self._dumps(payload)),
            )
        return run_id

    def finish_scan_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM scan_runs WHERE id=?", (run_id,)).fetchone()
            payload = self._loads(row["data"], {}) if row else {"id": run_id}
            payload.update(summary)
            payload["finished_at"] = finished
            payload["status"] = status
            conn.execute("UPDATE scan_runs SET finished_at=?, status=?, data=? WHERE id=?", (finished, status, self._dumps(payload), run_id))

    def list_scan_runs(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM scan_runs WHERE user_id=? ORDER BY started_at DESC LIMIT ?", (user_id, max(1, min(limit, 100)))).fetchall()
        return [self._loads(row["data"], {}) for row in rows]

    def list_sources(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM sources WHERE user_id=? ORDER BY sort_index ASC, rowid ASC", (user_id,)).fetchall()
        if rows:
            sources = sanitize_sources([self._loads(row["data"], {}) for row in rows])
            stored = [self._loads(row["data"], {}) for row in rows]
            if sources != stored:
                with self.connect() as conn:
                    conn.execute("DELETE FROM sources WHERE user_id=?", (user_id,))
                for index, source in enumerate(sources):
                    self.save_source(user_id, source, index=index)
            return sources
        defaults = default_sources()
        for index, source in enumerate(defaults):
            self.save_source(user_id, source, index=index)
        return defaults

    def save_source(self, user_id: int, source: dict[str, Any], index: int | None = None) -> None:
        if index is None:
            sources = self.list_sources(user_id)
            index = next((i for i, item in enumerate(sources) if item.get("id") == source.get("id")), len(sources))
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sources(id, user_id, enabled, sort_index, data) VALUES(?, ?, ?, ?, ?)",
                (source["id"], user_id, int(bool(source.get("enabled", True))), index, self._dumps(source)),
            )

    def get_source(self, user_id: int, source_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_sources(user_id) if item.get("id") == source_id), None)

    def add_custom_source(self, user_id: int, name: str, url: str, kind: str = "company_portal") -> dict[str, Any]:
        clean_name, clean_url = validate_source_input(name, url)
        source = build_source(clean_name, clean_url, kind)
        self.save_source(user_id, source)
        return source

    def list_jobs(
        self,
        user_id: int,
        states: set[str] | None = None,
        include_hidden: bool = False,
        today: date | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.cleanup_jobs(user_id, today=today)
        if states is not None:
            jobs = [job for job in jobs if str(job.get("state")) in states]
        if not include_hidden:
            jobs = [job for job in jobs if str(job.get("state")) not in {"skipped", "closed"}]
        return jobs

    def get_job(self, user_id: int, job_id: str) -> dict[str, Any] | None:
        return next((job for job in self.cleanup_jobs(user_id) if str(job.get("id")) == str(job_id)), None)

    def save_job(self, user_id: int, job: dict[str, Any]) -> None:
        job["state"] = str(job.get("state") or "matched")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO jobs(
                    id, user_id, state, fit_score, is_new, public_service, discovered_at, updated_at, data
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    user_id,
                    job["state"],
                    int(job.get("fit_score") or 0),
                    int(bool(job.get("is_new", True))),
                    int(bool(job.get("public_service"))),
                    str(job.get("discovered_at") or ""),
                    str(job.get("updated_at") or ""),
                    self._dumps(job),
                ),
            )

    def recalculate_job_readiness(self, user_id: int, job_id: str) -> dict[str, Any] | None:
        """Re-evaluate one job after evidence or generated material changes."""
        job = self.get_job(user_id, job_id)
        if not job:
            return None
        from core.profile import build_candidate
        from services.documents import resume_path

        profile = self.get_profile(user_id)
        candidate = build_candidate(profile, self.get_answers(user_id), self.get_resume_profile(user_id))
        refreshed = shape_job_record(
            user_id,
            job,
            candidate,
            self.list_documents(user_id),
            resume_path(self.base_dir, profile).exists(),
            existing=job,
        )
        refreshed["cover_letter_ready"] = bool(job.get("cover_letter_ready"))
        refreshed["generated_answers_ready"] = bool(job.get("generated_answers_ready"))
        refreshed["state"] = job.get("state") or refreshed.get("state")
        refreshed["state_history"] = list(job.get("state_history") or [])
        self.save_job(user_id, refreshed)
        return refreshed

    def save_jobs(self, user_id: int, jobs: list[dict[str, Any]]) -> None:
        for job in jobs:
            self.save_job(user_id, job)

    def update_job_state(self, user_id: int, job_id: str, state: str, **updates: Any) -> dict[str, Any] | None:
        job = self.get_job(user_id, job_id)
        if not job:
            return None
        allowed_states = {
            "discovered", "matched", "saved", "needs_documents", "preparing",
            "ready_for_manual_apply", "ready_to_apply", "applied_by_user",
            "interview", "offer", "accepted", "rejected", "withdrawn", "skipped", "closed",
        }
        if state not in allowed_states:
            raise ValueError(f"Unsupported application state: {state}")
        previous_state = str(job.get("state") or "matched")
        history = list(job.get("state_history") or [])
        if previous_state != state:
            history.append({
                "from": previous_state,
                "to": state,
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            })
        if state == "applied_by_user":
            updates.setdefault(
                "application_artifact_snapshot",
                {
                    "application_url": job.get("apply_url") or job.get("listing_url") or "",
                    "resume_path": self.get_profile(user_id).get("active_resume_path", ""),
                    "cover_letter_generated_at": (self.get_cover_letter(user_id, job_id) or {}).get("generated_at", ""),
                    "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                },
            )
        job["state"] = state
        job["is_new"] = False
        job["state_history"] = history[-50:]
        job.update(updates)
        self.save_job(user_id, job)
        return job

    def update_job_fields(self, user_id: int, job_id: str, **updates: Any) -> dict[str, Any] | None:
        """Persist user-entered application metadata without changing the job state."""
        job = self.get_job(user_id, job_id)
        if not job:
            return None
        job.update(updates)
        self.save_job(user_id, job)
        return job

    def mark_job_viewed(self, user_id: int, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(user_id, job_id)
        if not job:
            return None
        if job.get("is_new"):
            job["is_new"] = False
            self.save_job(user_id, job)
        return job

    def save_cover_letter(self, user_id: int, job_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO cover_letters(job_id, user_id, data) VALUES(?, ?, ?)", (job_id, user_id, self._dumps(payload)))
        job = self.get_job(user_id, job_id)
        if job:
            job["cover_letter_ready"] = True
            job["generated_answers_ready"] = True
            if job.get("state") == "matched" and not job.get("missing_docs"):
                job["state"] = "ready_for_manual_apply"
            self.save_job(user_id, job)
            self.recalculate_job_readiness(user_id, job_id)

    def get_cover_letter(self, user_id: int, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM cover_letters WHERE job_id=? AND user_id=?", (job_id, user_id)).fetchone()
        return self._loads(row["data"], {}) if row else None

    def list_cover_letters(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT data FROM cover_letters WHERE user_id=?", (user_id,)).fetchall()
        return [self._loads(row["data"], {}) for row in rows]

    def get_ui_state(self, user_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT menu_chat_id, menu_message_id, pending_input, data FROM ui_state WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {}
        return {
            "menu_chat_id": row["menu_chat_id"],
            "menu_message_id": row["menu_message_id"],
            "pending_input": self._loads(row["pending_input"], None),
            **self._loads(row["data"], {}),
        }

    def save_ui_state(self, user_id: int, **payload: Any) -> None:
        current = self.get_ui_state(user_id)
        pending_input = payload.pop("pending_input", current.get("pending_input"))
        menu_chat_id = payload.pop("menu_chat_id", current.get("menu_chat_id"))
        menu_message_id = payload.pop("menu_message_id", current.get("menu_message_id"))
        merged_data = {**{k: v for k, v in current.items() if k not in {"menu_chat_id", "menu_message_id", "pending_input"}}, **payload}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ui_state(user_id, menu_chat_id, menu_message_id, pending_input, data)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    menu_chat_id,
                    menu_message_id,
                    self._dumps(pending_input) if pending_input is not None else None,
                    self._dumps(merged_data),
                ),
            )

    def clear_pending_input(self, user_id: int) -> None:
        state = self.get_ui_state(user_id)
        self.save_ui_state(user_id, menu_chat_id=state.get("menu_chat_id"), menu_message_id=state.get("menu_message_id"), pending_input=None)

    def source_stats(self, user_id: int) -> dict[str, int]:
        sources = self.list_sources(user_id)
        return {
            "total": len(sources),
            "enabled": sum(1 for item in sources if item.get("enabled", True)),
            "public_sector": sum(1 for item in sources if item.get("enabled", True) and item.get("kind") == "public_jobs_portal"),
            "scheduled": sum(1 for item in sources if item.get("enabled", True) and item.get("schedule_enabled", True)),
        }

    def job_counts(self, user_id: int, today: date | None = None) -> dict[str, int]:
        jobs = self.list_jobs(user_id, include_hidden=True, today=today)
        counts = {
            "total": len(jobs),
            "new": 0,
            "matched": 0,
            "saved": 0,
            "needs_documents": 0,
            "ready_for_manual_apply": 0,
            "applied_by_user": 0,
            "skipped": 0,
            "public_service": 0,
            "eligible": 0,
            "strong_matches": 0,
            "actionable": 0,
            "ready_to_apply": 0,
            "closing_soon": 0,
            "stale_unverified": 0,
        }
        for job in jobs:
            state = str(job.get("state") or "matched")
            counts[state] = counts.get(state, 0) + 1
            if job.get("is_new") and state not in {"skipped", "closed"}:
                counts["new"] += 1
            if job.get("public_service"):
                counts["public_service"] += 1
            if str(job.get("eligibility_status") or "") == "eligible":
                counts["eligible"] += 1
            if (
                bool(job.get("strong_match"))
                and str(job.get("eligibility_status") or "") == "eligible"
                and str(job.get("freshness_status") or "") not in {"expired", "stale_unverified"}
                and str(job.get("trust_status") or "") != "high_risk"
            ):
                counts["strong_matches"] += 1
            if bool(job.get("actionable")):
                counts["actionable"] += 1
                if state not in {"applied_by_user", "accepted", "rejected", "withdrawn", "skipped", "closed"}:
                    counts["ready_to_apply"] += 1
            if str(job.get("freshness_status") or "") == "closing_soon":
                counts["closing_soon"] += 1
            if str(job.get("freshness_status") or "") == "stale_unverified":
                counts["stale_unverified"] += 1
        return counts

    def list_user_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT user_id FROM profiles ORDER BY user_id").fetchall()
        return [int(row["user_id"]) for row in rows]
