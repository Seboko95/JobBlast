"""Profile normalization, matching profile construction, and settings parsing."""

from __future__ import annotations

from typing import Any

from core.defaults import (
    PROFILE_LIST_FIELDS,
    PROFILE_TEXT_FIELDS,
    SETTINGS_PROMPTS,
    default_profile,
)
from core.feedback import feedback_match_reason
from core.personas import active_persona
from jobblast_ats import build_candidate_profile


def normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw).replace("\n", ",").split(",")
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value).strip().split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
    return items


def merge_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    profile = default_profile()
    raw = raw or {}
    if raw.get("job_title") and not raw.get("target_roles"):
        raw = {**raw, "target_roles": [str(raw.get("job_title")).strip()]}
    if raw.get("location") and not raw.get("preferred_locations"):
        raw = {**raw, "preferred_locations": [str(raw.get("location")).strip()]}
    if raw.get("search_keywords") and not raw.get("keywords"):
        raw = {**raw, "keywords": normalize_string_list(raw.get("search_keywords"))}
    if raw.get("resume") and not raw.get("resume_summary"):
        raw = {**raw, "resume_summary": str(raw.get("resume")).strip()}
    for field in PROFILE_TEXT_FIELDS:
        if raw.get(field) is not None:
            profile[field] = str(raw.get(field)).strip()
    for field in PROFILE_LIST_FIELDS:
        if raw.get(field) is not None:
            profile[field] = normalize_string_list(raw.get(field))
    try:
        threshold = int(raw.get("minimum_match_threshold", profile["minimum_match_threshold"]))
    except Exception:
        threshold = profile["minimum_match_threshold"]
    profile["minimum_match_threshold"] = max(1, min(100, threshold))
    if raw.get("cover_letter_tone"):
        profile["cover_letter_tone"] = str(raw["cover_letter_tone"]).strip().lower()
    if raw.get("cover_letter_length"):
        profile["cover_letter_length"] = str(raw["cover_letter_length"]).strip().lower()
    notifications = dict(profile["notification_preferences"])
    notifications.update(dict(raw.get("notification_preferences") or {}))
    profile["notification_preferences"] = notifications
    public_service = dict(profile["public_service_options"])
    public_service.update(dict(raw.get("public_service_options") or {}))
    profile["public_service_options"] = public_service
    if raw.get("active_resume_path"):
        profile["active_resume_path"] = str(raw["active_resume_path"]).strip()
    raw_personas = raw.get("personas")
    if isinstance(raw_personas, list):
        profile["personas"] = [item for item in raw_personas if isinstance(item, dict) and str(item.get("id") or "").strip()]
    if raw.get("active_persona_id") is not None:
        profile["active_persona_id"] = str(raw.get("active_persona_id") or "").strip() or "default"
    return profile


def profile_for_matching(profile: dict[str, Any]) -> dict[str, Any]:
    persona = active_persona(profile)
    target_roles = normalize_string_list(persona.get("target_roles") or profile.get("target_roles"))
    locations = normalize_string_list(persona.get("preferred_locations") or profile.get("preferred_locations"))
    keywords = normalize_string_list(persona.get("keywords") or profile.get("keywords"))
    excluded_keywords = normalize_string_list(profile.get("excluded_keywords"))
    return {
        "name": profile.get("name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "linkedin": profile.get("linkedin", ""),
        "job_title": target_roles[0] if target_roles else "",
        "location": locations[0] if locations else "Gauteng",
        "search_keywords": ", ".join(keywords),
        "keywords": keywords,
        "excluded_keywords": normalize_string_list(persona.get("excluded_keywords") or profile.get("excluded_keywords")),
        "blocked_employers": normalize_string_list(persona.get("blocked_employers") or profile.get("blocked_employers")),
        "personas": list(profile.get("personas") or []),
        "persona_id": persona.get("id", "default"),
        "resume": profile.get("resume_summary", ""),
    }


def build_candidate(profile: dict[str, Any], answers: dict[str, Any], resume_profile: dict[str, Any]) -> dict[str, Any]:
    candidate = build_candidate_profile(profile_for_matching(profile), answers or {}, resume_profile or {})
    from core.evidence import build_evidence_ledger

    candidate["evidence_ledger"] = build_evidence_ledger(profile, resume_profile or {}, answers or {})
    return candidate


def apply_profile_input(profile: dict[str, Any], field: str, raw: str) -> tuple[dict[str, Any], str | None]:
    value = raw.strip()
    if field in PROFILE_TEXT_FIELDS:
        profile[field] = value
        return profile, None
    if field in PROFILE_LIST_FIELDS:
        items = normalize_string_list(value)
        if not items:
            return profile, "Please send at least one value."
        profile[field] = items
        return profile, None
    if field == "minimum_match_threshold":
        try:
            threshold = int(value)
        except ValueError:
            return profile, "Threshold must be a whole number."
        profile[field] = max(1, min(100, threshold))
        return profile, None
    return profile, f"Unsupported setting: {field}"


def toggle_nested_option(profile: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    updated = merge_profile(profile)
    nested = dict(updated.get(section) or {})
    nested[key] = not bool(nested.get(key, False))
    updated[section] = nested
    return updated


def settings_prompt(field: str) -> str:
    return SETTINGS_PROMPTS.get(field, "Send the new value.")


def explain_match(job: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    title_lower = str(job.get("title", "")).lower()
    description_lower = str(job.get("description", "")).lower()
    haystack = f"{title_lower} {description_lower}"

    for role in candidate.get("target_roles", [])[:4]:
        if role and role.lower() in haystack:
            reasons.append(f"Role match: {role}")
            break
    skill_hits = [skill for skill in candidate.get("skills", []) if skill.lower() in haystack][:3]
    if skill_hits:
        reasons.append("Skill overlap: " + ", ".join(skill_hits))
    location_hits = [loc for loc in candidate.get("locations", []) if loc and loc.lower() in haystack][:2]
    if location_hits:
        reasons.append("Location fit: " + ", ".join(location_hits))
    if job.get("public_service"):
        reasons.append("Includes public-service application guidance")
    feedback_reason = feedback_match_reason(job, candidate)
    if feedback_reason:
        reasons.append(feedback_reason)
    if not reasons:
        portal_type = job.get("portal_type") or "job portal"
        reasons.append(f"Strong title/source match on {portal_type}")
    return reasons
