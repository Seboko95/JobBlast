"""Public-service and Z83 helpers for manual applications."""

from __future__ import annotations

from pathlib import Path

import httpx

from jobblast_ats import summarize_application_requirements

PUBLIC_Z83_EDITABLE_URL = "https://jobs.gauteng.gov.za/Docs/Editable%20Z83.pdf"
PUBLIC_Z83_GUIDE_URL = (
    "https://www.labour.gov.za/DocumentCenter/Publications/Public%20Employment%20Services/"
    "Tips%20on%20how%20to%20complete%20Z83%20application%20form%20for%20employment%20in%20Public%20Service.pdf"
)


async def ensure_z83_assets(base_dir: Path) -> dict[str, Path]:
    public_dir = base_dir / "Documents" / "Public_Service"
    public_dir.mkdir(parents=True, exist_ok=True)
    assets = {
        "editable_z83": (PUBLIC_Z83_EDITABLE_URL, public_dir / "Editable_Z83.pdf"),
        "z83_guide": (PUBLIC_Z83_GUIDE_URL, public_dir / "Z83_Guide.pdf"),
    }
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        for url, path in assets.values():
            if path.exists() and path.stat().st_size > 10000:
                continue
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            path.write_bytes(response.content)
    return {key: path for key, (_, path) in assets.items()}


def public_service_checklist(job: dict) -> list[str]:
    requirements = dict(job.get("application_requirements") or {})
    checklist = ["Detailed CV"]
    if requirements.get("z83_required"):
        checklist.append("Completed Z83 form")
    if requirements.get("signed_z83_required"):
        checklist.append("Signed application form")
    if requirements.get("certified_docs_shortlisted_only"):
        checklist.append("Certified copies usually only if shortlisted")
    if requirements.get("contact_email"):
        checklist.append(f"Contact email: {requirements['contact_email']}")
    if requirements.get("contact_phone"):
        checklist.append(f"Contact phone: {requirements['contact_phone']}")
    return checklist


def public_service_text(job: dict) -> str:
    requirements = dict(job.get("application_requirements") or {})
    lines = [
        f"{job.get('title', 'Public Service Job')}",
        f"Department: {job.get('department') or job.get('company') or 'Unknown'}",
    ]
    if job.get("reference_number"):
        lines.append(f"Reference: {job['reference_number']}")
    if job.get("centre") or job.get("location"):
        lines.append(f"Centre: {job.get('centre') or job.get('location')}")
    if job.get("province"):
        lines.append(f"Province: {job['province']}")
    if job.get("closing_date"):
        closing = job["closing_date"]
        if job.get("closing_time"):
            closing += f" {job['closing_time']}"
        lines.append(f"Closing date/time: {closing}")
    if job.get("salary_package"):
        lines.append(f"Package: {job['salary_package']}")
    if job.get("salary_level"):
        lines.append(f"Salary level: {job['salary_level']}")
    if job.get("circular"):
        lines.append(f"Circular: {job['circular']}")
    if job.get("submission_instructions"):
        lines.extend(["", "Submission instructions:", job["submission_instructions"]])
    summary = summarize_application_requirements(requirements)
    if summary:
        lines.extend(["", "How to apply:", summary])
    checklist = public_service_checklist(job)
    if checklist:
        lines.extend(["", "Checklist:"] + [f"- {item}" for item in checklist])
    return "\n".join(lines)
