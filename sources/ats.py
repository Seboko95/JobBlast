"""ATS-backed and generic company portal adapters."""

from __future__ import annotations

from jobblast_ats import (
    infer_portal_type,
    normalize_job,
    scan_ashby,
    scan_generic_company_site,
    scan_greenhouse,
    scan_icims,
    scan_lever,
    scan_simplify,
    scan_smartrecruiters,
    scan_successfactors,
    scan_workday,
    score_job_fit,
)


async def scan(source: dict, candidate: dict) -> list[dict]:
    portal_type = source.get("portal_type") or infer_portal_type(source["seed_url"])
    if portal_type == "greenhouse":
        jobs = await scan_greenhouse(source)
    elif portal_type == "lever":
        jobs = await scan_lever(source)
    elif portal_type == "smartrecruiters":
        jobs = await scan_smartrecruiters(source)
    elif portal_type == "workday":
        jobs = await scan_workday(source)
    elif portal_type == "ashby":
        jobs = await scan_ashby(source)
    elif portal_type == "icims":
        jobs = await scan_icims(source)
    elif portal_type == "successfactors":
        jobs = await scan_successfactors(source)
    elif portal_type == "simplify":
        jobs = await scan_simplify(source)
    else:
        jobs = await scan_generic_company_site(source)
    threshold = int(candidate.get("minimum_match_threshold") or 18)
    normalized = [
        normalize_job(job, source, candidate)
        for job in jobs
        if score_job_fit(job.get("title", ""), job.get("description", ""), candidate) >= threshold
    ]
    return [job for job in normalized if int(job.get("fit_score") or 0) >= threshold]
