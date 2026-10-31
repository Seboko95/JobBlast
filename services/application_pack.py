"""Export a manual-apply application pack for a selected job."""

from __future__ import annotations

from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from services.cover_letter_docs import export_cover_letter_docx
from services.cover_letter_format import formatted_cover_letter_text
from services.gap_analysis import gap_analysis_text
from services.public_service import public_service_text


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _filename_base(job: dict[str, Any]) -> str:
    title = "".join(char if char.isalnum() else "_" for char in _clean(job.get("title") or "application_pack")).strip("_")
    company = "".join(char if char.isalnum() else "_" for char in _clean(job.get("company") or "company")).strip("_")
    return f"{title[:36] or 'application'}_{company[:24] or 'company'}"


def _how_to_apply_text(job: dict[str, Any]) -> str:
    notes = list(job.get("notes") or [])
    missing = list(job.get("missing_docs") or [])
    lines = [
        f"How to apply for {job.get('title', 'this job')}",
        "",
        f"Application method: {job.get('application_method_label', 'Check the vacancy link')}",
        f"Apply link: {job.get('apply_url') or job.get('listing_url') or ''}",
        f"Recommended next action: {job.get('recommended_next_action', 'Review the vacancy and apply manually.')}",
    ]
    if missing:
        lines.extend(["", "Missing documents:"] + [f"- {item}" for item in missing])
    if notes:
        lines.extend(["", "Preparation notes:"] + [f"- {item}" for item in notes])
    return "\n".join(lines)


def _answers_text(job: dict[str, Any], package: dict[str, Any]) -> str:
    lines = [
        f"Application answers for {job.get('title', 'this job')}",
        "",
        "Suggested answers:",
    ]
    lines.extend(f"- {item}" for item in package.get("suggested_answers", []))
    lines.extend(["", "CV alignment notes:"])
    lines.extend(f"- {item}" for item in package.get("cv_alignment_notes", []))
    return "\n".join(lines)


def _job_summary_text(job: dict[str, Any]) -> str:
    required_docs = list(job.get("required_docs") or [])
    why_match = list(job.get("why_match") or [])
    lines = [
        f"Title: {job.get('title', 'Untitled role')}",
        f"Company: {job.get('company', 'Unknown')}",
        f"Location: {job.get('location') or 'Not stated'}",
        f"Source: {job.get('source_name', 'Unknown')} ({job.get('portal_type', 'generic')})",
        f"Closing date: {job.get('closing_date') or 'Not stated'}",
        f"Match score: {job.get('fit_score', 0)}",
        f"Apply link: {job.get('apply_url') or job.get('listing_url') or ''}",
        "",
        "Why it matches:",
    ]
    lines.extend(f"- {item}" for item in why_match)
    lines.extend(["", "Required documents:"])
    lines.extend(f"- {item}" for item in required_docs)
    return "\n".join(lines)


def _preparation_text(job: dict[str, Any]) -> str:
    plan = dict(job.get("preparation_plan") or {})
    lines = [f"Preparation plan for {job.get('title', 'this job')}", "", f"Status: {plan.get('status', job.get('actionability_state', 'unknown'))}"]
    for heading, key in [("Blocking items", "blocking_items"), ("Required preparation", "preparation_items"), ("Needs confirmation", "confirmation_items"), ("Manual items", "manual_items")]:
        values = plan.get(key) or []
        if values:
            lines.extend(["", heading + ":"] + [f"- {item}" for item in values])
    return "\n".join(lines)


def _evidence_audit_text(package: dict[str, Any]) -> str:
    validation = dict(package.get("claim_validation") or {})
    lines = ["Evidence and generated-claim audit", "", f"Validation: {'PASS' if validation.get('valid') else 'REVIEW REQUIRED'}"]
    if validation.get("issues"):
        lines.extend(["", "Issues:"] + [f"- {item}" for item in validation["issues"]])
    for claim in validation.get("claims") or []:
        lines.append(f"- {claim.get('status')}: {claim.get('text')}")
    return "\n".join(lines)


def export_application_pack(profile: dict[str, Any], job: dict[str, Any], package: dict[str, Any]) -> tuple[str, bytes]:
    cover_filename, cover_docx = export_cover_letter_docx(profile, job, package)
    letter_text = formatted_cover_letter_text(profile, job, package)
    pack_name = f"{_filename_base(job)}_application_pack.zip"
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("01_job_summary.txt", _job_summary_text(job))
        archive.writestr("02_how_to_apply.txt", _how_to_apply_text(job))
        archive.writestr("03_cover_letter.txt", letter_text)
        archive.writestr(f"04_{cover_filename}", cover_docx)
        archive.writestr("05_application_answers.txt", _answers_text(job, package))
        archive.writestr("06_gap_analysis.txt", gap_analysis_text(job, package))
        archive.writestr("07_preparation_plan.txt", _preparation_text(job))
        archive.writestr("08_evidence_audit.txt", _evidence_audit_text(package))
        if job.get("public_service"):
            archive.writestr("09_public_service_notes.txt", public_service_text(job))
    return pack_name, buffer.getvalue()
