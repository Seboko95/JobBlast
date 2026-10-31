"""Resume parsing and document handling utilities."""

from __future__ import annotations

import uuid
import zipfile
import shutil
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from core.profile import merge_profile, profile_for_matching
from core.evidence import build_evidence_ledger, evidence_facts
from jobblast_ats import (
    build_answer_bank_defaults,
    classify_document_type,
    extract_document_tags,
    extract_pdf_text,
    parse_resume_text,
)
from storage.repository import Repository
from services.upload_validation import UploadValidationError, validate_upload


def resume_path(base_dir: Path, profile: dict[str, Any]) -> Path:
    root = base_dir.resolve()
    configured = str(profile.get("active_resume_path") or "").strip()
    if configured:
        path = (base_dir / configured).resolve()
        if root in path.parents and path.is_file():
            return path
    # Read-only compatibility for legacy single-user files. New uploads never use these names.
    for fallback in [base_dir / "uploaded_cv.pdf", base_dir / "uploaded_resume.pdf", base_dir / "uploaded_resume.docx"]:
        if fallback.is_file():
            return fallback.resolve()
    return base_dir / "__missing_resume__"


def migrate_legacy_storage(repo: Repository, user_id: int) -> dict[str, int]:
    """Copy legacy single-user files into scoped storage without deleting originals."""
    copied_resumes = 0
    copied_documents = 0
    profile = merge_profile(repo.get_profile(user_id))
    legacy_resume = next(
        (repo.base_dir / name for name in ["uploaded_cv.pdf", "uploaded_resume.pdf", "uploaded_resume.docx"] if (repo.base_dir / name).is_file()),
        None,
    )
    active_path = str(profile.get("active_resume_path") or "")
    if legacy_resume and (not active_path or "data/users/" not in active_path.replace("\\", "/")):
        target_dir = repo.base_dir / "data" / "users" / str(user_id) / "resumes"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"legacy-{legacy_resume.name}"
        if not target.exists():
            shutil.copy2(legacy_resume, target)
            copied_resumes += 1
        profile["active_resume_path"] = str(target.relative_to(repo.base_dir)).replace("\\", "/")
        repo.save_profile(user_id, profile)

    legacy_dir = repo.base_dir / "additional_docs"
    for document in repo.list_documents(user_id):
        filename = str(document.get("filename") or "")
        legacy_path = legacy_dir / Path(filename).name
        if not filename or "/" in filename or "\\" in filename or not legacy_path.is_file():
            continue
        target_dir = repo.base_dir / "data" / "users" / str(user_id) / "documents"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"legacy-{Path(filename).name}"
        if not target.exists():
            shutil.copy2(legacy_path, target)
            copied_documents += 1
        document["filename"] = str(target.relative_to(repo.base_dir)).replace("\\", "/")
        repo.save_document(user_id, document)
    return {"resumes": copied_resumes, "documents": copied_documents}


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_bytes)
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    return " ".join(part.strip() for part in texts if part and part.strip()).strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    return ""


def refresh_resume_profile(repo: Repository, user_id: int) -> tuple[bool, str, dict[str, Any]]:
    profile = repo.get_profile(user_id)
    path = resume_path(repo.base_dir, profile)
    if not path.exists():
        payload = {"parse_status": "missing", "parsed_at": "", "raw_text": "", "search_terms": []}
        repo.save_resume_profile(user_id, payload)
        return False, "No active resume file is available.", payload
    try:
        validated = validate_upload(path.name, path.read_bytes(), kind="resume")
        text = validated.text
    except Exception as exc:
        payload = {"parse_status": "failed", "parsed_at": "", "raw_text": "", "error": str(exc), "search_terms": []}
        repo.save_resume_profile(user_id, payload)
        return False, f"Resume parsing failed: {exc}", payload
    parsed = parse_resume_text(text, profile_for_matching(profile))
    answers = repo.get_answers(user_id)
    parsed["source_document_id"] = str(profile.get("active_resume_path") or "")
    parsed["source_reference"] = str(parsed.get("sha256") or path.name)
    parsed["evidence_ledger"] = build_evidence_ledger(profile, parsed, answers, repo.list_documents(user_id))
    parsed["evidence_facts"] = evidence_facts(profile, parsed, answers)
    repo.save_resume_profile(user_id, parsed)
    repo.replace_evidence_facts(user_id, parsed["evidence_ledger"])
    defaults = build_answer_bank_defaults(profile_for_matching(profile) | {"experience_years": parsed.get("experience_years") or ""})
    merged_answers = {**defaults, **answers}
    if parsed.get("experience_years"):
        merged_answers["experience_years"] = str(parsed["experience_years"])
    if parsed.get("locations"):
        merged_answers["current_location"] = parsed["locations"][0]
    repo.save_answers(user_id, merged_answers)
    return True, f"Resume parsed from {path.name}.", parsed


