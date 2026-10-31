"""Formatting helpers for professional cover letter rendering."""

from __future__ import annotations

from datetime import date
from typing import Any


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def cover_letter_subject(job: dict[str, Any]) -> str:
    title = _clean(job.get("title") or "the role")
    return f"Subject: Application for {title} Position"


def cover_letter_hiring_manager(job: dict[str, Any]) -> str:
    for key in ("hiring_manager_name", "contact_name", "contact_person", "recruiter_name"):
        value = _clean(job.get(key))
        if value:
            return value
    return ""


def cover_letter_salutation(job: dict[str, Any]) -> str:
    manager = cover_letter_hiring_manager(job)
    return f"Dear {manager}," if manager else "Dear Hiring Manager,"


def cover_letter_signoff(job: dict[str, Any]) -> str:
    return "Yours sincerely," if cover_letter_hiring_manager(job) else "Yours faithfully,"


def candidate_address_lines(profile: dict[str, Any]) -> list[str]:
    lines = [
        _clean(profile.get("name")),
        _clean(profile.get("address_line")),
        _clean(profile.get("city_province_postal")) or _clean((profile.get("preferred_locations") or [""])[0]),
        _clean(profile.get("phone")),
        _clean(profile.get("email")),
    ]
    return [line for line in lines if line]


def company_address_lines(job: dict[str, Any]) -> list[str]:
    lines = [
        cover_letter_hiring_manager(job),
        _clean(job.get("company")),
        _clean(job.get("company_address")),
        _clean(job.get("company_city_province_postal")) or _clean(job.get("location")),
    ]
    return [line for line in lines if line]


def cover_letter_body_paragraphs(package: dict[str, Any]) -> list[str]:
    body = str(package.get("cover_letter") or "").strip()
    if not body:
        return []
    paragraphs = [paragraph.strip() for paragraph in body.split("\n\n") if paragraph.strip()]
    if paragraphs:
        return paragraphs
    return [line.strip() for line in body.splitlines() if line.strip()]


def cover_letter_date(today: date | None = None) -> str:
    current = today or date.today()
    return current.strftime("%d %B %Y")


def formatted_cover_letter_text(profile: dict[str, Any], job: dict[str, Any], package: dict[str, Any], today: date | None = None) -> str:
    lines: list[str] = []
    lines.extend(candidate_address_lines(profile))
    lines.extend(
        [
            "",
            cover_letter_date(today),
            "",
        ]
    )
    company_lines = company_address_lines(job)
    if company_lines:
        lines.extend(company_lines)
        lines.append("")
    lines.extend(
        [
            cover_letter_subject(job),
            "",
            cover_letter_salutation(job),
            "",
        ]
    )
    lines.extend(cover_letter_body_paragraphs(package))
    lines.extend(
        [
            "",
            cover_letter_signoff(job),
            "",
        ]
    )
    name = _clean(profile.get("name"))
    if name:
        lines.append(name)
    return "\n".join(lines).strip()
