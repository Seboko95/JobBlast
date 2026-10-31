"""Deterministic JobBlast intelligence shared by Telegram and API surfaces."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from core.evidence import audit_requirements
from core.feedback import feedback_score_adjustment

ENGINE_VERSION = "jobblast-intelligence-2"
WEIGHTS_VERSION = "structured-availability-v2"

TITLE_ALIASES = {
    "medical rep": "medical representative",
    "pharma rep": "pharmaceutical sales representative",
    "pharmaceutical sales": "pharmaceutical sales representative",
    "pharmacy assistant": "pharmacist assistant",
    "qualified pharmacist assistant": "pharmacist assistant",
    "post basic pharmacist assistant": "pharmacist assistant",
    "qpb pharmacist assistant": "pharmacist assistant",
    "product adviser": "product specialist",
    "territory sales": "territory manager",
}

TITLE_FAMILIES = {
    "pharmacist assistant": "pharmacy_assistant",
    "pharmacy technician": "pharmacy_assistant",
    "medical representative": "pharmaceutical_sales",
    "pharmaceutical sales representative": "pharmaceutical_sales",
    "product specialist": "pharmaceutical_sales",
    "territory manager": "commercial_healthcare",
    "sales representative": "commercial_sales",
    "software developer": "technology",
    "software engineer": "technology",
    "data analyst": "technology",
    "administration clerk": "public_administration",
    "admin clerk": "public_administration",
}

TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "trackingid", "refid", "pagenum", "page", "source", "mc_cid", "mc_eid",
}

GENERIC_ROLE_WORDS = {"sales", "customer", "service", "manager", "assistant", "support", "officer", "administrator"}


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def normalize_title(value: Any) -> str:
    title = normalize_text(value)
    return TITLE_ALIASES.get(title, title)


def occupation_family(title: Any) -> str:
    normalized = normalize_title(title)
    if normalized in TITLE_FAMILIES:
        return TITLE_FAMILIES[normalized]
    if any(term in normalized for term in ["pharmacy", "pharmacist", "medical representative", "pharmaceutical"]):
        return "healthcare_and_pharma"
    if any(term in normalized for term in ["software", "developer", "engineer", "technology", "data analyst"]):
        return "technology"
    if any(term in normalized for term in ["clerk", "administrator", "administration", "government"]):
        return "public_administration"
    return "other"


def title_relationship(left: Any, right: Any) -> str:
    left_title = normalize_title(left)
    right_title = normalize_title(right)
    if not left_title or not right_title:
        return "unknown"
    if left_title == right_title:
        return "equivalent"
    left_family = occupation_family(left_title)
    right_family = occupation_family(right_title)
    if left_family == right_family and left_family != "other":
        return "close_related"
    return "unrelated"


def canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return normalize_text(raw)
    query = urlencode(
        [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_QUERY_KEYS]
    )
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", "", query, ""))


def canonical_job_key(job: dict[str, Any]) -> str:
    company = normalize_text(job.get("company"))
    reference = normalize_text(job.get("reference_number"))
    external_id = normalize_text(job.get("external_id"))
    title = normalize_title(job.get("title"))
    location = normalize_text(job.get("location"))
    url = canonical_url(job.get("apply_url") or job.get("listing_url"))
    if reference:
        return f"reference|{company}|{reference}"
    if external_id and job.get("source_kind") in {"company_portal", "ats", "public_jobs_portal"}:
        return f"external|{company}|{external_id}"
    if url:
        return f"url|{url}"
    return f"fallback|{company}|{title}|{location}"


def identity_match(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, str]:
    """Return confidence and reason for a conservative cross-source merge."""
    left_ref = normalize_text(left.get("reference_number"))
    right_ref = normalize_text(right.get("reference_number"))
    if left_ref and left_ref == right_ref:
        return 1.0, "matching reference number"
    left_external = normalize_text(left.get("external_id"))
    right_external = normalize_text(right.get("external_id"))
    if left_external and left_external == right_external and normalize_text(left.get("company")) == normalize_text(right.get("company")):
        return 0.98, "matching employer requisition id"
    left_url = canonical_url(left.get("apply_url") or left.get("listing_url"))
    right_url = canonical_url(right.get("apply_url") or right.get("listing_url"))
    if left_url and left_url == right_url:
        return 0.98, "matching canonical application URL"
    same_company = normalize_text(left.get("company")) == normalize_text(right.get("company"))
    same_location = normalize_text(left.get("location")) == normalize_text(right.get("location"))
    relationship = title_relationship(left.get("title"), right.get("title"))
    left_words = set(normalize_text(left.get("description")).split())
    right_words = set(normalize_text(right.get("description")).split())
    overlap = len(left_words & right_words) / max(1, len(left_words | right_words))
    if same_company and same_location and relationship == "equivalent" and overlap >= 0.30:
        return 0.90, "same employer, equivalent title, location, and description overlap"
    return 0.0, "insufficient identity evidence"


def content_hash(job: dict[str, Any]) -> str:
    values = [
        normalize_text(job.get("title")),
        normalize_text(job.get("description")),
        normalize_text(job.get("location")),
        normalize_text(job.get("salary_package")),
        normalize_text(job.get("closing_date")),
        normalize_text(job.get("apply_url")),
    ]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    fields = ["title", "description", "location", "salary_package", "closing_date", "apply_url", "availability_status"]
    return [field for field in fields if normalize_text(previous.get(field)) != normalize_text(current.get(field))]


def _candidate_terms(candidate: dict[str, Any], key: str) -> list[str]:
    return [normalize_text(item) for item in candidate.get(key, []) or [] if normalize_text(item)]


def _contains_any(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term in text]


def _role_hits(title: str, text: str, terms: list[str]) -> list[str]:
    """Require meaningful title context so generic words do not define a role match."""
    hits: list[str] = []
    for term in terms:
        words = term.split()
        if len(words) == 1 and term in GENERIC_ROLE_WORDS:
            continue
        if term and (term in title or term in text):
            hits.append(term)
    return hits


def _parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    for candidate in [raw, raw.split("T", 1)[0]]:
        for pattern in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"]:
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    match = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", raw)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    return None


def _is_expired(job: dict[str, Any], today: date | None = None) -> bool:
    closing = _parse_date(job.get("closing_date"))
    return bool(closing and closing < (today or date.today()))


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def freshness_status(job: dict[str, Any], now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if str(job.get("availability_status") or "").lower() in {"closed", "expired", "removed"} or _is_expired(job, current.date()):
        return "expired"
    closing = _parse_date(job.get("closing_date"))
    if closing and 0 <= (closing - current.date()).days <= 3:
        return "closing_soon"
    last_verified = _parse_timestamp(job.get("last_verified"))
    last_seen = _parse_timestamp(job.get("last_seen_at") or job.get("updated_at") or job.get("discovered_at"))
    if last_verified and current - last_verified <= timedelta(days=7):
        return "active"
    if last_seen and current - last_seen <= timedelta(days=7):
        return "active_unverified"
    if last_seen and current - last_seen > timedelta(days=30):
        return "stale_unverified"
    if not last_seen:
        return "unknown"
    return "new" if current - last_seen <= timedelta(days=1) else "active_unverified"


def _answer_status(candidate: dict[str, Any], keys: list[str]) -> str:
    answers = candidate.get("answer_bank") or {}
    for key in keys:
        value = str(answers.get(key) or "").strip().lower()
        if value in {"no", "false", "not registered", "not licensed"}:
            return "NOT_MET"
        if value in {"yes", "true", "registered", "licensed"}:
            return "MET"
    return "UNKNOWN"


def eligibility(job: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(job.get(key) or "") for key in ["title", "description", "company", "location"]).lower()
    normalized_text = normalize_text(text)
    checks: list[dict[str, Any]] = []
    for keyword in [normalize_text(item) for item in candidate.get("excluded_keywords", []) or []]:
        if keyword and keyword in normalized_text:
            checks.append({"type": "excluded_keyword", "requirement": keyword, "status": "NOT_MET", "hard": True})
    company = normalize_text(job.get("company"))
    for employer in [normalize_text(item) for item in candidate.get("blocked_employers", []) or []]:
        if employer and employer in company:
            checks.append({"type": "blocked_employer", "requirement": employer, "status": "NOT_MET", "hard": True})
    if _is_expired(job) or str(job.get("availability_status") or "").lower() in {"expired", "closed", "removed"}:
        checks.append({"type": "listing_status", "requirement": "active listing", "status": "NOT_MET", "hard": True})

    if any(token in normalized_text for token in ["sapc registration", "professional registration", "registered pharmacist", "registration with"]):
        candidate_licenses = " ".join(_candidate_terms(candidate, "licenses") + _candidate_terms(candidate, "certifications"))
        status = "MET" if any(token in candidate_licenses for token in ["sapc", "registration", "registered"]) else _answer_status(candidate, ["sapc_registered", "professional_registration"])
        checks.append({"type": "mandatory_registration", "requirement": "professional registration", "status": status, "hard": True})

    if re.search(r"(?:degree|diploma|qualification|bachelor|certificate).{0,35}(?:required|must|essential)", normalized_text):
        education = " ".join(_candidate_terms(candidate.get("resume_profile", {}), "education") + _candidate_terms(candidate, "certifications"))
        checks.append({"type": "mandatory_qualification", "requirement": "stated qualification", "status": "MET" if education else "UNKNOWN", "hard": True})

    if re.search(r"(?:driver|drivers|driver s) (?:licen[cs]e|permit).{0,35}(?:required|must|essential)", normalized_text):
        status = _answer_status(candidate, ["drivers_license", "driver_license"])
        if status == "UNKNOWN" and any("driver" in normalize_text(item) or "licen" in normalize_text(item) for item in candidate.get("licenses", [])):
            status = "MET"
        checks.append({"type": "mandatory_licence", "requirement": "driver licence", "status": status, "hard": True})

    if any(token in normalized_text for token in ["must be eligible to work", "right to work required", "work authorization required"]):
        checks.append({"type": "work_authorization", "requirement": "work authorization", "status": _answer_status(candidate, ["work_authorization"]), "hard": True})

    hard_location = re.search(r"must be based in ([a-z ]{3,40})", normalized_text)
    candidate_locations = _candidate_terms(candidate, "locations")
    if hard_location and candidate_locations:
        required_location = hard_location.group(1).strip()
        status = "MET" if any(location in required_location or required_location in location for location in candidate_locations) else "NOT_MET"
        checks.append({"type": "hard_location", "requirement": required_location, "status": status, "hard": True})

    blockers = [check for check in checks if check["status"] == "NOT_MET" and check.get("hard")]
    unknowns = [check for check in checks if check["status"] == "UNKNOWN" and check.get("hard")]
    status = "closed" if any(item["type"] == "listing_status" for item in blockers) else "excluded" if blockers else "eligible"
    return {
        "status": status,
        "eligible": not blockers,
        "confidence": "high" if checks and not unknowns else "medium" if checks else "low",
        "checks": checks,
        "blockers": blockers,
        "unknowns": unknowns,
        "reasons": [item["type"] for item in blockers],
    }


def _dimension(value: int | None, availability: str, confidence: str, reason: str) -> dict[str, Any]:
    return {"value": value, "availability": availability, "confidence": confidence, "reason": reason}


def _has_detailed_job_text(job: dict[str, Any]) -> bool:
    description = normalize_text(job.get("description"))
    return len(description) >= 160 or any(
        marker in description
        for marker in ["responsibilities", "requirements", "qualifications", "duties", "experience required"]
    )


def _numeric_salary(value: Any) -> float | None:
    matches = re.findall(r"(?:r|zar|salary|package)?\s*([0-9][0-9 ,]*(?:\.[0-9]+)?)", str(value or "").lower())
    numbers = []
    for match in matches:
        compact = match.replace(" ", "").replace(",", "")
        try:
            number = float(compact)
        except ValueError:
            continue
        if number >= 1000:
            numbers.append(number)
    return max(numbers) if numbers else None


def classify_actionability(
    snapshot: dict[str, Any],
    job: dict[str, Any],
    missing_docs: list[str] | None = None,
) -> dict[str, Any]:
    """Classify a strong match without treating unavailable evidence as failure."""
    eligibility_result = snapshot.get("eligibility") or {}
    blockers = [
        f"Confirmed eligibility blocker: {item.get('requirement') or item.get('type')}"
        for item in eligibility_result.get("blockers") or []
    ]
    requirement_audit = list(snapshot.get("requirement_audit") or [])
    blockers.extend(
        f"Confirmed mandatory requirement not met: {item.get('normalized_requirement')}"
        for item in requirement_audit
        if item.get("mandatory_or_preferred") == "mandatory" and item.get("candidate_status") == "NOT_MET"
    )
    if blockers:
        return {
            "actionability_state": "not_actionable",
            "actionability_blockers": blockers,
            "actionability_preparation": [],
            "actionability_reason": blockers[0],
            "actionable": False,
        }
    if not snapshot.get("strong_match"):
        return {
            "actionability_state": "not_actionable",
            "actionability_blockers": [],
            "actionability_preparation": [],
            "actionability_reason": "The vacancy is below the strong-match threshold.",
            "actionable": False,
        }
    freshness = str(snapshot.get("freshness_status") or job.get("freshness_status") or "")
    if freshness in {"expired", "stale_unverified"}:
        blockers.append(f"Listing is {freshness.replace('_', ' ')}.")
    if str(job.get("trust_status") or snapshot.get("trust_status") or "") == "high_risk":
        blockers.append("Application path is high risk.")
    if not str(job.get("apply_url") or job.get("listing_url") or "").strip():
        blockers.append("No application route is available.")
    if blockers:
        return {
            "actionability_state": "not_actionable",
            "actionability_blockers": blockers,
            "actionability_preparation": [],
            "actionability_reason": blockers[0],
            "actionable": False,
        }

    preparation: list[str] = []
    for item in missing_docs or []:
        preparation.append(f"Prepare required document: {item}.")
    if snapshot.get("candidate_requirement_status") == "UNKNOWN":
        for item in eligibility_result.get("unknowns") or []:
            preparation.append(f"Confirm requirement: {item.get('requirement') or item.get('type')}.")
    for item in requirement_audit:
        if item.get("mandatory_or_preferred") == "mandatory" and item.get("candidate_status") in {"UNKNOWN", "LIKELY_MET"}:
            preparation.append(f"Confirm evidence for: {item.get('normalized_requirement')}.")
    if snapshot.get("resume_evidence_status") != "MET":
        preparation.append("Strengthen CV evidence for the role before submitting.")
    if str(job.get("trust_status") or snapshot.get("trust_status") or "") == "caution":
        preparation.append("Review the application path carefully before submitting.")
    if preparation:
        return {
            "actionability_state": "prep_needed",
            "actionability_blockers": [],
            "actionability_preparation": preparation,
            "actionability_reason": preparation[0],
            "actionable": False,
        }
    return {
        "actionability_state": "actionable",
        "actionability_blockers": [],
        "actionability_preparation": [],
        "actionability_reason": "Known requirements and application materials are ready for manual submission.",
        "actionable": True,
    }


def match_snapshot(job: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(job.get(key) or "") for key in ["title", "description", "location"]).lower()
    title = normalize_title(job.get("title"))
    role_terms = _candidate_terms(candidate, "target_roles")
    skill_terms = _candidate_terms(candidate, "skills") + _candidate_terms(candidate, "certifications") + _candidate_terms(candidate, "licenses")
    location_terms = _candidate_terms(candidate, "locations")
    role_hits = _role_hits(title, text, role_terms)
    skill_hits = _contains_any(text, skill_terms)
    location_hits = _contains_any(text, location_terms)
    detailed_text = _has_detailed_job_text(job)
    candidate_families = {occupation_family(item) for item in role_terms}
    role_value = 100 if any(hit in title for hit in role_hits) else 82 if role_hits and occupation_family(title) in candidate_families else 35 if role_hits else 10
    role = _dimension(role_value, "available", "high" if role_value >= 82 else "medium", "exact or normalized title match" if role_value >= 82 else "related title or text overlap" if role_hits else "no direct title overlap")

    experience_years = candidate.get("experience_years") or candidate.get("resume_profile", {}).get("experience_years")
    year_match = re.search(r"(\d+)\+?\s+years?", str(job.get("description") or "").lower())
    if year_match and str(experience_years).isdigit():
        required = int(year_match.group(1))
        experience = _dimension(100 if int(experience_years) >= required else 35, "available", "high", f"candidate reports {experience_years} years; posting asks for {required}")
    elif year_match:
        experience = _dimension(None, "unknown", "low", "experience requirement is present but candidate years are unknown")
    else:
        experience = _dimension(None, "unknown", "low", "the vacancy does not state an explicit experience threshold")

    if skill_terms and detailed_text:
        skills = _dimension(min(100, 35 + len(skill_hits) * 22), "available", "high" if skill_hits else "medium", f"{len(skill_hits)} configured skill or credential overlaps")
    elif skill_terms:
        skills = _dimension(None, "unknown", "low", "the source text is too brief to assess skills reliably")
    else:
        skills = _dimension(None, "unknown", "low", "no skills or credentials configured")
    location = _dimension(100 if location_hits else 55 if location_terms else None, "available" if location_terms else "unknown", "high" if location_hits else "low", "location overlap found" if location_hits else "location is not confirmed")
    resume_text = normalize_text((candidate.get("resume_profile") or {}).get("raw_text"))
    evidence_terms = _candidate_terms(candidate, "target_roles") + skill_terms
    evidence_hits = [term for term in evidence_terms if term in resume_text]
    if not resume_text:
        evidence = _dimension(None, "unknown", "low", "no parsed resume evidence")
    elif not detailed_text and not evidence_hits:
        evidence = _dimension(None, "unknown", "low", "the source text is too brief to compare against the resume")
    elif evidence_hits:
        evidence = _dimension(min(100, 55 + len(evidence_hits) * 12), "available", "high" if evidence_hits else "medium", "current resume supports matched role or skill terms" if evidence_hits else "resume is present but no matching evidence was found")
    else:
        evidence = _dimension(None, "unknown", "low", "resume is present but no matching role or skill evidence was found")
    freshness = _dimension(85 if job.get("last_verified") else 65, "available", "medium", "recent source verification" if job.get("last_verified") else "discovery timestamp available without verification") if (job.get("last_verified") or job.get("discovered_at") or job.get("last_seen_at")) else _dimension(None, "unknown", "low", "no reliable discovery or verification timestamp")

    preference_terms = _candidate_terms(candidate, "keywords")
    preference_hits = _contains_any(text, preference_terms)
    if preference_terms and detailed_text:
        preference = _dimension(100 if preference_hits else 55, "available", "medium" if preference_hits else "low", "configured preference keywords found" if preference_hits else "configured preference keywords were not found")
    elif preference_terms:
        preference = _dimension(None, "unknown", "low", "the source text is too brief to assess preference keywords")
    else:
        preference = _dimension(None, "unknown", "low", "no preference keywords configured")

    answer_bank = candidate.get("answer_bank") or {}
    job_salary = _numeric_salary(job.get("salary_package"))
    candidate_salary = _numeric_salary(answer_bank.get("salary_expectation") or candidate.get("salary_expectation"))
    if job_salary is None or candidate_salary is None:
        salary = _dimension(None, "unknown", "low", "salary information is unavailable or non-numeric")
    else:
        salary = _dimension(100 if job_salary >= candidate_salary else 35, "available", "medium", "advertised package compared with stored salary expectation")

    result = eligibility(job, candidate)
    requirement_audit = audit_requirements(job, candidate)
    candidate_requirement_status = "NOT_MET" if result["blockers"] else "UNKNOWN" if result["unknowns"] else "MET"
    resume_evidence_status = "MET" if evidence["value"] is not None and evidence["value"] >= 50 else "UNKNOWN"
    listing_requirement_confidence = "high" if detailed_text else "low"
    dimensions = {"role_fit": role, "experience_fit": experience, "skills_fit": skills, "location_fit": location, "resume_evidence_fit": evidence, "freshness_score": freshness, "preference_fit": preference, "salary_fit": salary}
    weights = {"role_fit": 0.26, "experience_fit": 0.14, "skills_fit": 0.18, "location_fit": 0.10, "resume_evidence_fit": 0.14, "freshness_score": 0.10, "preference_fit": 0.05, "salary_fit": 0.03}
    available = [(key, item["value"], weights[key]) for key, item in dimensions.items() if item["value"] is not None]
    available_weight = sum(weight for _, _, weight in available)
    coverage = round(available_weight / sum(weights.values()), 2) if available else 0
    overall = round(sum(value * weight for _, value, weight in available) / available_weight) if available else 0
    feedback_adjustment = feedback_score_adjustment(str(job.get("title") or ""), str(job.get("description") or ""), candidate, job)
    overall = max(0, min(100, overall + feedback_adjustment))
    if not result["eligible"]:
        overall = 0
    freshness_label = freshness_status(job)
    strong_match = bool(result["eligible"] and overall >= 70)
    explanation: list[str] = []
    if role_hits:
        explanation.append("Role alignment: " + ", ".join(role_hits[:3]))
    if skill_hits:
        explanation.append("Skills/evidence overlap: " + ", ".join(skill_hits[:4]))
    if location_hits:
        explanation.append("Location alignment: " + ", ".join(location_hits[:2]))
    if result["blockers"]:
        explanation.append("Eligibility blocker: " + ", ".join(item["type"] for item in result["blockers"]))
    if result["unknowns"]:
        explanation.append("Needs confirmation: " + ", ".join(item["type"] for item in result["unknowns"]))
    unknown_dimensions = [key for key, item in dimensions.items() if item["value"] is None]
    if unknown_dimensions:
        explanation.append("Unavailable dimensions: " + ", ".join(unknown_dimensions))
    if feedback_adjustment:
        explanation.append(f"Feedback adjustment: {feedback_adjustment:+d} (capped conservative preference signal)")
    if freshness_label == "stale_unverified":
        explanation.append("Freshness warning: listing has not been verified recently")
    if not explanation:
        explanation.append("No strong structured overlap was found; review the listing before saving it.")
    candidate_dimensions = [
        (item["value"], weights[key])
        for key, item in dimensions.items()
        if key not in {"resume_evidence_fit", "freshness_score"} and item["value"] is not None
    ]
    candidate_weight = sum(weight for _, weight in candidate_dimensions)
    candidate_fit_score = round(sum(value * weight for value, weight in candidate_dimensions) / candidate_weight) if candidate_dimensions else 0
    recommendation_score = round(overall * (0.6 + 0.4 * coverage))
    snapshot = {
        "engine_version": ENGINE_VERSION,
        "weights_version": WEIGHTS_VERSION,
        "overall_score": overall,
        "recommendation_score": recommendation_score,
        "score_coverage": coverage,
        "score_confidence": "high" if coverage >= 0.8 else "medium" if coverage >= 0.6 else "low",
        "strong_match": strong_match,
        "actionable": False,
        "actionability_state": "not_actionable",
        "actionability_blockers": [],
        "actionability_preparation": [],
        "actionability_reason": "",
        "candidate_fit_score": candidate_fit_score,
        "candidate_requirement_status": candidate_requirement_status,
        "resume_evidence_status": resume_evidence_status,
        "listing_requirement_confidence": listing_requirement_confidence,
        "dimensions": {key: item["value"] for key, item in dimensions.items()},
        "dimension_details": dimensions,
        "eligibility": result,
        "requirement_audit": requirement_audit,
        "freshness_status": freshness_label,
        "matched_terms": {"roles": role_hits[:8], "skills": skill_hits[:8], "locations": location_hits[:4]},
        "explanation": explanation,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    snapshot.update(classify_actionability(snapshot, job))
    if snapshot["actionability_state"] == "prep_needed":
        explanation.append("Preparation needed: " + " ".join(snapshot["actionability_preparation"][:2]))
    elif snapshot["actionability_state"] == "actionable":
        explanation.append("Ready for manual submission: known requirements and materials are ready.")
    return snapshot
