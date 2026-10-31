"""Public-sector and government job adapters."""

from __future__ import annotations

from jobblast_ats import normalize_job, scan_gauteng_jobs, score_job_fit


async def scan(source: dict, candidate: dict) -> list[dict]:
    jobs = await scan_gauteng_jobs(source, candidate)
    threshold = max(10, int(candidate.get("minimum_match_threshold") or 18) - 8)
    normalized = [
        normalize_job(job, source, candidate)
        for job in jobs
        if score_job_fit(job.get("title", ""), job.get("description", ""), candidate) >= threshold
    ]
    return [job for job in normalized if int(job.get("fit_score") or 0) >= threshold]
