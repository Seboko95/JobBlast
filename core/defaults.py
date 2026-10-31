"""Default settings and constants for the manual-apply JobBlast assistant."""

from __future__ import annotations

from copy import deepcopy

DEFAULT_NOTIFICATION_PREFERENCES = {
    "new_matches": True,
    "public_service": True,
    "scan_errors": True,
    "daily_digest": False,
    "deadlines": True,
    "follow_ups": True,
    "closing_soon": True,
    "important_updates": True,
    "interviews": True,
    "trust_warnings": True,
}

DEFAULT_PUBLIC_SERVICE_OPTIONS = {
    "enabled": True,
    "prioritize": True,
    "send_z83_files": True,
}

DEFAULT_PROFILE = {
    "name": "",
    "address_line": "",
    "city_province_postal": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "resume_summary": "",
    "target_roles": [],
    "preferred_locations": [],
    "keywords": [],
    "excluded_keywords": [],
    "blocked_employers": [],
    "personas": [],
    "active_persona_id": "default",
    "minimum_match_threshold": 18,
    "cover_letter_tone": "confident",
    "cover_letter_length": "medium",
    "notification_preferences": deepcopy(DEFAULT_NOTIFICATION_PREFERENCES),
    "active_resume_path": "",
    "public_service_options": deepcopy(DEFAULT_PUBLIC_SERVICE_OPTIONS),
}

PROFILE_TEXT_FIELDS = {
    "name",
    "address_line",
    "city_province_postal",
    "email",
    "phone",
    "linkedin",
    "resume_summary",
}

PROFILE_LIST_FIELDS = {
    "target_roles",
    "preferred_locations",
    "keywords",
    "excluded_keywords",
    "blocked_employers",
}

PROFILE_STATE_LABELS = {
    "discovered": "Discovered",
    "matched": "Matched",
    "saved": "Saved",
    "needs_documents": "Needs Documents",
    "ready_for_manual_apply": "Ready for Manual Apply",
    "applied_by_user": "Applied",
    "skipped": "Skipped",
    "closed": "Closed",
    "preparing": "Preparing",
    "ready_to_apply": "Ready to Apply",
    "interview": "Interview",
    "offer": "Offer",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
}

ACTIVE_PIPELINE_STATES = {
    "matched",
    "saved",
    "needs_documents",
    "ready_for_manual_apply",
    "applied_by_user",
    "preparing",
    "ready_to_apply",
    "interview",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
}

TERMINAL_JOB_STATES = {"applied_by_user", "accepted", "rejected", "withdrawn", "skipped", "closed"}

SETTINGS_PROMPTS = {
    "target_roles": "Send your target roles as a comma-separated list.",
    "preferred_locations": "Send your preferred locations as a comma-separated list.",
    "keywords": "Send job search keywords as a comma-separated list.",
    "excluded_keywords": "Send excluded keywords as a comma-separated list.",
    "minimum_match_threshold": "Send the minimum match threshold as a number between 1 and 100.",
    "resume_summary": "Send a short resume summary for matching and cover letters.",
    "name": "Send your full name.",
    "address_line": "Send your street address line.",
    "city_province_postal": "Send your city, province, and postal code line.",
    "email": "Send your email address.",
    "phone": "Send your phone number.",
    "linkedin": "Send your LinkedIn profile URL.",
}


def default_profile() -> dict:
    """Return a deep-copied default profile payload."""

    return deepcopy(DEFAULT_PROFILE)
