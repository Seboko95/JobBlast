"""Pipeline export helpers."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from core.jobs import state_label


EXPORT_COLUMNS = [
    "id",
    "title",
    "company",
    "location",
    "state",
    "state_label",
    "fit_score",
    "recommendation_score",
    "actionability_state",
    "actionability_reason",
    "closing_date",
    "source_name",
    "application_method",
    "apply_url",
    "public_service",
    "cover_letter_ready",
    "applied_at",
    "recommended_next_action",
]


def export_jobs_csv(jobs: list[dict[str, Any]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        writer.writerow(
            {
                "id": job.get("id", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "state": job.get("state", ""),
                "state_label": state_label(job),
                "fit_score": job.get("fit_score", 0),
                "recommendation_score": job.get("recommendation_score", job.get("fit_score", 0)),
                "actionability_state": job.get("actionability_state", "not_actionable"),
                "actionability_reason": job.get("actionability_reason", ""),
                "closing_date": job.get("closing_date", ""),
                "source_name": job.get("source_name", ""),
                "application_method": job.get("application_method_label", ""),
                "apply_url": job.get("apply_url") or job.get("listing_url", ""),
                "public_service": bool(job.get("public_service")),
                "cover_letter_ready": bool(job.get("cover_letter_ready")),
                "applied_at": job.get("applied_at", ""),
                "recommended_next_action": job.get("recommended_next_action", ""),
            }
        )
    return buffer.getvalue().encode("utf-8-sig")