def save_resume_bytes(repo: Repository, user_id: int, filename: str, file_bytes: bytes) -> tuple[bool, str, dict[str, Any]]:
    try:
        validated = validate_upload(filename, file_bytes, kind="resume")
    except UploadValidationError as exc:
        return False, str(exc), {"parse_status": "rejected", "error": str(exc), "search_terms": []}
    profile = merge_profile(repo.get_profile(user_id))
    user_dir = repo.base_dir / "data" / "users" / str(user_id) / "resumes"
    user_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{uuid.uuid4().hex}{validated.extension}"
    target_path = user_dir / target_name
    target_path.write_bytes(file_bytes)
    profile["active_resume_path"] = str(target_path.relative_to(repo.base_dir)).replace("\\", "/")
    repo.save_profile(user_id, profile)
    ok, message, parsed = refresh_resume_profile(repo, user_id)
    parsed.update({"size_bytes": validated.size_bytes, "sha256": validated.sha256, "content_type": validated.content_type})
    return ok, message, parsed


def save_supporting_document(repo: Repository, user_id: int, filename: str, file_bytes: bytes) -> dict[str, Any]:
    validated = validate_upload(filename, file_bytes, kind="supporting")
    docs_dir = repo.base_dir / "data" / "users" / str(user_id) / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    extension = validated.extension
    document_id = str(uuid.uuid4())[:8]
    target_name = f"{document_id}{extension}"
    target_path = docs_dir / target_name
    target_path.write_bytes(file_bytes)
    parsed_text = validated.text
    payload = {
        "id": document_id,
        "original_name": filename,
        "filename": str(target_path.relative_to(repo.base_dir)).replace("\\", "/"),
        "doc_type": classify_document_type(filename, parsed_text),
        "active": True,
        "tags": extract_document_tags(filename, parsed_text),
        "parsed_text": parsed_text[:4000],
        "size_bytes": validated.size_bytes,
        "sha256": validated.sha256,
        "content_type": validated.content_type,
        "page_count": validated.page_count,
        "added_at": "",
    }
    repo.save_document(user_id, payload)
    profile = repo.get_profile(user_id)
    repo.replace_evidence_facts(
        user_id,
        build_evidence_ledger(profile, repo.get_resume_profile(user_id), repo.get_answers(user_id), repo.list_documents(user_id)),
    )
    return payload


def resume_status(repo: Repository, user_id: int) -> str:
    profile = repo.get_profile(user_id)
    parsed = repo.get_resume_profile(user_id)
    path = resume_path(repo.base_dir, profile)
    if not path.exists():
        return "Missing"
    if parsed.get("parse_status") == "parsed":
        search_terms = len(parsed.get("search_terms", []))
        return f"Parsed ({search_terms} search terms)"
    if parsed.get("parse_status") == "failed":
        return "Present, parse failed"
    return f"Present ({path.name})"


def document_summary(repo: Repository, user_id: int) -> str:
    docs = repo.list_documents(user_id)
    if not docs:
        return "No supporting documents"
    counts: dict[str, int] = {}
    for doc in docs:
        if not doc.get("active", True):
            continue
        counts[doc.get("doc_type", "supporting_doc")] = counts.get(doc.get("doc_type", "supporting_doc"), 0) + 1
    active_total = sum(counts.values())
    return (
        f"{active_total} active | cert {counts.get('certificate', 0)} | "
        f"license {counts.get('license', 0)} | portfolio {counts.get('portfolio', 0)} | "
        f"support {counts.get('supporting_doc', 0)} | cover {counts.get('cover_letter', 0)}"
    )
