"""Structured, user-controlled evidence used by matching and generation."""

from __future__ import annotations

import re
from typing import Any


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _source_reference(source: str, profile: dict[str, Any], resume_profile: dict[str, Any]) -> tuple[str, str]:
    if source == "resume":
        return str(resume_profile.get("source_document_id") or ""), str(
            resume_profile.get("source_reference") or resume_profile.get("sha256") or "active resume"
        )
    if source == "answers":
        return "answer_bank", "user answer bank"
    return "profile", "profile settings"


def build_evidence_ledger(
    profile: dict[str, Any],
    resume_profile: dict[str, Any],
    answers: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a deduplicated, provenance-rich ledger from current user data."""
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        fact_type: str,
        claim: Any,
        source: str,
        confidence: str = "medium",
        section: str | None = None,
        allowed: bool = True,
    ) -> None:
        normalized = _clean(claim)
        if not normalized:
            return
        key = (fact_type.lower(), normalized.lower())
        if key in seen:
            return
        seen.add(key)
        document_id, reference = _source_reference(source, profile, resume_profile)
        fact_id = f"{fact_type}:{len(facts) + 1}"
        facts.append(
            {
                "fact_id": fact_id,
                "id": fact_id,
                "fact_type": fact_type,
                "normalized_value": " ".join(normalized.lower().split()),
                "display_value": normalized,
                "normalized_claim": normalized,
                "source_document_id": document_id,
                "source_document": source,
                "source_section": section or fact_type,
                "source_reference": reference,
                "evidence_reference": reference,
                "confidence": confidence,
                "evidence_confidence": confidence,
                "user_verified": source in {"profile", "answers"},
                "allowed_in_generation": bool(allowed),
                "valid_from": None,
                "valid_to": None,
            }
        )

    for field in ["name", "email", "phone", "linkedin", "resume_summary"]:
        add(field, profile.get(field), "profile", "high")
    for field in ["target_roles", "preferred_locations", "keywords", "excluded_keywords"]:
        for value in profile.get(field, []) or []:
            add("preference" if field != "excluded_keywords" else "exclusion", value, "profile", "high", field, False)
    add("job_title", profile.get("job_title"), "profile", "high")

    for field in ["headline", "experience_years"]:
        add(field, resume_profile.get(field), "resume", "medium")
    for value in resume_profile.get("target_roles", []) or []:
        add("job_title", value, "resume", "medium", "headline")
    for field, label in [
        ("skills", "skill"),
        ("certifications", "certification"),
        ("licenses", "licence"),
        ("education", "qualification"),
        ("companies", "employer"),
        ("industries", "industry"),
        ("languages", "language"),
    ]:
        for value in resume_profile.get(field, []) or []:
            add(label, value, "resume", "medium", field)
    for item in resume_profile.get("employment", []) or []:
        if isinstance(item, dict):
            for field, label in [("title", "job_title"), ("employer", "employer"), ("responsibilities", "responsibility")]:
                values = item.get(field) if isinstance(item.get(field), list) else [item.get(field)]
                for value in values:
                    add(label, value, "resume", "medium", "employment")

    for field in ["current_title", "work_authorization", "drivers_license", "driver_license", "notice_period", "current_location"]:
        add(field, answers.get(field), "answers", "high")
    for field, value in (answers.get("confirmed_facts") or {}).items():
        add(str(field), value, "answers", "high", "confirmed_facts")

    for document in documents or []:
        if not isinstance(document, dict) or not document.get("active", True):
            continue
        document_id = str(document.get("id") or "").strip()
        for tag in document.get("tags", []) or []:
            add("document_evidence", tag, "resume", "strong_inferred", document.get("doc_type") or "document")
            if facts:
                facts[-1]["source_document_id"] = document_id
                facts[-1]["source_reference"] = str(document.get("original_name") or document_id)
                facts[-1]["source_document"] = str(document.get("original_name") or "supporting document")
        document_text = str(document.get("parsed_text") or "").lower()
        for fact_type, token in [("qualification", "qualification"), ("registration", "sapc"), ("licence", "licen"), ("skill", "skill")]:
            if token in document_text:
                add(fact_type, document.get("original_name") or token, "resume", "strong_inferred", document.get("doc_type") or "document")
                if facts:
                    facts[-1]["source_document_id"] = document_id

    return facts[:240]


def evidence_facts(profile: dict[str, Any], resume_profile: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    return [f"{item['fact_type']}: {item['normalized_claim']}" for item in build_evidence_ledger(profile, resume_profile, answers)]


_MATERIAL_CLAIM_RE = re.compile(
    r"\b(?:phd|mba|bachelor|master|degree|diploma|certificate|sapc|registered|licensed|licence|license|"
    r"\d+\s+years?|worked at|employed by|managed|supervised)\b",
    re.I,
)


def validate_generated_claims(package: dict[str, Any], ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify material candidate claims without letting unsupported claims pass silently."""
    evidence = " ".join(str(item.get("normalized_claim") or item.get("display_value") or "") for item in ledger).lower()
    text = " ".join(
        str(package.get(field) or "")
        for field in ["cover_letter", "motivation_summary", "suggested_answers", "cv_alignment_notes"]
    )
    claims: list[dict[str, Any]] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        lowered = sentence.lower().strip()
        terms = [term.lower() for term in _MATERIAL_CLAIM_RE.findall(lowered)]
        if not terms or not re.search(r"\b(?:i|my|we|worked|managed|supervised)\b", lowered):
            continue
        supported = [
            term
            for term in terms
            if term in evidence or (re.search(r"\d+", term) and re.search(r"\d+", term).group(0) in evidence)
        ]
        if len(supported) == len(terms):
            status = "SUPPORTED"
        elif supported:
            status = "PARTIAL_SUPPORT"
        elif any(term in lowered for term in ["licence", "license", "registered", "sapc", "phd", "mba", "degree", "diploma"]):
            status = "USER_CONFIRMATION_REQUIRED"
        else:
            status = "UNSUPPORTED"
        claims.append({"text": sentence[:240], "status": status, "evidence_terms": supported})
    issues = [item["text"] for item in claims if item["status"] in {"UNSUPPORTED", "USER_CONFIRMATION_REQUIRED"}]
    return {"valid": not issues, "issues": issues[:8], "evidence_count": len(ledger), "claims": claims[:20]}


REQUIREMENT_SYNONYMS = {
    "microsoft office": {"microsoft office", "office suite", "word", "excel", "powerpoint"},
    "office suite": {"microsoft office", "office suite", "word", "excel", "powerpoint"},
    "driver licence": {"driver licence", "drivers licence", "driver license", "drivers license", "driving licence", "driving license"},
    "professional registration": {"professional registration", "registered", "registration", "sapc"},
    "retail pharmacy experience": {"retail pharmacy", "pharmacy", "pharmacist assistant", "dispensary"},
    "pharmaceutical sales experience": {"pharmaceutical sales", "pharma sales", "medical representative", "sales representative"},
}


def _requirement_key(value: Any) -> str:
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    for key, aliases in REQUIREMENT_SYNONYMS.items():
        if key in normalized or any(alias in normalized for alias in aliases if len(alias) > 4):
            return key
    return normalized


def _requirement_candidates(job: dict[str, Any]) -> list[dict[str, Any]]:
    requirements = dict(job.get("application_requirements") or {})
    output: list[dict[str, Any]] = []

    def add(value: Any, mandatory: bool, confidence: str, source_text: str = "") -> None:
        if isinstance(value, dict):
            text = value.get("requirement") or value.get("name") or value.get("text")
            mandatory_value = value.get("mandatory", mandatory)
            confidence_value = value.get("confidence", confidence)
        else:
            text = value
            mandatory_value = mandatory
            confidence_value = confidence
        text = _clean(text)
        if text:
            output.append({"text": text, "mandatory": bool(mandatory_value), "confidence": str(confidence_value), "source_text": source_text or text})

    for key in ["mandatory_requirements", "required_requirements", "mandatory"]:
        for value in requirements.get(key, []) if isinstance(requirements.get(key), list) else []:
            add(value, True, "high")
    for key in ["preferred_requirements", "preferred"]:
        for value in requirements.get(key, []) if isinstance(requirements.get(key), list) else []:
            add(value, False, "medium")
    description = _clean(job.get("description"))
    for line in re.split(r"[\n.;]+", description):
        lower = line.lower()
        if any(token in lower for token in ["driver", "sapc", "professional registration", "degree", "diploma", "qualification", "microsoft office", "office suite"]):
            mandatory = bool(re.search(r"\b(required|must|essential|mandatory|minimum)\b", lower))
            add(line, mandatory, "high" if mandatory else "medium")
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, bool]] = set()
    for item in output:
        key = (_requirement_key(item["text"]), item["mandatory"])
        if not key[0] or key in seen:
            continue
        seen.add(key)
        item["normalized_requirement"] = key[0]
        deduped.append(item)
    return deduped[:40]


