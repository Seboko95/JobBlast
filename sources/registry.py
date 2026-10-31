"""Registry that routes sources to the appropriate adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
from time import perf_counter
from datetime import datetime, timezone

from sources import aggregators, ats, linkedin, public_sector
from core.jobs import build_job_id
from jobblast_ats import classify_source_failure, classify_source_issue
from services.source_health import cooldown_active, source_health_status, update_source_health

logger = logging.getLogger(__name__)


async def scan_source(source: dict, candidate: dict) -> list[dict]:
    kind = source.get("kind")
    if kind == "linkedin_search":
        return await linkedin.scan(source, candidate)
    if kind == "aggregator_search":
        return await aggregators.scan(source, candidate)
    if kind == "public_jobs_portal":
        return await public_sector.scan(source, candidate)
    return await ats.scan(source, candidate)


async def scan_enabled_sources(
    sources: list[dict],
    candidate: dict,
    existing_jobs: dict[str, dict],
    scheduled_only: bool = False,
    progress_callback=None,
) -> tuple[list[dict], list[dict]]:
    discovered: list[dict] = []
    source_results: list[dict] = []
    selected = [
        source
        for source in sources
        if source.get("enabled", True) and (not scheduled_only or source.get("schedule_enabled", True))
    ]
    semaphore = asyncio.Semaphore(5)

    async def scan_one(source: dict) -> tuple[dict, list[dict], str, str, str]:
        async with semaphore:
            if cooldown_active(source):
                return source, [], "", "cooldown", "cooldown"
            started = perf_counter()
            for attempt in range(3):
                try:
                    jobs = await scan_source(source, candidate)
                    source["last_duration_ms"] = int((perf_counter() - started) * 1000)
                    return source, jobs, "", "", ""
                except Exception as exc:
                    message = str(exc)
                    failure_category = classify_source_failure(message)
                    if failure_category not in {"timeout", "network", "http_429"} or attempt == 2:
                        source["last_duration_ms"] = int((perf_counter() - started) * 1000)
                        return source, [], message, classify_source_issue(message), failure_category
                    await asyncio.sleep(2 ** attempt)
            return source, [], "source retry loop ended unexpectedly", "error", "unknown"

    tasks = [asyncio.create_task(scan_one(source)) for source in selected]
    known_ids = set(existing_jobs)
    for finished in asyncio.as_completed(tasks):
        source, jobs, error, issue_level, failure_category = await finished
        new_count = 0
        for job in jobs:
            canonical_id = build_job_id(job)
            job["canonical_id"] = canonical_id
            if canonical_id not in known_ids:
                known_ids.add(canonical_id)
                new_count += 1
            discovered.append(job)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        source["last_checked"] = jobs[0].get("discovered_at") if jobs else now
        if failure_category == "cooldown":
            source["last_result"] = "cooldown: temporarily suspended after repeated failures"
            source_results.append(
                {
                    "source_id": source["id"],
                    "source_name": source.get("name", source["id"]),
                    "matched": 0,
                    "new": 0,
                    "error": "",
                    "warning": source["last_result"],
                    "failure_category": "cooldown",
                    "health_status": "cooldown",
                    "last_duration_ms": 0,
                }
            )
            if progress_callback is not None:
                progress_result = progress_callback(source_results[-1], len(source_results), len(selected))
                if inspect.isawaitable(progress_result):
                    await progress_result
            continue
        source["last_error"] = error if issue_level == "error" else ""
        source["last_failure_category"] = failure_category if error else ""
        if not error:
            source["last_result"] = f"{new_count} new / {len(jobs)} matched"
        elif issue_level == "warning":
            source["last_result"] = f"warning: {error[:120]}"
            logger.warning("Source warning [%s]: %s", source.get("name", source.get("id")), error)
        else:
            source["last_result"] = f"error: {error[:120]}"
            logger.error("Source error [%s]: %s", source.get("name", source.get("id")), error)
        update_source_health(
            source,
            matched=len(jobs),
            new=new_count,
            error=error if issue_level == "error" else "",
            warning=error if issue_level == "warning" else "",
            failure_category=failure_category if error else "",
            duration_ms=int(source.get("last_duration_ms") or 0),
        )
        source_results.append(
            {
                "source_id": source["id"],
                "source_name": source.get("name", source["id"]),
                "matched": len(jobs),
                "new": new_count,
                "error": error if issue_level == "error" else "",
                "warning": error if issue_level == "warning" else "",
                "failure_category": failure_category if error else "",
                "health_status": source_health_status(source),
                "last_duration_ms": int(source.get("last_duration_ms") or 0),
            }
        )
        if progress_callback is not None:
            progress_result = progress_callback(source_results[-1], len(source_results), len(selected))
            if inspect.isawaitable(progress_result):
                await progress_result
    discovered.sort(key=lambda item: (item.get("recommendation_score") or item.get("fit_score", 0), item.get("discovered_at", "")), reverse=True)
    return discovered, source_results
