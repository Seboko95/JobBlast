"""FastAPI backend for the JobBlast Android companion app."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from core.profile import merge_profile
from core.personas import personas_for_profile, select_persona, upsert_persona
from services.application_pack import export_application_pack
from services.cover_letter_docs import export_cover_letter_docx
from services.cover_letter_format import formatted_cover_letter_text
from services.cover_letters import generate_cover_package
from services.documents import document_summary, migrate_legacy_storage, resume_status, save_resume_bytes, save_supporting_document
from services.upload_validation import MAX_UPLOAD_BYTES, UploadValidationError
from services.gap_analysis import gap_analysis_text
from services.job_views import bot_status, filter_jobs
from services.ranking_evaluation import evaluate_cases
from services.pipeline_export import export_jobs_csv
from services.scanner import run_scan
from storage.repository import Repository

JobListName = Literal["top", "new", "pipeline", "public", "letters", "urgent", "stale"]


class AppContext:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        load_dotenv(base_dir / ".env")
        self.repo = Repository(base_dir)
        self.api_token = os.getenv("JOBBLAST_API_TOKEN", "").strip()
        configured_user = os.getenv("JOBBLAST_API_USER_ID", "").strip()
        self.user_id = int(configured_user) if configured_user else self._default_user_id()
        if self.user_id > 0:
            migrate_legacy_storage(self.repo, self.user_id)

    def _default_user_id(self) -> int:
        user_ids = self.repo.list_user_ids()
        return user_ids[0] if len(user_ids) == 1 else 0


class ProfileUpdate(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    enabled: bool


class FeedbackPayload(BaseModel):
    label: Literal["relevant", "not_for_me"]
    reason: str = ""


class StatePayload(BaseModel):
    state: Literal[
        "saved", "preparing", "ready_to_apply", "ready_for_manual_apply",
        "applied_by_user", "interview", "offer", "accepted", "rejected", "withdrawn", "skipped",
    ]


class PersonaPayload(BaseModel):
    id: str
    name: str
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    blocked_employers: list[str] = Field(default_factory=list)


class JobActionResponse(BaseModel):
    job: dict[str, Any]


class TextResponse(BaseModel):
    text: str
    package: dict[str, Any] | None = None


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, MAX_UPLOAD_BYTES + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The uploaded file is larger than the 10 MiB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(base_dir: Path | None = None) -> FastAPI:
    context = AppContext(base_dir or Path(__file__).resolve().parent.parent)
    app = FastAPI(title="JobBlast API", version="0.1.0")
    app.state.context = context

    @app.middleware("http")
    async def reject_oversized_upload_requests(request: Request, call_next):
        if request.url.path in {"/v1/resume", "/v1/documents"}:
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length else 0
            except ValueError:
                content_length = 0
            # Multipart framing adds a small amount around the bounded file payload.
            if content_length > MAX_UPLOAD_BYTES + 256 * 1024:
                return JSONResponse(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, content={"detail": "The uploaded file is larger than the 10 MiB limit."})
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            item.strip()
            for item in os.getenv(
                "JOBBLAST_API_ALLOWED_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000",
            ).split(",")
            if item.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_auth(authorization: str | None = Header(default=None)) -> AppContext:
        if not context.api_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="JOBBLAST_API_TOKEN is not configured.",
            )
        if context.user_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="A single JOBBLAST_API_USER_ID must be configured for the private API.",
            )
        expected = f"Bearer {context.api_token}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token.")
        return context

    def ensure_job(repo: Repository, user_id: int, job_id: str) -> dict[str, Any]:
        job = repo.get_job(user_id, job_id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return job

    async def ensure_cover_package(repo: Repository, user_id: int, job: dict[str, Any]) -> dict[str, Any]:
        package = repo.get_cover_letter(user_id, job["id"])
        if package:
            return package
        profile = repo.get_profile(user_id)
        answers = repo.get_answers(user_id)
        resume_profile = repo.get_resume_profile(user_id)
        package = await generate_cover_package(profile, resume_profile, answers, job)
        repo.save_cover_letter(user_id, job["id"], package)
        return package

    def filtered_jobs(repo: Repository, user_id: int, list_name: JobListName) -> list[dict[str, Any]]:
        return filter_jobs(repo.list_jobs(user_id, include_hidden=False), list_name)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "jobblast-api"}

    @app.get("/v1/home")
    async def home(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        repo = ctx.repo
        user_id = ctx.user_id
        return {
            "user_id": user_id,
            "profile": repo.get_profile(user_id),
            "counts": repo.job_counts(user_id),
            "source_stats": repo.source_stats(user_id),
            "resume_status": resume_status(repo, user_id),
            "document_summary": document_summary(repo, user_id),
        }

    @app.get("/v1/status")
    async def status_report(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return bot_status(ctx.repo, ctx.user_id)

    @app.get("/v1/scans")
    async def scan_history(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"scans": ctx.repo.list_scan_runs(ctx.user_id)}

    @app.get("/v1/evaluation")
    async def ranking_evaluation(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"evaluation": evaluate_cases()}

    @app.get("/v1/profile")
    async def get_profile(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"profile": ctx.repo.get_profile(ctx.user_id)}

    @app.get("/v1/personas")
    async def personas(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        profile = ctx.repo.get_profile(ctx.user_id)
        return {"active_persona_id": profile.get("active_persona_id", "default"), "personas": personas_for_profile(profile)}

    @app.post("/v1/personas")
    async def create_persona(payload: PersonaPayload, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        profile = ctx.repo.get_profile(ctx.user_id)
        updated = upsert_persona(profile, payload.model_dump())
        ctx.repo.save_profile(ctx.user_id, updated)
        return {"personas": personas_for_profile(updated)}

    @app.post("/v1/personas/{persona_id}/activate")
    async def activate_persona(persona_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        profile = ctx.repo.get_profile(ctx.user_id)
        selected = select_persona(profile, persona_id)
        if not selected:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Persona not found.")
        profile["active_persona_id"] = str(selected["id"])
        ctx.repo.save_profile(ctx.user_id, profile)
        return {"active_persona_id": selected["id"], "persona": selected}

    @app.put("/v1/profile")
    async def update_profile(payload: ProfileUpdate, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        current = ctx.repo.get_profile(ctx.user_id)
        updated = merge_profile({**current, **payload.profile})
        ctx.repo.save_profile(ctx.user_id, updated)
        return {"profile": updated}

    @app.get("/v1/jobs")
    async def list_jobs(list_name: JobListName = "top", ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"jobs": filtered_jobs(ctx.repo, ctx.user_id, list_name)}

    @app.get("/v1/jobs/export.csv")
    async def export_jobs(ctx: AppContext = Depends(require_auth)) -> Response:
        payload = export_jobs_csv(filter_jobs(ctx.repo.list_jobs(ctx.user_id, include_hidden=True), "pipeline"))
        return Response(
            content=payload,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="jobblast_pipeline.csv"'},
        )

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"job": ensure_job(ctx.repo, ctx.user_id, job_id)}

    @app.post("/v1/jobs/{job_id}/save", response_model=JobActionResponse)
    async def save_job(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ctx.repo.update_job_state(ctx.user_id, job_id, "saved")
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return {"job": job}

    @app.post("/v1/jobs/{job_id}/skip", response_model=JobActionResponse)
    async def skip_job(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ctx.repo.update_job_state(ctx.user_id, job_id, "skipped")
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return {"job": job}

    @app.post("/v1/jobs/{job_id}/mark-applied", response_model=JobActionResponse)
    async def mark_applied(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        applied_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        job = ctx.repo.update_job_state(ctx.user_id, job_id, "applied_by_user", applied_at=applied_at)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return {"job": job}

    @app.post("/v1/jobs/{job_id}/feedback")
    async def feedback(job_id: str, payload: FeedbackPayload, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        ensure_job(ctx.repo, ctx.user_id, job_id)
        return {"feedback": ctx.repo.save_feedback(ctx.user_id, job_id, payload.label, payload.reason)}

    @app.post("/v1/jobs/{job_id}/state", response_model=JobActionResponse)
    async def update_state(job_id: str, payload: StatePayload, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ctx.repo.update_job_state(ctx.user_id, job_id, payload.state)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return {"job": job}

    @app.delete("/v1/jobs/{job_id}")
    async def remove_job(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        removed = ctx.repo.delete_job(ctx.user_id, job_id)
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return {"removed": True}

    @app.post("/v1/scan")
    async def scan(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        result = await run_scan(ctx.repo, ctx.user_id)
        return {
            "new_jobs": len(result["new_jobs"]),
            "reconciled_jobs": len(result.get("reconciled_jobs", [])),
            "persisted_jobs": len(result.get("persisted_jobs", [])),
            "eligible_jobs": result.get("eligible_count", 0),
            "strong_matches": result.get("strong_match_count", 0),
            "public_matches": result.get("public_matches", 0),
            "new_public_matches": result.get("new_public_matches", 0),
            "errors": result["errors"],
            "warnings": result.get("warnings", []),
        }

    @app.post("/v1/jobs/{job_id}/cover-letter", response_model=TextResponse)
    async def cover_letter(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ensure_job(ctx.repo, ctx.user_id, job_id)
        package = await ensure_cover_package(ctx.repo, ctx.user_id, job)
        text = formatted_cover_letter_text(ctx.repo.get_profile(ctx.user_id), job, package)
        return {"text": text, "package": package}

    @app.post("/v1/jobs/{job_id}/answers", response_model=TextResponse)
    async def answers(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ensure_job(ctx.repo, ctx.user_id, job_id)
        package = await ensure_cover_package(ctx.repo, ctx.user_id, job)
        lines = [f"Application notes for {job.get('title', 'this job')}", "", "Suggested answers:"]
        lines.extend(f"- {item}" for item in package.get("suggested_answers", []))
        lines.extend(["", "CV alignment notes:"])
        lines.extend(f"- {item}" for item in package.get("cv_alignment_notes", []))
        return {"text": "\n".join(lines), "package": package}

    @app.post("/v1/jobs/{job_id}/gap-analysis", response_model=TextResponse)
    async def gap_analysis(job_id: str, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        job = ensure_job(ctx.repo, ctx.user_id, job_id)
        package = await ensure_cover_package(ctx.repo, ctx.user_id, job)
        return {"text": gap_analysis_text(job, package), "package": package}

    @app.get("/v1/jobs/{job_id}/cover-letter.docx")
    async def cover_letter_docx(job_id: str, ctx: AppContext = Depends(require_auth)) -> Response:
        job = ensure_job(ctx.repo, ctx.user_id, job_id)
        package = await ensure_cover_package(ctx.repo, ctx.user_id, job)
        filename, payload = export_cover_letter_docx(ctx.repo.get_profile(ctx.user_id), job, package)
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/v1/jobs/{job_id}/application-pack.zip")
    async def application_pack(job_id: str, ctx: AppContext = Depends(require_auth)) -> Response:
        job = ensure_job(ctx.repo, ctx.user_id, job_id)
        package = await ensure_cover_package(ctx.repo, ctx.user_id, job)
        filename, payload = export_application_pack(ctx.repo.get_profile(ctx.user_id), job, package)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/v1/documents")
    async def documents(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {
            "resume_status": resume_status(ctx.repo, ctx.user_id),
            "document_summary": document_summary(ctx.repo, ctx.user_id),
            "documents": ctx.repo.list_documents(ctx.user_id),
        }

    @app.post("/v1/resume")
    async def upload_resume(file: UploadFile = File(...), ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        payload = await _read_upload(file)
        ok, message, parsed = save_resume_bytes(ctx.repo, ctx.user_id, file.filename or "resume.pdf", payload)
        if not ok and parsed.get("parse_status") == "rejected":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)
        return {"ok": ok, "message": message, "parsed": parsed, "profile": ctx.repo.get_profile(ctx.user_id)}

    @app.post("/v1/documents")
    async def upload_document(file: UploadFile = File(...), ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        payload = await _read_upload(file)
        try:
            document = save_supporting_document(ctx.repo, ctx.user_id, file.filename or "document.pdf", payload)
        except UploadValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return {"document": document, "document_summary": document_summary(ctx.repo, ctx.user_id)}

    @app.get("/v1/sources")
    async def sources(ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        return {"sources": ctx.repo.list_sources(ctx.user_id), "stats": ctx.repo.source_stats(ctx.user_id)}

    @app.put("/v1/sources/{source_id}")
    async def update_source(source_id: str, payload: SourceUpdate, ctx: AppContext = Depends(require_auth)) -> dict[str, Any]:
        source = ctx.repo.get_source(ctx.user_id, source_id)
        if not source:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
        source["enabled"] = payload.enabled
        ctx.repo.save_source(ctx.user_id, source)
        return {"source": source}

    return app


app = create_app()
