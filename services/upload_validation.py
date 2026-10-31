"""Bounded, content-aware validation for user-uploaded documents."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_EXTRACTED_TEXT = 200_000
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_MEMBERS = 200


class UploadValidationError(ValueError):
    """Raised when an upload is unsafe or cannot be parsed reliably."""


@dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    content_type: str
    text: str
    page_count: int | None
    sha256: str
    size_bytes: int


def _docx_text(file_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UploadValidationError("The DOCX file is missing its required document parts.")
            xml_bytes = archive.read("word/document.xml")
    except UploadValidationError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise UploadValidationError("The DOCX file is malformed.") from exc

    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise UploadValidationError("The DOCX document XML is malformed.") from exc
    text = " ".join(
        node.text.strip()
        for node in root.iter()
        if node.tag.endswith("}t") and node.text and node.text.strip()
    ).strip()
    if len(text) > MAX_EXTRACTED_TEXT:
        raise UploadValidationError("The document contains too much extracted text.")
    return text


def validate_upload(filename: str, file_bytes: bytes, *, kind: str) -> ValidatedUpload:
    """Validate actual content before any filesystem or database write."""
    if not isinstance(file_bytes, (bytes, bytearray)):
        raise UploadValidationError("The uploaded file could not be read safely.")
    payload = bytes(file_bytes)
    if not payload:
        raise UploadValidationError("The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise UploadValidationError("The uploaded file is larger than the 10 MiB limit.")

    extension = Path(str(filename or "")).suffix.lower()
    allowed = {".pdf", ".docx"} if kind == "resume" else {".pdf", ".docx", ".txt"} if kind == "supporting" else set()
    if not allowed:
        raise UploadValidationError("Unsupported upload category.")
    if extension not in allowed:
        raise UploadValidationError(f"Unsupported file type: {extension or 'unknown'}.")

    text = ""
    page_count: int | None = None
    if extension == ".pdf":
        if not payload.startswith(b"%PDF-"):
            raise UploadValidationError("The file extension does not match a valid PDF.")
        try:
            reader = PdfReader(io.BytesIO(payload), strict=True)
            if reader.is_encrypted:
                raise UploadValidationError("Encrypted PDFs are not supported.")
            page_count = len(reader.pages)
            if page_count > MAX_PDF_PAGES:
                raise UploadValidationError("The PDF has too many pages.")
            chunks: list[str] = []
            total = 0
            for page in reader.pages:
                chunk = page.extract_text() or ""
                total += len(chunk)
                if total > MAX_EXTRACTED_TEXT:
                    raise UploadValidationError("The document contains too much extracted text.")
                chunks.append(chunk)
            text = "\n".join(chunks).strip()
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError("The PDF is malformed or could not be parsed safely.") from exc
        content_type = "application/pdf"
    elif extension == ".docx":
        if not payload.startswith(b"PK"):
            raise UploadValidationError("The file extension does not match a valid DOCX archive.")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = archive.infolist()
                if len(members) > MAX_DOCX_MEMBERS:
                    raise UploadValidationError("The DOCX archive contains too many files.")
                if sum(info.file_size for info in members) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise UploadValidationError("The DOCX archive is too large when decompressed.")
            text = _docx_text(payload)
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError("The DOCX file is malformed or could not be parsed safely.") from exc
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadValidationError("Text supporting documents must be valid UTF-8.") from exc
        if len(text) > MAX_EXTRACTED_TEXT:
            raise UploadValidationError("The document contains too much text.")
        content_type = "text/plain; charset=utf-8"

    return ValidatedUpload(
        extension=extension,
        content_type=content_type,
        text=text,
        page_count=page_count,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
