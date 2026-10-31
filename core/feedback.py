"""Implicit ranking feedback derived from saved, applied, and skipped jobs."""

from __future__ import annotations

from typing import Any


TRACKED_TERMS = [
    "medical representative",
    "pharmaceutical sales representative",
    "sales representative",
    "product specialist",
    "territory manager",
    "business development",
    "pharmacist assistant",
    "pharmaceutical",
    "pharma",
    "healthcare",
    "otc",
    "consumer health",
    "fmcg",
    "pharmacy",
    "hospital",
]

POSITIVE_STATES = {
    "saved": 2,
    "ready_for_manual_apply": 2,
    "applied_by_user": 3,
}

NEGATIVE_STATES = {
    "skipped": 2,
    "closed": 1,
}

NEGATIVE_FEEDBACK_REASONS = {
    "wrong_role": "Wrong role",
    "too_senior": "Too senior",
    "too_junior": "Too junior",
    "wrong_location": "Wrong location",
    "salary_low": "Salary too low",
    "wrong_industry": "Wrong industry",
    "company_not_wanted": "Not interested in company",
    "missing_qualification": "Missing qualification",
    "duplicate": "Duplicate",
    "expired": "Expired",
    "suspicious": "Suspicious",
    "already_seen": "Already seen",
    "other": "Other",
}

NON_LEARNING_REASONS = {"duplicate", "expired", "suspicious", "already_seen"}


def _haystack(job: dict[str, Any]) -> str:
    return " ".join(
        [
            str(job.get("title") or ""),
            str(job.get("description") or ""),
            str(job.get("company") or ""),
            str(job.get("location") or ""),
        ]
    ).lower()


def _top_terms(counts: dict[str, int], limit: int = 8) -> list[str]:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [term for term, _ in ranked[:limit]]


def build_feedback_profile(jobs: list[dict[str, Any]], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    positive_terms: dict[str, int] = {}
    negative_terms: dict[str, int] = {}
    positive_portals: dict[str, int] = {}
    negative_portals: dict[str, int] = {}
    positive_sources: dict[str, int] = {}
    negative_sources: dict[str, int] = {}

    for job in jobs:
        state = str(job.get("state") or "matched")
        weight = POSITIVE_STATES.get(state, 0) - NEGATIVE_STATES.get(state, 0)
        if weight == 0:
            continue
        target_terms = positive_terms if weight > 0 else negative_terms
        target_portals = positive_portals if weight > 0 else negative_portals
        target_sources = positive_sources if weight > 0 else negative_sources
        magnitude = abs(weight)
        haystack = _haystack(job)
        for term in TRACKED_TERMS:
            if term in haystack:
                target_terms[term] = target_terms.get(term, 0) + magnitude
        portal = str(job.get("portal_type") or "").strip().lower()
        if portal:
            target_portals[portal] = target_portals.get(portal, 0) + magnitude
        source_name = str(job.get("source_name") or "").strip().lower()
        if source_name:
            target_sources[source_name] = target_sources.get(source_name, 0) + magnitude

    by_id = {str(job.get("id")): job for job in jobs}
    for event in events or []:
        job = by_id.get(str(event.get("job_id")))
        if not job:
            continue
        if str(event.get("reason") or "").strip().lower() in NON_LEARNING_REASONS:
            continue
        weight = 2 if event.get("label") == "relevant" else -2
        target_terms = positive_terms if weight > 0 else negative_terms
        target_portals = positive_portals if weight > 0 else negative_portals
        target_sources = positive_sources if weight > 0 else negative_sources
        haystack = _haystack(job)
        for term in TRACKED_TERMS:
            if term in haystack:
                target_terms[term] = target_terms.get(term, 0) + abs(weight)
        portal = str(job.get("portal_type") or "").strip().lower()
        if portal:
            target_portals[portal] = target_portals.get(portal, 0) + abs(weight)
        source_name = str(job.get("source_name") or "").strip().lower()
        if source_name:
            target_sources[source_name] = target_sources.get(source_name, 0) + abs(weight)

    return {
        "positive_terms": _top_terms(positive_terms),
        "negative_terms": _top_terms(negative_terms),
        "positive_portals": _top_terms(positive_portals, limit=4),
        "negative_portals": _top_terms(negative_portals, limit=4),
        "positive_sources": _top_terms(positive_sources, limit=4),
        "negative_sources": _top_terms(negative_sources, limit=4),
    }


def feedback_score_adjustment(
    title: str,
    description: str,
    profile: dict[str, Any],
    job_meta: dict[str, Any] | None = None,
) -> int:
    feedback = dict(profile.get("feedback_profile") or {})
    if not feedback:
        return 0
    haystack = f"{title} {description}".lower()
    adjustment = 0

    for term in feedback.get("positive_terms", []):
        if term in haystack:
            adjustment += 4
    for term in feedback.get("negative_terms", []):
        if term in haystack:
            adjustment -= 5

    job_meta = job_meta or {}
    portal = str(job_meta.get("portal_type") or "").strip().lower()
    source_name = str(job_meta.get("source_name") or "").strip().lower()
    if portal and portal in feedback.get("positive_portals", []):
        adjustment += 3
    if portal and portal in feedback.get("negative_portals", []):
        adjustment -= 4
    if source_name and source_name in feedback.get("positive_sources", []):
        adjustment += 2
    if source_name and source_name in feedback.get("negative_sources", []):
        adjustment -= 3

    return max(-18, min(adjustment, 18))


def feedback_match_reason(job: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    feedback = dict(candidate.get("feedback_profile") or {})
    haystack = _haystack(job)
    for term in feedback.get("positive_terms", []):
        if term in haystack:
            return f"Similar to roles you previously saved or applied: {term}"
    portal = str(job.get("portal_type") or "").strip().lower()
    if portal and portal in feedback.get("positive_portals", []):
        return f"Matches a portal type where you previously kept stronger jobs: {portal}"
    return None
