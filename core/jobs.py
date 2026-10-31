"""Job record shaping, manual pipeline state rules, and application guidance helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from urllib.parse import urlparse

from core.defaults import PROFILE_STATE_LABELS, TERMINAL_JOB_STATES
from core.intelligence import canonical_job_key, classify_actionability, content_hash, freshness_status, match_snapshot, normalize_title, occupation_family
from core.profile import explain_match
from jobblast_ats import summarize_application_requirements


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def canonical_job_url(job: dict) -> str:
    raw = str(job.get("apply_url") or job.get("listing_url") or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.lower()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def job_identity_key(job: dict) -> str:
    return canonical_job_key(job)


def build_job_id(job: dict) -> str:
    seed = job_identity_key(job)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def parse_closing_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if "T" in raw:
        candidates.append(raw.split("T", 1)[0])
    patterns = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for candidate in candidates:
        for pattern in patterns:
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


def is_job_expired(job: dict, today: date | None = None) -> bool:
    closing = parse_closing_date(job.get("closing_date"))
    if closing is None:
        return False
    current = today or date.today()
    return closing < current


def is_public_service(job: dict) -> bool:
    requirements = dict(job.get("application_requirements") or {})
    return bool(job.get("public_service") or requirements.get("public_service") or requirements.get("z83_required"))


def infer_application_method(job: dict) -> str:
    requirements = dict(job.get("application_requirements") or {})
    method = str(requirements.get("application_method") or "").strip()
    if method:
        mapping = {
            "online_portal": "Online portal",
            "email": "Email",
            "hand_delivery": "Hand delivery",
            "post": "Postal application",
            "mixed": "Multiple methods",
        }
        return mapping.get(method, method.replace("_", " ").title())
    portal_type = str(job.get("portal_type") or "portal").lower()
    if portal_type in {"linkedin", "greenhouse", "lever", "smartrecruiters", "workday"}:
        return f"{portal_type.title()} portal"
    return "Check job link"


def required_documents(job: dict, docs: list[dict], resume_available: bool, cover_letter_ready: bool = False) -> tuple[list[str], list[str]]:
    requirements = dict(job.get("application_requirements") or {})
    required = ["Resume / CV"]
    missing: list[str] = []

    if not resume_available:
        missing.append("Resume / CV")

    if is_public_service(job):
        required.append("Detailed CV")
        if requirements.get("z83_required"):
            required.append("Z83 form")
            missing.append("Z83 form")
    lower_text = f"{job.get('description', '')} {requirements.get('summary', '')}".lower()
    if "cover letter" in lower_text or "motivation" in lower_text:
        required.append("Cover letter / motivation")
        if not cover_letter_ready and not any(doc.get("doc_type") == "cover_letter" and doc.get("active", True) for doc in docs):
            missing.append("Cover letter / motivation")
    if any(token in lower_text for token in ["driver", "driver's licence", "drivers license", "code 10"]):
        required.append("Driver's licence proof")
        if not any(doc.get("doc_type") == "license" for doc in docs):
            missing.append("Driver's licence proof")
    if "sapc" in lower_text:
        required.append("SAPC / registration proof")
        if not any("sapc" in (tag or "") for doc in docs for tag in doc.get("tags", [])):
            missing.append("SAPC / registration proof")

    deduped_required: list[str] = []
    seen_required: set[str] = set()
    for item in required:
        key = item.lower()
        if key in seen_required:
            continue
        seen_required.add(key)
        deduped_required.append(item)

    deduped_missing: list[str] = []
    seen_missing: set[str] = set()
    for item in missing:
        key = item.lower()
        if key in seen_missing:
            continue
        seen_missing.add(key)
        deduped_missing.append(item)
    return deduped_required, deduped_missing


def readiness_items(job: dict, required: list[str], missing: list[str]) -> list[dict[str, str]]:
    missing_keys = {str(item).lower() for item in missing}
    items = []
    for item in required:
        items.append({
            "name": item,
            "importance": "required",
            "status": "missing" if item.lower() in missing_keys else "ready",
        })
    for item in list((job.get("application_requirements") or {}).get("optional_documents") or []):
        name = str(item).strip()
        if name:
            items.append({"name": name, "importance": "optional", "status": "unknown"})
    return items


def infer_initial_state(job: dict, missing_docs: list[str]) -> str:
    if str(job.get("eligibility_status") or "") in {"excluded", "closed"}:
        return "closed"
    if str(job.get("state") or "") in TERMINAL_JOB_STATES:
        return str(job["state"])
    if missing_docs:
        return "needs_documents"
    return "matched"


def recommended_next_action(job: dict, missing_docs: list[str]) -> str:
    if missing_docs:
        return f"Gather or prepare: {', '.join(missing_docs)}."
    if is_public_service(job):
        return "Open the vacancy, review the requirement summary, prepare the Z83 pack, and submit manually."
    return "Open the apply link, attach the tailored cover letter, and apply manually."


def manual_apply_notes(job: dict, missing_docs: list[str]) -> list[str]:
    notes = []
    requirements = dict(job.get("application_requirements") or {})
    summary = summarize_application_requirements(requirements)
    if summary:
        notes.append(summary)
    if job.get("portal_type"):
        notes.append(f"Likely portal type: {job['portal_type']}")
    if missing_docs:
        notes.append("Missing documents: " + ", ".join(missing_docs))
    return notes


def build_preparation_plan(job: dict, snapshot: dict, missing_docs: list[str], cover_letter_ready: bool) -> dict:
    """Return a concise, durable plan separating blockers from user preparation."""
    blockers = list(snapshot.get("actionability_blockers") or [])
    required = [f"Prepare required document: {item}." for item in missing_docs]
    confirmations = []
    optional = []
    for item in snapshot.get("requirement_audit") or []:
        status = str(item.get("candidate_status") or "UNKNOWN")
        label = str(item.get("normalized_requirement") or item.get("source_text") or "requirement")
        if item.get("mandatory_or_preferred") == "mandatory" and status in {"UNKNOWN", "LIKELY_MET"}:
            confirmations.append(f"Confirm evidence for {label}.")
        elif item.get("mandatory_or_preferred") == "preferred" and status == "UNKNOWN":
            optional.append(f"Review preferred requirement: {label}.")
    if snapshot.get("resume_evidence_status") != "MET":
        required.append("Tailor the CV to surface the strongest supported role evidence.")
    if not cover_letter_ready and any("cover letter" in str(item).lower() or "motivation" in str(item).lower() for item in missing_docs):
        required.append("Generate and review the tailored cover letter.")
    generated = ["Cover letter" if cover_letter_ready else ""]
    generated = [item for item in generated if item]
    return {
        "status": snapshot.get("actionability_state", "not_actionable"),
        "blocking_items": blockers,
        "preparation_items": list(dict.fromkeys(required)),
        "manual_items": ["Open the official listing and submit manually."],
        "generated_items": generated,
        "confirmed_items": [
            str(item.get("normalized_requirement"))
            for item in snapshot.get("requirement_audit") or []
            if item.get("candidate_status") == "MET"
        ],
        "confirmation_items": confirmations,
        "optional_items": optional,
        "last_evaluated": snapshot.get("generated_at", ""),
        "requirements": list(snapshot.get("requirement_audit") or []),
    }


def shape_job_record(
    user_id: int,
    scanned_job: dict,
    candidate: dict,
    docs: list[dict],
    resume_available: bool,
    existing: dict | None = None,
) -> dict:
    existing = existing or {}
    job = dict(scanned_job)
    analysis_job = {**existing, **job}
    job_id = existing.get("id") or build_job_id(job)
    required_docs, missing_docs = required_documents(job, docs, resume_available, bool(existing.get("cover_letter_ready")))
    job["missing_docs"] = missing_docs
    snapshot = match_snapshot(analysis_job, candidate)
    snapshot.update(classify_actionability(snapshot, analysis_job, missing_docs))
    preparation_plan = build_preparation_plan(job, snapshot, missing_docs, bool(existing.get("cover_letter_ready")))
    readiness = round(100 * (len(required_docs) - len(missing_docs)) / max(1, len(required_docs)))
    state = existing.get("state") or infer_initial_state(job, missing_docs)
    if state not in TERMINAL_JOB_STATES and state not in {"saved", "ready_for_manual_apply"}:
        state = infer_initial_state(job, missing_docs)
    source_entry = {
        "source_id": str(job.get("source_id") or ""),
        "source_name": str(job.get("source_name") or ""),
        "listing_url": str(job.get("listing_url") or ""),
        "apply_url": str(job.get("apply_url") or ""),
        "authority": str(job.get("source_authority") or "discovery"),
        "seen_at": str(job.get("discovered_at") or ""),
    }
    source_listings = list(existing.get("source_listings") or [])
    if source_entry["source_id"] and not any(item.get("source_id") == source_entry["source_id"] for item in source_listings if isinstance(item, dict)):
        source_listings.append(source_entry)
    return {
        **job,
        "id": job_id,
        "user_id": user_id,
        "canonical_key": job_identity_key(job),
        "source_ids": sorted(set([str(item) for item in list(existing.get("source_ids") or []) + [job.get("source_id")] if item])),
        "canonical_title": normalize_title(job.get("title")),
        "occupation_family": occupation_family(job.get("title")),
        "content_hash": content_hash(job),
        "fit_score": int(snapshot.get("overall_score") or 0),
        "recommendation_score": int(snapshot.get("recommendation_score") or snapshot.get("overall_score") or 0),
        "match_snapshot": snapshot,
        "strong_match": bool(snapshot.get("strong_match")),
        "actionable": bool(snapshot.get("actionable")),
        "actionability_state": snapshot.get("actionability_state", "not_actionable"),
        "actionability_blockers": list(snapshot.get("actionability_blockers") or []),
        "actionability_preparation": list(snapshot.get("actionability_preparation") or []),
        "actionability_reason": snapshot.get("actionability_reason", ""),
        "preparation_plan": preparation_plan,
        "eligibility_status": snapshot["eligibility"].get("status", job.get("eligibility_status", "unknown")),
        "eligibility_checks": list(snapshot["eligibility"].get("checks") or []),
        "eligibility_blockers": list(snapshot["eligibility"].get("blockers") or []),
        "freshness_status": snapshot.get("freshness_status") or freshness_status(job),
        "last_verified": job.get("last_verified") or existing.get("last_verified") or "",
        "verification_status": job.get("verification_status") or existing.get("verification_status") or "unverified",
        "verification_method": job.get("verification_method") or existing.get("verification_method") or "",
        "authoritative_url": job.get("authoritative_url") or existing.get("authoritative_url") or "",
        "official_requisition_id": job.get("official_requisition_id") or existing.get("official_requisition_id") or "",
        "verification_checked_at": job.get("verification_checked_at") or existing.get("verification_checked_at") or "",
        "verification_conflicts": list(job.get("verification_conflicts") or existing.get("verification_conflicts") or []),
        "first_seen_at": existing.get("first_seen_at") or job.get("discovered_at") or "",
        "source_listings": source_listings,
        "match_explanation": {
            "engine_version": snapshot.get("engine_version"),
            "weights_version": snapshot.get("weights_version"),
            "job_revision": int(existing.get("revision") or 0),
            "persona_id": candidate.get("persona_id", "default"),
            "generated_at": snapshot.get("generated_at"),
        },
        "application_readiness": {
            "score": readiness,
            "status": snapshot.get("actionability_state", "not_actionable"),
            "mandatory_requirement_status": snapshot.get("candidate_requirement_status", "UNKNOWN"),
            "application_document_status": "ready" if not missing_docs else "missing",
            "resume_evidence_quality": snapshot.get("resume_evidence_status", "UNKNOWN"),
            "manual_user_action_required": True,
            "preparation_plan": preparation_plan,
            "required": required_docs,
            "missing": missing_docs,
            "items": readiness_items(job, required_docs, missing_docs),
        },
        "public_service": bool(existing.get("public_service", job.get("public_service") or is_public_service(job))),
        "state": state,
        "is_new": existing.get("is_new", True),
        "required_docs": required_docs,
        "missing_docs": missing_docs,
        "why_match": snapshot.get("explanation") or existing.get("why_match") or explain_match(job, candidate),
        "recommended_next_action": recommended_next_action(job, missing_docs),
        "application_method_label": infer_application_method(job),
        "notes": manual_apply_notes(job, missing_docs),
        "cover_letter_ready": bool(existing.get("cover_letter_ready")),
        "generated_answers_ready": bool(existing.get("generated_answers_ready")),
        "applied_at": existing.get("applied_at"),
        "state_history": list(existing.get("state_history") or []),
        "updated_at": scanned_job.get("discovered_at") or existing.get("updated_at"),
    }


def state_label(job: dict) -> str:
    return PROFILE_STATE_LABELS.get(str(job.get("state") or ""), str(job.get("state") or "").replace("_", " ").title())
