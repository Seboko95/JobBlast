"""Small, transparent persona helpers for independent career searches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def personas_for_profile(profile: dict[str, Any]) -> list[dict[str, Any]]:
    stored = [item for item in profile.get("personas", []) or [] if isinstance(item, dict) and item.get("id")]
    if stored:
        return deepcopy(stored)
    return [
        {
            "id": "default",
            "name": "Primary search",
            "target_roles": list(profile.get("target_roles") or []),
            "preferred_locations": list(profile.get("preferred_locations") or []),
            "keywords": list(profile.get("keywords") or []),
            "excluded_keywords": list(profile.get("excluded_keywords") or []),
            "blocked_employers": list(profile.get("blocked_employers") or []),
        }
    ]


def active_persona(profile: dict[str, Any]) -> dict[str, Any]:
    personas = personas_for_profile(profile)
    active_id = str(profile.get("active_persona_id") or "").strip()
    return next((item for item in personas if str(item.get("id")) == active_id), personas[0])


def select_persona(profile: dict[str, Any], persona_id: str) -> dict[str, Any] | None:
    return next((item for item in personas_for_profile(profile) if str(item.get("id")) == str(persona_id)), None)


def upsert_persona(profile: dict[str, Any], persona: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(persona.get("id") or "").strip()
    if not persona_id:
        raise ValueError("Persona id is required.")
    personas = personas_for_profile(profile)
    if personas and personas[0].get("id") == "default" and not profile.get("personas"):
        personas = []
    replacement = {**persona, "id": persona_id, "name": str(persona.get("name") or persona_id).strip()}
    for index, current in enumerate(personas):
        if str(current.get("id")) == persona_id:
            personas[index] = replacement
            break
    else:
        personas.append(replacement)
    profile["personas"] = personas
    if not profile.get("active_persona_id") or profile.get("active_persona_id") == "default":
        profile["active_persona_id"] = persona_id
    return profile
