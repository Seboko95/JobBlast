"""Deadline and follow-up reminder helpers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.jobs import parse_closing_date


ACTIVE_DEADLINE_STATES = {"matched", "saved", "needs_documents", "preparing", "ready_for_manual_apply", "ready_to_apply"}
FOLLOW_UP_STATE = "applied_by_user"


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if "T" in raw:
        candidates.append(raw.split("T", 1)[0])
    for candidate in candidates:
        for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(candidate, pattern).date()
            except ValueError:
                continue
    return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=parsed.tzinfo) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _business_days_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    current = start
    total = 0
    while current < end:
        current = current.fromordinal(current.toordinal() + 1)
        if current.weekday() < 5:
            total += 1
    return total


def _ensure_flags(job: dict[str, Any]) -> dict[str, str]:
    existing = job.get("reminder_flags") or {}
    if isinstance(existing, dict):
        return {str(key): str(value) for key, value in existing.items()}
    return {}


def _fmt_date(value: date) -> str:
    return value.strftime("%d %b %Y")


def collect_job_reminders(repo, user_id: int, today: date | None = None) -> dict[str, list[str]]:
    current = today or date.today()
    deadline_lines: list[str] = []
    followup_lines: list[str] = []
    interview_lines: list[str] = []
    outcome_lines: list[str] = []
    changed_jobs: list[dict[str, Any]] = []
    jobs = repo.list_jobs(user_id, include_hidden=True, today=current)

    for job in jobs:
        state = str(job.get("state") or "")
        flags = _ensure_flags(job)
        changed = False

        if state in ACTIVE_DEADLINE_STATES:
            closing = parse_closing_date(job.get("closing_date"))
            if closing is not None:
                days_left = (closing - current).days
                deadline_rules = {
                    3: ("deadline_3d", f"{job.get('title', 'Job')} at {job.get('company', 'Unknown')} closes in 3 days on {_fmt_date(closing)}."),
                    1: ("deadline_1d", f"{job.get('title', 'Job')} at {job.get('company', 'Unknown')} closes tomorrow on {_fmt_date(closing)}."),
                    0: ("deadline_0d", f"{job.get('title', 'Job')} at {job.get('company', 'Unknown')} closes today ({_fmt_date(closing)})."),
                }
                if days_left in deadline_rules:
                    flag, message = deadline_rules[days_left]
                    if flag not in flags:
                        flags[flag] = current.isoformat()
                        deadline_lines.append(message)
                        changed = True
                if days_left in {0, 1} and job.get("actionability_state") == "prep_needed" and "prep_urgent" not in flags:
                    flags["prep_urgent"] = current.isoformat()
                    deadline_lines.append(f"{job.get('title', 'Job')} needs preparation before its closing date.")
                    changed = True

        if state == FOLLOW_UP_STATE:
            applied = _parse_iso_date(job.get("applied_at"))
            if applied is not None:
                business_days = _business_days_between(applied, current)
                follow_rules = {
                    5: ("followup_5d", f"{job.get('title', 'Job')} at {job.get('company', 'Unknown')} was marked applied 5 business days ago. Consider a follow-up."),
                    10: ("followup_10d", f"{job.get('title', 'Job')} at {job.get('company', 'Unknown')} was marked applied 10 business days ago. If appropriate, send a final follow-up."),
                }
                for threshold, (flag, message) in follow_rules.items():
                    if business_days >= threshold and flag not in flags:
                        flags[flag] = current.isoformat()
                        followup_lines.append(message)
                        changed = True

        interview_at = _parse_iso_datetime(job.get("interview_at"))
        if interview_at is not None and (interview_at.date() - current).days in {0, 1} and "interview_reminder" not in flags:
            flags["interview_reminder"] = current.isoformat()
            interview_lines.append(f"Interview reminder: {job.get('title', 'Job')} is scheduled for {interview_at.strftime('%d %b %Y %H:%M')}.")
            changed = True

        if state == "offer" and "offer_recorded" not in flags:
            flags["offer_recorded"] = current.isoformat()
            outcome_lines.append(f"Offer recorded for {job.get('title', 'Job')}. Review the offer details and decision deadline.")
            changed = True

        if changed:
            job["reminder_flags"] = flags
            changed_jobs.append(job)

    for job in changed_jobs:
        repo.save_job(user_id, job)

    return {"deadlines": deadline_lines, "follow_ups": followup_lines, "interviews": interview_lines, "outcomes": outcome_lines}
