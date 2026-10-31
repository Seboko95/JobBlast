"""Aggregator site adapters."""

from __future__ import annotations

from jobblast_ats import (
    _search_query_variants,
    normalize_job,
    search_careers24_jobs,
    score_job_fit,
    search_indeed_jobs,
    search_pnet_jobs,
)


async def scan(source: dict, candidate: dict) -> list[dict]:
    queries = candidate.get("search_terms", []) or [candidate.get("search_keywords", "pharmaceutical sales representative")]
    location = candidate.get("location", "Gauteng")
    seed_url = str(source.get("seed_url", "")).lower()
    name = str(source.get("name", "")).lower()
    if "indeed" in name or "indeed" in seed_url:
        jobs = await _search_query_variants(search_indeed_jobs, queries[:4], location)
    elif "careers24" in name or "careers24" in seed_url:
        jobs = await _search_query_variants(search_careers24_jobs, queries[:4], location)
    else:
        jobs = await _search_query_variants(search_pnet_jobs, queries[:4], location)
    threshold = int(candidate.get("minimum_match_threshold") or 18)
    normalized = [
        normalize_job(job, source, candidate)
        for job in jobs
        if score_job_fit(job.get("title", ""), job.get("description", ""), candidate) >= threshold
    ]
    return [job for job in normalized if int(job.get("fit_score") or 0) >= threshold]