def audit_requirements(job: dict[str, Any], candidate: dict[str, Any], ledger: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Match vacancy requirements to evidence without turning absence into failure."""
    facts = ledger if ledger is not None else list(candidate.get("evidence_ledger") or [])
    fact_text = " ".join(str(item.get("normalized_value") or item.get("normalized_claim") or "").lower() for item in facts)
    answers = {str(key).lower(): str(value or "").lower() for key, value in (candidate.get("answer_bank") or {}).items()}
    audited: list[dict[str, Any]] = []
    for requirement in _requirement_candidates(job):
        normalized = requirement["normalized_requirement"]
        aliases = REQUIREMENT_SYNONYMS.get(normalized, {normalized})
        matched = [fact for fact in facts if any(alias in str(fact.get("normalized_value") or fact.get("normalized_claim") or "").lower() for alias in aliases)]
        explicit_no = any(
            value in {"no", "false", "not registered", "not licensed"}
            for key, value in answers.items()
            if any(token in key for token in normalized.split()[:2])
        )
        explicit_yes = any(
            value in {"yes", "true", "registered", "licensed"}
            for key, value in answers.items()
            if any(token in key for token in normalized.split()[:2])
        )
        if explicit_no:
            status, evidence_status = "NOT_MET", "MISSING"
            reason = "The user explicitly marked this requirement as unavailable."
        elif matched or explicit_yes:
            strong = explicit_yes or any(str(fact.get("confidence")) in {"high", "strong_inferred"} or fact.get("user_verified") for fact in matched)
            status, evidence_status = ("MET", "STRONG") if strong else ("LIKELY_MET", "PARTIAL")
            reason = "Matched to stored profile or document evidence." if strong else "Reasonable inferred match; user review is recommended."
        else:
            status, evidence_status = "UNKNOWN", "MISSING"
            reason = "No stored evidence proves or disproves this requirement."
        audited.append(
            {
                "type": "mandatory" if requirement["mandatory"] else "preferred",
                "normalized_requirement": normalized,
                "mandatory_or_preferred": "mandatory" if requirement["mandatory"] else "preferred",
                "confidence": requirement["confidence"],
                "source_text": requirement["source_text"],
                "evidence_status": evidence_status,
                "candidate_status": status,
                "matched_profile_fact_ids": [str(fact.get("fact_id") or fact.get("id")) for fact in matched],
                "blocking": bool(requirement["mandatory"]),
                "reason": reason,
            }
        )
    return audited
