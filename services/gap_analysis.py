"""Gap analysis and CV tailoring helpers for shortlisted jobs."""

from __future__ import annotations

import re
from typing import Any


ROLE_HINTS = [
    "medical representative",
    "pharmaceutical sales representative",
    "sales representative",
    "product specialist",
    "territory manager",
    "business development",
    "pharmacist assistant",
]

DOMAIN_HINTS = [
    "pharmaceutical",
    "pharma",
    "healthcare",
    "otc",
    "fmcg",
    "consumer health",
    "pharmacy",
    "hospital",
    "retail pharmacy",
]

SKILL_HINTS = [
    "sapc",
    "stock control",
    "patient counselling",
    "customer service",
    "negotiation",
    "selling",
    "cold chain",
    "dispensing",
    "driving",
    "territory",
]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _haystack(job: dict[str, Any]) -> str:
    return " ".join(
        [
            _clean(job.get("title")),
            _clean(job.get("description")),
            _clean(job.get("company")),
            _clean(job.get("location")),
        ]
    ).lower()


def _uniq(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = _clean(item)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _matched_terms(job_text: str, values: list[str]) -> list[str]:
    matches: list[str] = []
    for value in values:
        cleaned = _clean(value)
        if cleaned and cleaned.lower() in job_text:
            matches.append(cleaned)
    return _uniq(matches)


def _job_keywords(job_text: str) -> list[str]:
    matches = [term for term in ROLE_HINTS + DOMAIN_HINTS + SKILL_HINTS if term in job_text]
    return _uniq([term.title() if term.islower() else term for term in matches])[:8]


def fallback_gap_analysis(
    profile: dict[str, Any],
    resume_profile: dict[str, Any],
    answers: dict[str, Any],
    job: dict[str, Any],
    missing_docs: list[str] | None = None,
) -> dict[str, list[str]]:
    job_text = _haystack(job)
    missing_docs = list(missing_docs or job.get("missing_docs") or [])
    target_roles = list(resume_profile.get("target_roles") or profile.get("target_roles") or [])
    skills = list(resume_profile.get("skills") or [])
    certifications = list(resume_profile.get("certifications") or [])
    licenses = list(resume_profile.get("licenses") or [])
    locations = list(resume_profile.get("locations") or profile.get("preferred_locations") or [])

    strong_matches: list[str] = []
    weaker_matches: list[str] = []
    missing_evidence: list[str] = []
    tailoring_notes: list[str] = []

    role_hits = _matched_terms(job_text, target_roles)
    if role_hits:
        strong_matches.append(f"Role alignment: {', '.join(role_hits[:2])}")
    elif any(term in job_text for term in ROLE_HINTS):
        weaker_matches.append("Role family is relevant, but the vacancy uses broader or different title wording.")

    skill_hits = _matched_terms(job_text, skills + certifications + licenses)
    if skill_hits:
        strong_matches.append(f"Relevant evidence already present: {', '.join(skill_hits[:3])}")
    else:
        weaker_matches.append("The posting does not clearly overlap with your strongest listed skills yet.")

    domain_hits = [term.title() for term in DOMAIN_HINTS if term in job_text and term in str(resume_profile.get("raw_text", "")).lower()]
    if domain_hits:
        strong_matches.append(f"Domain overlap: {', '.join(_uniq(domain_hits)[:3])}")
    elif any(term in job_text for term in DOMAIN_HINTS):
        weaker_matches.append("The vacancy sits in a related healthcare or commercial domain, but the overlap is not explicit enough.")

    location_hits = _matched_terms(job_text, locations)
    if location_hits:
        strong_matches.append(f"Location fit: {', '.join(location_hits[:2])}")
    elif locations:
        weaker_matches.append("Location is not clearly confirmed in the posting, so availability or travel willingness should be stated.")

    if missing_docs:
        missing_evidence.extend([f"Required document gap: {item}" for item in missing_docs])
    if "driver" in job_text and not any("driver" in item.lower() or "licence" in item.lower() or "license" in item.lower() for item in licenses + missing_docs):
        missing_evidence.append("Driver's licence or mobility requirement should be made explicit if you meet it.")
    if "degree" in job_text or "qualification" in job_text:
        education_text = " ".join(str(item) for item in resume_profile.get("education", []))
        if not re.search(r"(degree|diploma|certificate|qualification)", education_text, re.I):
            missing_evidence.append("Formal qualification is mentioned in the vacancy; surface the strongest education line if applicable.")
    if "experience" in job_text and "sales" in job_text and not any(term in str(resume_profile.get("raw_text", "")).lower() for term in ["sales", "territory", "representative", "promotion"]):
        missing_evidence.append("Commercial or field-sales experience is not obvious enough from the current resume signals.")

    keywords = _job_keywords(job_text)
    if keywords:
        tailoring_notes.append(f"Mirror these keywords in the top third of the CV: {', '.join(keywords[:5])}.")
    if target_roles:
        tailoring_notes.append(f"Use the exact vacancy title or nearest role match near the CV headline: {job.get('title', 'the role')}.")
    if role_hits or skill_hits:
        tailoring_notes.append("Move the most relevant achievements and healthcare-facing experience above older or lower-signal experience.")
    if any(term in job_text for term in ["travel", "territory", "field", "driver"]):
        tailoring_notes.append("State travel readiness, licence status, and territory flexibility clearly in the profile summary.")
    if answers.get("work_authorization"):
        tailoring_notes.append("If the portal asks, reuse your stored work-authorization and availability answers consistently.")

    if not strong_matches:
        strong_matches.append("General healthcare-facing and customer-support background still gives this vacancy a workable baseline fit.")
    if not weaker_matches:
        weaker_matches.append("The main remaining risk is whether the vacancy expects more direct field-sales wording than the current CV shows.")
    if not missing_evidence:
        missing_evidence.append("No hard evidence gap was detected from the current vacancy text, but tailor the top CV section before applying.")

    return {
        "strong_matches": _uniq(strong_matches)[:4],
        "weaker_matches": _uniq(weaker_matches)[:4],
        "missing_evidence": _uniq(missing_evidence)[:4],
        "cv_tailoring_notes": _uniq(tailoring_notes)[:5],
    }


def gap_analysis_text(job: dict[str, Any], package: dict[str, Any]) -> str:
    lines = [
        f"Gap analysis for {job.get('title', 'this job')}",
        "",
        "Strong matches:",
    ]
    lines.extend(f"- {item}" for item in package.get("strong_matches", []))
    lines.extend(["", "Weaker signals:"])
    lines.extend(f"- {item}" for item in package.get("weaker_matches", []))
    lines.extend(["", "Missing evidence or risks:"])
    lines.extend(f"- {item}" for item in package.get("missing_evidence", []))
    lines.extend(["", "CV tailoring notes:"])
    lines.extend(f"- {item}" for item in package.get("cv_tailoring_notes", []))
    return "\n".join(lines)
