"""Track and summarize scanner source health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cooldown_active(source: dict[str, Any], now: datetime | None = None) -> bool:
    until = _parse_iso(source.get("cooldown_until"))
    return bool(until and until > (now or datetime.now(timezone.utc)))


def source_health_status(source: dict[str, Any]) -> str:
    if cooldown_active(source):
        return "cooldown"
    if source.get("last_error"):
        return "error"
    if source.get("last_warning"):
        return "warning"
    if int(source.get("consecutive_zero_results") or 0) >= 3:
        return "low_yield"
    if int(source.get("scan_count") or 0) <= 1:
        return "new"
    return "healthy"


def update_source_health(
    source: dict[str, Any],
    matched: int,
    new: int,
    error: str = "",
    warning: str = "",
    failure_category: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    now = _now_iso()
    source["last_attempt"] = now
    source["last_duration_ms"] = int(duration_ms or source.get("last_duration_ms") or 0)
    source["scan_count"] = int(source.get("scan_count") or 0) + 1
    source["last_matched_jobs"] = int(matched)
    source["last_new_jobs"] = int(new)
    source["total_matched_jobs"] = int(source.get("total_matched_jobs") or 0) + int(matched)
    source["total_new_jobs"] = int(source.get("total_new_jobs") or 0) + int(new)
    source["last_warning"] = warning or ""
    source["last_failure_category"] = failure_category if (error or warning) else ""
    if error or warning:
        source["consecutive_failures"] = int(source.get("consecutive_failures") or 0) + 1
        source["consecutive_zero_results"] = int(source.get("consecutive_zero_results") or 0) + (1 if matched == 0 else 0)
        source["cooldown_until"] = (_parse_iso(now) + timedelta(hours=1)).isoformat().replace("+00:00", "Z") if source["consecutive_failures"] >= 3 else ""
        source["last_error"] = error or ""
        if error:
            source["error_count"] = int(source.get("error_count") or 0) + 1
        elif warning:
            source["warning_count"] = int(source.get("warning_count") or 0) + 1
    else:
        source["last_error"] = ""
        source["last_warning"] = ""
        source["last_failure_category"] = ""
        source["last_success"] = now
        source["consecutive_failures"] = 0
        source["cooldown_until"] = ""
        source["success_count"] = int(source.get("success_count") or 0) + 1
        source["consecutive_zero_results"] = int(source.get("consecutive_zero_results") or 0) + 1 if matched == 0 else 0
    source["health_status"] = source_health_status(source)
    return source


def source_health_badge(source: dict[str, Any]) -> str:
    mapping = {
        "healthy": "Healthy",
        "warning": "Warning",
        "error": "Error",
        "low_yield": "Low Yield",
        "cooldown": "Cooldown",
        "new": "New",
    }
    return mapping.get(source_health_status(source), "Unknown")
