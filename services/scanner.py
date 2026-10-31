"""Scanning orchestration that stores manual-apply job records."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from core.feedback import build_feedback_profile
from core.jobs import build_preparation_plan, job_identity_key, shape_job_record
from core.intelligence import changed_fields, classify_actionability, freshness_status
from core.profile import build_candidate
from services.documents import resume_path
from services.official_verification import apply_verification, verify_priority_jobs
from sources.registry import scan_enabled_sources
from storage.repository import Repository


async def run_scan(repo: Repository, user_id: int, scheduled_only: bool = False, progress_callback=None) -> dict:
    scan_started = perf_counter()
    run_id = repo.start_scan_run(user_id, scheduled_only=scheduled_only)
    profile = repo.get_profile(user_id)
    answers = repo.get_answers(user_id)
    resume_profile = repo.get_resume_profile(user_id)
    candidate = build_candidate(profile, answers, resume_profile)
    candidate["minimum_match_threshold"] = profile.get("minimum_match_threshold", 18)
    candidate["feedback_profile"] = build_feedback_profile(
        repo.list_jobs(user_id, include_hidden=True),
        repo.list_feedback(user_id),
    )
    sources = repo.list_sources(user_id)
    existing_jobs = {job["id"]: job for job in repo.list_jobs(user_id, include_hidden=True)}
    existing_by_canonical = {
        job_identity_key(job): job
        for job in existing_jobs.values()
        if job_identity_key(job)
    }
    try:
        source_started = perf_counter()
        discovered, source_results = await scan_enabled_sources(
            sources,
            candidate,
            existing_jobs,
            scheduled_only=scheduled_only,
            progress_callback=progress_callback,
        )
        source_discovery_ms = int((perf_counter() - source_started) * 1000)
    except Exception as exc:
        repo.finish_scan_run(run_id, "failed", {"error": str(exc)})
        raise
    try:
        reconcile_started = perf_counter()
        docs = repo.list_documents(user_id)
        resume_available = resume_path(repo.base_dir, profile).exists()
        saved_jobs: list[dict] = []
        new_jobs: list[dict] = []
        reconciled_jobs: list[dict] = []
        saved_by_canonical: dict[str, dict] = {}
        for job in discovered:
            canonical_key = job_identity_key(job)
            existing = (
                existing_jobs.get(str(job.get("canonical_id") or ""))
                or existing_by_canonical.get(canonical_key)
                or saved_by_canonical.get(canonical_key)
            )
            shaped = shape_job_record(user_id, job, candidate, docs, resume_available, existing=existing)
            shaped["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            shaped["revision_changes"] = changed_fields(existing, shaped) if existing else []
            shaped["revision"] = int((existing or {}).get("revision") or 0) + (1 if shaped["revision_changes"] else 0)
            shaped["last_seen_at"] = shaped["updated_at"]
            shaped["availability_status"] = "active"
            if shaped.get("verification_status") == "source_verified":
                shaped["last_verified"] = shaped["updated_at"]
            shaped["freshness_status"] = freshness_status(shaped)
            shaped["match_snapshot"]["freshness_status"] = shaped["freshness_status"]
            shaped["match_snapshot"].update(classify_actionability(shaped["match_snapshot"], shaped, shaped.get("missing_docs") or []))
            shaped["preparation_plan"] = build_preparation_plan(
                shaped,
                shaped["match_snapshot"],
                list(shaped.get("missing_docs") or []),
                bool(shaped.get("cover_letter_ready")),
            )
            shaped.setdefault("application_readiness", {})["preparation_plan"] = shaped["preparation_plan"]
            shaped["strong_match"] = bool(shaped.get("match_snapshot", {}).get("strong_match") and shaped.get("eligibility_status") == "eligible")
            shaped["actionable"] = bool(shaped.get("match_snapshot", {}).get("actionable"))
            shaped["actionability_state"] = shaped["match_snapshot"].get("actionability_state", "not_actionable")
            shaped["actionability_blockers"] = list(shaped["match_snapshot"].get("actionability_blockers") or [])
            shaped["actionability_preparation"] = list(shaped["match_snapshot"].get("actionability_preparation") or [])
            shaped["actionability_reason"] = shaped["match_snapshot"].get("actionability_reason", "")
            shaped["is_new"] = not bool(existing)
            saved_jobs.append(shaped)
            saved_by_canonical[canonical_key] = shaped
            (reconciled_jobs if existing else new_jobs).append(shaped)
        reconcile_ranking_ms = int((perf_counter() - reconcile_started) * 1000)
        persistence_started = perf_counter()
        repo.save_jobs(user_id, saved_jobs)
        persistence_ms = int((perf_counter() - persistence_started) * 1000)
        verification_started = perf_counter()
        try:
            verification = await verify_priority_jobs(repo.list_jobs(user_id, include_hidden=False))
        except Exception as exc:
            verification = {
                "attempted": 0,
                "verified": 0,
                "unverified": 0,
                "conflicted": 0,
                "degraded": len(saved_jobs),
                "results": [],
                "error": str(exc)[:240],
            }
        verification_ms = int((perf_counter() - verification_started) * 1000)
        checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        by_id = {str(item.get("job_id")): item for item in verification.get("results") or []}
        current_jobs = repo.list_jobs(user_id, include_hidden=False)
        updated_verification_jobs = []
        for job in current_jobs:
            result_for_job = by_id.get(str(job.get("id")))
            if result_for_job:
                updated_verification_jobs.append(apply_verification(job, result_for_job, checked_at))
        if updated_verification_jobs:
            persistence_started = perf_counter()
            repo.save_jobs(user_id, updated_verification_jobs)
            persistence_ms += int((perf_counter() - persistence_started) * 1000)
            updated_by_id = {str(job.get("id")): job for job in updated_verification_jobs}
            saved_jobs = [updated_by_id.get(str(job.get("id")), job) for job in saved_jobs]
        persistence_started = perf_counter()
        for source in sources:
            repo.save_source(user_id, source)
        persistence_ms += int((perf_counter() - persistence_started) * 1000)
        timings = {
            "source_discovery_ms": source_discovery_ms,
            "canonical_reconciliation_eligibility_ranking_ms": reconcile_ranking_ms,
            "verification_ms": verification_ms,
            "persistence_ms": persistence_ms,
            "notifications_ms": 0,
            "total_ms": int((perf_counter() - scan_started) * 1000),
        }
        result = {
            "run_id": run_id,
            "new_jobs": new_jobs,
            "reconciled_jobs": reconciled_jobs,
            "persisted_jobs": saved_jobs,
            "saved_jobs": saved_jobs,
            "source_results": source_results,
            "public_matches": sum(1 for job in saved_jobs if job.get("public_service")),
            "new_public_matches": sum(1 for job in new_jobs if job.get("public_service")),
            "eligible_count": sum(1 for job in saved_jobs if job.get("eligibility_status") == "eligible"),
            "strong_match_count": sum(1 for job in saved_jobs if job.get("strong_match")),
            "errors": [item for item in source_results if item.get("error")],
            "warnings": [item for item in source_results if item.get("warning")],
            "verification": verification,
            "timings": timings,
        }
        repo.finish_scan_run(
            run_id,
            "completed",
            {
                "new_count": len(new_jobs),
                "reconciled_count": len(reconciled_jobs),
                "saved_count": len(saved_jobs),
                "eligible_count": result["eligible_count"],
                "strong_match_count": result["strong_match_count"],
                "source_results": source_results,
                "verification": verification,
                "timings": timings,
            },
        )
        return result
    except Exception as exc:
        repo.finish_scan_run(run_id, "failed", {"error": str(exc), "source_results": source_results})
        raise
