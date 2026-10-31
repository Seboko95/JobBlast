"""Bounded, conservative verification against public authoritative vacancy pages."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

OFFICIAL_PORTALS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday", "successfactors", "icims", "recruitee"}
OFFICIAL_SOURCE_KINDS = {"company_portal", "public_jobs_portal"}
VERIFICATION_CACHE_SECONDS = 6 * 60 * 60


def _host(url: str) -> str:
    return urlparse(str(url or "").strip()).netloc.lower()


def is_official_candidate(job: dict[str, Any]) -> bool:
    portal = str(job.get("portal_type") or "").lower()
    kind = str(job.get("source_kind") or "").lower()
    host = _host(job.get("apply_url") or job.get("listing_url"))
    return bool(
        kind in OFFICIAL_SOURCE_KINDS
        or portal in OFFICIAL_PORTALS
        or host.endswith(".gov.za")
    )


def _title_tokens(title: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(title or "").lower()) if len(token) > 2}


def _title_supported(title: str, body: str) -> bool:
    tokens = _title_tokens(title)
    if not tokens:
        return False
    observed = set(re.findall(r"[a-z0-9]+", body.lower()))
    return len(tokens & observed) >= max(1, min(3, len(tokens)))


async def verify_job(job: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
    url = str(job.get("authoritative_url") or job.get("apply_url") or job.get("listing_url") or "").strip()
    result: dict[str, Any] = {
        "job_id": str(job.get("id") or ""),
        "attempted": False,
        "status": "unverified",
        "method": "no_official_route",
        "authoritative_url": url,
        "official_requisition_id": str(job.get("reference_number") or job.get("external_id") or ""),
        "conflict": "",
    }
    if not is_official_candidate(job):
        return result
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        result.update(status="degraded", method="invalid_official_url")
        return result
    result["attempted"] = True
    try:
        response = await client.get(url, headers={"User-Agent": "JobBlast/1.0 official-verification"})
    except httpx.TimeoutException:
        result.update(status="degraded", method="timeout")
    except httpx.TransportError as exc:
        message = str(exc).lower()
        result.update(status="degraded", method="tls_failure" if "ssl" in message or "certificate" in message else "network_failure")
    except Exception:
        result.update(status="degraded", method="verification_failure")
    else:
        final_url = str(response.url)
        result["authoritative_url"] = final_url
        if response.status_code in {401, 403}:
            result.update(status="official_unavailable", method=f"http_{response.status_code}")
        elif response.status_code == 404:
            result.update(status="official_unavailable", method="http_404")
        elif response.status_code >= 500:
            result.update(status="degraded", method=f"http_{response.status_code}")
        elif response.status_code >= 400:
            result.update(status="degraded", method=f"http_{response.status_code}")
        elif _title_supported(str(job.get("title") or ""), response.text[:500_000]):
            result.update(status="source_verified", method="official_page_title_match")
        else:
            result.update(status="conflicted", method="official_page_identity_unconfirmed", conflict="The official page was reachable but did not contain enough title evidence to confirm the listing.")
    return result


async def verify_priority_jobs(jobs: list[dict[str, Any]], limit: int = 12) -> dict[str, Any]:
    """Verify only high-value records so a scan remains bounded and partial-safe."""
    candidates = [
        job for job in jobs
        if (
            job.get("strong_match")
            or job.get("actionability_state") in {"actionable", "prep_needed"}
            or job.get("public_service")
            or job.get("state") == "saved"
            or job.get("closing_soon")
            or str(job.get("trust_status") or "").lower() in {"suspicious", "low_trust"}
        )
    ]
    def priority(job: dict[str, Any]) -> int:
        if job.get("strong_match"):
            return 7
        if job.get("actionability_state") == "actionable":
            return 6
        if job.get("actionability_state") == "prep_needed":
            return 5
        if job.get("state") == "saved":
            return 4
        if job.get("public_service"):
            return 3
        if job.get("closing_soon"):
            return 2
        return 1

    selected = sorted(
        candidates,
        key=lambda item: (priority(item), is_official_candidate(item), item.get("recommendation_score", item.get("fit_score", 0))),
        reverse=True,
    )[:max(1, int(limit))]
    if not selected:
        return {"attempted": 0, "cache_hits": 0, "verified": 0, "unverified": 0, "conflicted": 0, "degraded": 0, "results": []}

    def cache_key(job: dict[str, Any]) -> str:
        revision = str(job.get("content_hash") or job.get("revision") or "")
        url = str(job.get("verification_url") or job.get("authoritative_url") or job.get("apply_url") or job.get("listing_url") or "")
        return f"{revision}|{url}"

    def cached_result(job: dict[str, Any]) -> dict[str, Any] | None:
        checked = str(job.get("verification_checked_at") or "")
        if not checked or str(job.get("verification_revision") or "") != cache_key(job):
            return None
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(checked.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            return None
        if age < 0 or age > VERIFICATION_CACHE_SECONDS:
            return None
        return {
            "job_id": str(job.get("id") or ""),
            "attempted": False,
            "cached": True,
            "status": str(job.get("verification_status") or "unverified"),
            "method": str(job.get("verification_method") or "cached"),
            "authoritative_url": str(job.get("authoritative_url") or job.get("apply_url") or job.get("listing_url") or ""),
            "official_requisition_id": str(job.get("official_requisition_id") or job.get("reference_number") or job.get("external_id") or ""),
            "conflict": "",
        }

    cached = [item for item in (cached_result(job) for job in selected) if item]
    pending = [job for job in selected if cached_result(job) is None]
    semaphore = asyncio.Semaphore(3)

    results = list(cached)
    if pending:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True, verify=True) as client:
            async def bounded(job: dict[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    return await verify_job(job, client)

            results.extend(await asyncio.gather(*(bounded(job) for job in pending)))
    return {
        "attempted": sum(1 for item in results if item.get("attempted")),
        "cache_hits": sum(1 for item in results if item.get("cached")),
        "verified": sum(1 for item in results if item.get("status") == "source_verified"),
        "unverified": sum(1 for item in results if item.get("status") in {"unverified", "official_unavailable"}),
        "conflicted": sum(1 for item in results if item.get("status") == "conflicted"),
        "degraded": sum(1 for item in results if item.get("status") == "degraded"),
        "results": results,
    }


def apply_verification(job: dict[str, Any], result: dict[str, Any], checked_at: str) -> dict[str, Any]:
    updated = dict(job)
    updated["verification_status"] = result.get("status", "unverified")
    updated["verification_method"] = result.get("method", "")
    updated["authoritative_url"] = result.get("authoritative_url") or updated.get("apply_url") or updated.get("listing_url") or ""
    updated["official_requisition_id"] = result.get("official_requisition_id") or updated.get("official_requisition_id") or ""
    updated["verification_checked_at"] = checked_at
    revision = str(updated.get("content_hash") or updated.get("revision") or "")
    updated["verification_revision"] = f"{revision}|{updated['authoritative_url']}"
    updated["verification_url"] = updated["authoritative_url"]
    if result.get("status") == "source_verified":
        updated["last_verified"] = checked_at
    if result.get("conflict"):
        conflicts = list(updated.get("verification_conflicts") or [])
        conflicts.append({"at": checked_at, "message": result["conflict"], "url": updated["authoritative_url"]})
        updated["verification_conflicts"] = conflicts[-10:]
    return updated
