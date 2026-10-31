"""Deterministic listing authority and risk signals."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


OFFICIAL_PORTALS = {"greenhouse", "lever", "smartrecruiters", "workday", "ashby", "successfactors", "icims"}


def assess_listing_trust(job: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    source = source or job
    portal = str(job.get("portal_type") or source.get("portal_type") or "").lower()
    source_kind = str(source.get("kind") or job.get("source_kind") or "").lower()
    url = str(job.get("apply_url") or job.get("listing_url") or source.get("seed_url") or "")
    host = urlparse(url).netloc.lower()
    source_host = urlparse(str(source.get("seed_url") or "")).netloc.lower()
    score = 35
    positive: list[str] = []
    risks: list[str] = []
    if portal in OFFICIAL_PORTALS:
        score += 35
        positive.append("recognized ATS listing")
    if source_kind in {"company_portal", "public_jobs_portal"}:
        score += 20
        positive.append("employer or official public-service source")
    if host and source_host and host == source_host:
        score += 10
        positive.append("listing domain matches source domain")
    if host and host.endswith(".gov.za"):
        score += 10
        positive.append("South African government domain")
    if not host:
        risks.append("missing or invalid source URL")
    if any(term in host for term in ["bit.ly", "tinyurl", "t.me", "wa.me"]):
        score -= 25
        risks.append("redirect or messaging-only domain")
    if any(term in str(job.get("description") or "").lower() for term in ["pay a fee", "registration fee", "processing fee"]):
        score -= 35
        risks.append("application payment language")
    if any(term in host for term in ["gmail.com", "outlook.com", "yahoo.com"]) and source_kind in {"aggregator_search", "linkedin_search"}:
        score -= 15
        risks.append("free-email or secondary contact domain")
    score = max(0, min(100, score))
    if score >= 75 and (source_kind in {"company_portal", "public_jobs_portal"} or portal in OFFICIAL_PORTALS):
        trust_status = "verified"
    elif score >= 60:
        trust_status = "likely_legitimate"
    elif score >= 35:
        trust_status = "unverified"
    elif score >= 20:
        trust_status = "caution"
    else:
        trust_status = "high_risk"
    return {
        "trust_score": score,
        "trust_status": trust_status,
        "verification_status": "source_verified" if source_kind in {"company_portal", "public_jobs_portal"} or portal in OFFICIAL_PORTALS else "unverified",
        "positive_signals": positive,
        "risk_signals": risks,
        "source_authority": "official" if source_kind in {"company_portal", "public_jobs_portal"} or portal in OFFICIAL_PORTALS else "discovery",
    }
