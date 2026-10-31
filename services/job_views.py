"""Shared job filtering and status helpers for Telegram and API surfaces."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from core.intelligence import freshness_status
from core.jobs import parse_closing_date


def is_strong_match(job: dict[str, Any]) -> bool:
    """Strong-match views must never promote a hard-ineligible listing."""
    if "eligibility_status" not in job:
        # Legacy records remain stored, but unknown eligibility is not a strong match.
        return False
    if isinstance(job.get("match_snapshot"), dict) and "actionability_state" not in job:
        # A structured legacy record must be rescanned before it is promoted again.
        return False
    if "strong_match" in job:
        return bool(
            job.get("strong_match")
            and job.get("eligibility_status") == "eligible"
            and str(job.get("freshness_status") or "") not in {"expired", "stale_unverified"}
            and str(job.get("trust_status") or "") != "high_risk"
        )
    return bool(
        str(job.get("eligibility_status") or "") == "eligible"
        and (job.get("strong_match") or int(job.get("fit_score") or 0) >= 70)
    )


def _rank_score(job: dict[str, Any]) -> int:
    return int(job.get("recommendation_score") or job.get("fit_score") or 0)


def _parse_iso_day(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def filter_jobs(jobs: list[dict[str, Any]], list_name: str, today: date | None = None) -> list[dict[str, Any]]:
    current = today or date.today()
    if list_name == "top":
        jobs = [job for job in jobs if is_strong_match(job) and str(job.get("state") or "") not in {"closed", "skipped"}]
        return sorted(
            jobs,
            key=lambda item: (_rank_score(item), item.get("is_new", False), item.get("discovered_at", "")),
            reverse=True,
        )
    if list_name == "new":
        filtered = [job for job in jobs if job.get("is_new") and job.get("state") not in {"applied_by_user", "skipped", "closed"}]
        return sorted(filtered, key=lambda item: item.get("discovered_at", ""), reverse=True)
    if list_name == "pipeline":
        states = {"discovered", "matched", "saved", "needs_documents", "preparing", "ready_for_manual_apply", "ready_to_apply", "applied_by_user", "interview", "offer", "accepted"}
        return sorted(
            [job for job in jobs if job.get("state") in states],
            key=lambda item: (item.get("state"), _rank_score(item)),
            reverse=True,
        )
    if list_name == "ready":
        filtered = [
            job for job in jobs
            if job.get("actionability_state") == "actionable"
            and job.get("state") not in {"applied_by_user", "accepted", "rejected", "withdrawn", "skipped", "closed"}
        ]
        return sorted(filtered, key=lambda item: (_rank_score(item), item.get("closing_date", "")), reverse=True)
    if list_name == "preparing":
        filtered = [job for job in jobs if job.get("state") in {"preparing", "needs_documents"} or job.get("actionability_state") == "prep_needed"]
        return sorted(filtered, key=lambda item: (_rank_score(item), item.get("updated_at", "")), reverse=True)
    if list_name in {"saved", "applied", "interviews", "offers", "accepted", "rejected", "withdrawn"}:
        state_map = {
            "saved": {"saved"},
            "applied": {"applied_by_user"},
            "interviews": {"interview"},
            "offers": {"offer"},
            "accepted": {"accepted"},
            "rejected": {"rejected"},
            "withdrawn": {"withdrawn"},
        }
        filtered = [job for job in jobs if job.get("state") in state_map[list_name]]
        return sorted(filtered, key=lambda item: (item.get("updated_at", ""), _rank_score(item)), reverse=True)
    if list_name == "letters":
        filtered = [job for job in jobs if job.get("cover_letter_ready") or job.get("state") in {"matched", "saved", "ready_for_manual_apply"}]
        return sorted(filtered, key=lambda item: (item.get("cover_letter_ready", False), _rank_score(item)), reverse=True)
    if list_name == "public":
        filtered = [job for job in jobs if job.get("public_service")]
        return sorted(filtered, key=lambda item: (_rank_score(item), item.get("closing_date", "")), reverse=True)
    if list_name == "urgent":
        filtered = []
        for job in jobs:
            closing = parse_closing_date(job.get("closing_date"))
            if closing and current <= closing <= current + timedelta(days=7) and job.get("state") not in {"applied_by_user", "skipped", "closed"} and str(job.get("eligibility_status") or "") == "eligible":
                filtered.append(job)
        return sorted(filtered, key=lambda item: parse_closing_date(item.get("closing_date")) or date.max)
    if list_name == "stale":
        cutoff = current - timedelta(days=30)
        filtered = []
        for job in jobs:
            if job.get("state") in {"applied_by_user", "skipped", "closed"}:
                continue
            touched = _parse_iso_day(job.get("last_seen_at")) or _parse_iso_day(job.get("updated_at")) or _parse_iso_day(job.get("discovered_at"))
            if freshness_status(job) == "stale_unverified" or (touched and touched < cutoff):
                filtered.append(job)
        return sorted(filtered, key=lambda item: (_parse_iso_day(item.get("updated_at")) or date.min, _rank_score(item)))
    return jobs


def bot_status(repo, user_id: int, running_pid: int | None = None, today: date | None = None) -> dict[str, Any]:
    current = today or date.today()
    jobs = repo.list_jobs(user_id, include_hidden=True, today=current)
    sources = repo.list_sources(user_id)
    last_checked = sorted([str(item.get("last_checked") or "") for item in sources if item.get("last_checked")], reverse=True)
    return {
        "running_pid": running_pid,
        "counts": repo.job_counts(user_id, today=current),
        "source_stats": repo.source_stats(user_id),
        "source_errors": sum(1 for item in sources if str(item.get("last_result") or "").startswith("error:")),
        "source_warnings": sum(1 for item in sources if str(item.get("last_result") or "").startswith("warning:")),
        "last_scan_at": last_checked[0] if last_checked else "",
        "urgent_jobs": len(filter_jobs(jobs, "urgent", today=current)),
        "stale_jobs": len(filter_jobs(jobs, "stale", today=current)),
        "documents": len(repo.list_documents(user_id)),
        "cover_letters": len(repo.list_cover_letters(user_id)),
    }
