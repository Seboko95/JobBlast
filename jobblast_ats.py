import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from core.feedback import feedback_score_adjustment

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_SOURCE_DEFS = [
    {"name": "LinkedIn Jobs", "kind": "linkedin_search", "seed_url": "https://www.linkedin.com/jobs/search"},
    {"name": "Gauteng Professional Job Centre", "kind": "public_jobs_portal", "seed_url": "https://jobs.gauteng.gov.za/Public/Jobs.aspx"},
    {"name": "Cipla South Africa", "kind": "company_portal", "seed_url": "https://careers.cipla.com/"},
    {"name": "Accord Healthcare", "kind": "company_portal", "seed_url": "https://www.accordhealthcare.co.za/"},
    {"name": "MENARINI Group", "kind": "company_portal", "seed_url": "https://www.menarini.com/en-us/careers"},
    {"name": "Reckitt", "kind": "company_portal", "seed_url": "https://careers.reckitt.com/"},
    {"name": "MNA Recruitment", "kind": "company_portal", "seed_url": "https://www.mnarecruitment.com/jobs/"},
    {"name": "Aspen Pharmacare", "kind": "company_portal", "seed_url": "https://www.aspenpharma.com/careers/"},
    {"name": "Adcock Ingram", "kind": "company_portal", "seed_url": "https://www.adcock.com/careers"},
    {"name": "Sanofi", "kind": "company_portal", "seed_url": "https://jobs.sanofi.com/en/"},
    {"name": "Novartis", "kind": "company_portal", "seed_url": "https://www.novartis.com/careers/career-search"},
    {"name": "Johnson & Johnson", "kind": "company_portal", "seed_url": "https://careers.jnj.com/en/"},
    {"name": "Indeed South Africa", "kind": "aggregator_search", "seed_url": "https://za.indeed.com/jobs"},
    {"name": "PNet", "kind": "aggregator_search", "seed_url": "https://www.pnet.co.za/jobs/"},
    {"name": "Careers24", "kind": "aggregator_search", "seed_url": "https://www.careers24.com/jobs/lc-gauteng/q-medical-representative/"},
    {"name": "Roche Careers", "kind": "company_portal", "seed_url": "https://careers.roche.com/southafrica"},
    {"name": "Bayer Careers", "kind": "company_portal", "seed_url": "https://jobs.bayer.com/"},
    {"name": "GSK Careers", "kind": "company_portal", "seed_url": "https://www.gsk.com/en-gb/careers/"},
    {"name": "Abbott Careers", "kind": "company_portal", "seed_url": "https://www.abbott.com/careers.html"},
]

SOURCE_URL_ALIASES = {
    "https://www.accordhealthcare.co.za/": ["https://www.accordhealth.co.za/"],
    "https://www.mnarecruitment.com/jobs/": ["https://www.mnarecruitment.com/"],
    "https://careers.jnj.com/en/": ["https://www.careers.jnj.com/en/", "https://www.careers.jnj.com/en/jobs/"],
}

SOURCE_NAME_ALIASES = {
    "accord healthcare": ["https://www.accordhealth.co.za/"],
    "mna recruitment": ["https://www.mnarecruitment.com/"],
    "johnson & johnson": ["https://www.careers.jnj.com/en/", "https://www.careers.jnj.com/en/jobs/"],
    "roche careers": ["https://careers.roche.com/southafrica"],
    "bayer careers": ["https://jobs.bayer.com/"],
    "gsk careers": ["https://www.gsk.com/en-gb/careers/"],
    "abbott careers": ["https://www.abbott.com/careers.html"],
}

PHARMA_KEYWORDS = [
    "pharmaceutical",
    "pharma",
    "sales representative",
    "medical representative",
    "medical rep",
    "territory",
    "hospital representative",
    "key accounts",
    "detail representative",
    "detailing",
    "pharmacist",
    "otc",
    "consumer health",
    "fmcg",
    "wellness",
]

ROLE_KEYWORDS = [
    "sales rep",
    "sales representative",
    "medical representative",
    "medical sales",
    "territory manager",
    "territory sales",
    "product specialist",
    "key account",
    "business development",
    "pharmacist assistant",
]

DOC_TYPES = {"resume", "certificate", "license", "portfolio", "cover_letter", "supporting_doc"}
PUBLIC_SERVICE_CONTACT_HINTS = [
    "applications should be submitted strictly online",
    "new z83 form",
    "public service department",
    "no hand-delivered",
    "no hand delivered",
    "faxed",
]
PUBLIC_SERVICE_METHOD_LABELS = {
    "online_portal": "Online portal",
    "email": "Email application",
    "hand_delivery": "Hand delivery",
    "post": "Posted application",
    "mixed": "Multiple methods",
    "unknown": "Check job notes",
}
LOCATION_HINTS = [
    "johannesburg", "gauteng", "pretoria", "midrand", "sandton", "east rand",
    "cape town", "durban", "south africa",
]
SKILL_HINTS = [
    "pharmaceutical sales",
    "customer relationship management",
    "otc",
    "self-medication",
    "fmcg",
    "patient counselling",
    "quality assurance",
    "negotiation",
    "selling skills",
    "unisolv",
    "on-base",
    "ms office",
    "inventory",
    "consumer advisory",
]
NEGATIVE_ROLE_HINTS = [
    "receptionist",
    "admin clerk",
    "data capturer",
    "cashier",
    "call centre",
    "waiter",
]


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip(" ,.;:-")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _split_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if re.sub(r"\s+", " ", line).strip()]


def _strip_bullets(value: str) -> str:
    return value.lstrip("?-*•\uf0b7 ").strip()


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts).strip()


def classify_document_type(filename: str, parsed_text: str = "") -> str:
    lower = f"{filename} {parsed_text[:2000]}".lower()
    if any(token in lower for token in ["curriculum vitae", "resume", " cv ", "_cv", "-cv", "resume_"]):
        return "resume"
    if "cover letter" in lower or ("cover" in lower and "letter" in lower):
        return "cover_letter"
    if any(token in lower for token in ["certificate", "certification", "first aid", "sapc"]):
        return "certificate"
    if any(token in lower for token in ["license", "licence", "driver", "drivers"]):
        return "license"
    if any(token in lower for token in ["portfolio", "work sample", "samples"]):
        return "portfolio"
    return "supporting_doc"


def extract_document_tags(filename: str, parsed_text: str = "") -> list[str]:
    lower = f"{filename} {parsed_text[:4000]}".lower()
    tags: list[str] = []
    for token in ["sapc", "first aid", "code 10", "driver", "otc", "pharmacy", "sales", "portfolio"]:
        if token in lower:
            tags.append(token.replace(" ", "_"))
    return _uniq(tags)


def infer_upload_field_intent(label: str, name: str = "", accept: str = "") -> str:
    lower = f"{label} {name} {accept}".lower()
    if any(token in lower for token in ["resume", "cv", "curriculum vitae"]):
        return "resume"
    if "cover letter" in lower:
        return "cover_letter"
    if any(token in lower for token in ["certificate", "certification"]):
        return "certificate"
    if any(token in lower for token in ["license", "licence", "driver"]):
        return "license"
    if any(token in lower for token in ["portfolio", "work sample"]):
        return "portfolio"
    if any(token in lower for token in ["supporting", "attachment", "additional", "other document", "documents"]):
        return "supporting_doc"
    return "unknown"


def document_matches_intent(doc_type: str, intent: str) -> bool:
    if intent == "supporting_doc":
        return doc_type in {"supporting_doc", "certificate", "license", "portfolio"}
    return doc_type == intent


def parse_resume_text(text: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or {}
    lines = _split_lines(text)
    lower = text.lower()

    full_name = lines[0] if lines else profile.get("name", "")
    headline = ""
    for line in lines[1:6]:
        if "|" in line or any(token in line.lower() for token in ROLE_KEYWORDS):
            headline = line
            break
    if not headline:
        headline = profile.get("job_title", "")

    emails = _uniq(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I))
    phones = _uniq(re.findall(r"(?:\+?\d[\d\s\-]{7,}\d)", text))

    locations: list[str] = []
    for line in lines[:20]:
        if any(loc in line.lower() for loc in LOCATION_HINTS):
            locations.append(line)
    locations = _uniq(locations or [profile.get("location", "")])

    target_roles: list[str] = []
    role_patterns = [
        r"aspiring\s+([A-Za-z /-]{5,80})",
        r"seeking to transition into(?: a| an)?\s+([A-Za-z /-]{5,80})\s+role",
        r"target role[:\s]+([A-Za-z /-]{5,80})",
    ]
    for pattern in role_patterns:
        for match in re.findall(pattern, text, re.I):
            target_roles.append(match)
    if headline:
        target_roles.extend([part for part in re.split(r"\||/|,", headline) if any(token in part.lower() for token in ROLE_KEYWORDS)])
    target_roles.append(profile.get("job_title", ""))
    target_roles.extend([part.strip() for part in str(profile.get("search_keywords", "")).split(",") if part.strip()])
    target_roles = _uniq([role.title() for role in target_roles if role])

    skills: list[str] = []
    skill_section = re.search(r"key competencies(.*?)(employment history|education|licenses|licences|certifications)", lower, re.S)
    if skill_section:
        raw_section = text[skill_section.start():skill_section.end()]
        for line in _split_lines(raw_section):
            if len(line) < 3:
                continue
            if "key competencies" in line.lower():
                continue
            skills.append(_strip_bullets(line))
    for hint in SKILL_HINTS:
        if hint in lower:
            skills.append(hint)
    skills = _uniq(skills)

    industries: list[str] = []
    for label, tokens in {
        "pharmaceutical": ["pharmaceutical", "pharma"],
        "healthcare": ["healthcare", "patient"],
        "retail pharmacy": ["pharmacy", "dispensary"],
        "fmcg": ["fmcg"],
        "consumer health": ["consumer health", "otc"],
    }.items():
        if any(token in lower for token in tokens):
            industries.append(label)

    companies = _uniq(re.findall(r"-\s*([A-Z][A-Za-z& .'-]+),", text))
    if not companies:
        for line in lines:
            if "clicks group" in line.lower():
                companies.append("Clicks Group")
    companies = _uniq(companies)

    experience_years = 0
    year_matches = re.findall(r"(\d+)\+?\s+years", lower)
    if year_matches:
        experience_years = max(int(value) for value in year_matches)

    education: list[str] = []
    education_section = re.search(r"education(.*?)(licenses|licences|certifications|technical skills|skills|$)", lower, re.S)
    if education_section:
        education = _uniq([line for line in _split_lines(text[education_section.start():education_section.end()]) if "education" not in line.lower()])

    certifications: list[str] = []
    cert_section = re.search(r"(licenses and certifications|licenses|licences|certifications)(.*?)(technical skills|skills|$)", lower, re.S)
    if cert_section:
        certifications = _uniq([_strip_bullets(line) for line in _split_lines(text[cert_section.start():cert_section.end()]) if not re.match(r"^(licenses|licences|certifications)", line, re.I)])

    licenses = _uniq([line for line in certifications if any(token in line.lower() for token in ["license", "licence", "driver", "sapc"])])

    languages: list[str] = []
    lang_match = re.search(r"(multilingual|languages?)[:\s]+([A-Za-z, /-]+)", text, re.I)
    if lang_match:
        languages.extend(re.split(r",|/|\|", lang_match.group(2)))
    languages = _uniq([lang.strip().title() for lang in languages if lang.strip()])

    parsed = {
        "parse_status": "parsed" if text.strip() else "missing",
        "parsed_at": utcnow_iso(),
        "raw_text": text,
        "full_name": full_name,
        "headline": headline,
        "emails": emails,
        "phones": phones,
        "locations": locations,
        "target_roles": target_roles,
        "skills": skills,
        "industries": _uniq(industries),
        "companies": companies,
        "experience_years": experience_years,
        "education": education,
        "licenses": licenses,
        "certifications": certifications,
        "languages": languages,
    }
    parsed["search_terms"] = derive_search_terms(parsed, profile)
    return parsed


def derive_search_terms(resume_profile: dict[str, Any] | None, profile: dict[str, Any] | None = None) -> list[str]:
    profile = profile or {}
    resume_profile = resume_profile or {}
    terms: list[str] = []
    base_roles = [
        "medical representative",
        "pharmaceutical sales representative",
        "sales representative",
        "product specialist",
        "territory manager",
    ]
    terms.extend(base_roles)
    terms.extend(resume_profile.get("target_roles", []))
    terms.extend([item.strip() for item in str(profile.get("search_keywords", "")).split(",") if item.strip()])
    lower_text = str(resume_profile.get("raw_text", "")).lower()
    if "sapc" in lower_text:
        terms.append("sapc representative")
    if any(token in lower_text for token in ["otc", "consumer health"]):
        terms.append("consumer health sales")
    if "fmcg" in lower_text:
        terms.append("fmcg sales")
    if "pharmacist assistant" in lower_text:
        terms.append("pharmacist assistant")
    return _uniq([term.title() for term in terms if term])[:10]


def build_candidate_profile(
    profile: dict[str, Any],
    answer_bank: dict[str, Any] | None = None,
    resume_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    answer_bank = answer_bank or {}
    resume_profile = resume_profile or {}
    target_roles = _uniq(
        [profile.get("job_title", "")]
        + resume_profile.get("target_roles", [])
        + [item.strip() for item in str(profile.get("search_keywords", "")).split(",") if item.strip()]
    )
    skills = _uniq(
        resume_profile.get("skills", [])
        + [item.strip() for item in str(profile.get("search_keywords", "")).split(",") if item.strip()]
    )
    certifications = _uniq(resume_profile.get("certifications", []))
    licenses = _uniq(resume_profile.get("licenses", []) + ([answer_bank.get("drivers_license")] if answer_bank.get("drivers_license") else []))
    locations = _uniq(resume_profile.get("locations", []) + [profile.get("location", ""), answer_bank.get("current_location", "")])
    search_terms = derive_search_terms(resume_profile, profile)
    return {
        **profile,
        "answer_bank": answer_bank,
        "resume_profile": resume_profile,
        "target_roles": target_roles,
        "skills": skills,
        "certifications": certifications,
        "licenses": licenses,
        "locations": locations,
        "search_terms": search_terms,
        "search_keywords": profile.get("search_keywords", ""),
    }


def json_dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def http_get(url: str, timeout: int = 20, *, raise_for_status: bool = True) -> httpx.Response:
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            if raise_for_status:
                response.raise_for_status()
            return response
    except Exception:
        logger.warning("Secure request failed for %s", url, exc_info=True)
        raise


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def extract_urls(text: str) -> list[str]:
    return [match.rstrip(".,);]") for match in re.findall(r"https?://[^\s|<>\"']+", str(text or ""))]


def normalize_source_url(value: str) -> str:
    raw = str(value or "").strip()
    urls = extract_urls(raw)
    url = urls[0] if urls else raw
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return raw


def validate_source_input(name: str, seed_url: str) -> tuple[str, str]:
    clean_name = str(name or "").strip()
    urls = extract_urls(seed_url)
    if not clean_name:
        raise ValueError("Source name is required.")
    if not urls:
        raise ValueError("Provide one valid source URL.")
    if len(urls) > 1:
        raise ValueError("Add one source URL at a time.")
    url = normalize_source_url(urls[0])
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Source URL is not valid.")
    return clean_name, url


def classify_source_issue(message: str) -> str:
    lower = str(message or "").lower()
    soft_markers = [
        "403 forbidden",
        "404 not found",
        "500 internal server error",
        "security check",
        "certificate verify failed",
        "hostname mismatch",
        "getaddrinfo failed",
        "timed out",
        "connection reset",
        "temporarily unavailable",
        "too many requests",
    ]
    return "warning" if any(marker in lower for marker in soft_markers) else "error"


def classify_source_failure(message: str) -> str:
    lower = str(message or "").lower()
    if any(marker in lower for marker in ["certificate verify failed", "hostname mismatch", "ssl", "tls"]):
        return "tls"
    if any(marker in lower for marker in ["timed out", "timeout", "readtimeout"]):
        return "timeout"
    if any(marker in lower for marker in ["403 forbidden", "http 403", "status code 403"]):
        return "http_403"
    if any(marker in lower for marker in ["404 not found", "http 404", "status code 404"]):
        return "http_404"
    if any(marker in lower for marker in ["429", "too many requests"]):
        return "http_429"
    if re.search(r"(?:http|status code|client error)?\s*['\"]?5\d\d\b", lower):
        return "http_5xx"
    if any(marker in lower for marker in ["parse", "parser", "json decode", "malformed html"]):
        return "parser"
    if any(marker in lower for marker in ["connection", "network", "getaddrinfo", "dns"]):
        return "network"
    return "unknown"


def hard_exclusion_keyword(title: str, description: str, profile: dict[str, Any]) -> str | None:
    haystack = f"{title} {description}".lower()
    for keyword in profile.get("excluded_keywords", []) or []:
        normalized = str(keyword or "").strip().lower()
        if normalized and normalized in haystack:
            return normalized
    return None


def source_candidate_urls(source: dict[str, Any]) -> list[str]:
    seed_url = normalize_source_url(source.get("seed_url") or "")
    candidates: list[str] = [seed_url] if seed_url else []
    candidates.extend(SOURCE_URL_ALIASES.get(seed_url, []))
    candidates.extend(SOURCE_NAME_ALIASES.get(str(source.get("name", "")).strip().lower(), []))
    if seed_url:
        parsed = urlparse(seed_url)
        root = f"{parsed.scheme}://{parsed.netloc}/"
        if parsed.path not in {"", "/"}:
            candidates.append(root)
            trimmed_path = parsed.path.rstrip("/")
            if "/" in trimmed_path:
                parent = trimmed_path.rsplit("/", 1)[0] + "/"
                candidates.append(f"{parsed.scheme}://{parsed.netloc}{parent}")
        if parsed.query:
            candidates.append(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    return _uniq([candidate for candidate in candidates if candidate.startswith("http")])


def infer_portal_type(url: str, html: str = "") -> str:
    haystack = f"{url} {html[:2000]}".lower()
    host = urlparse(url).netloc.lower()
    if "linkedin.com" in host:
        return "linkedin"
    if "greenhouse.io" in host or "boards.greenhouse.io" in host or "job-boards.greenhouse.io" in haystack:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "workday" in haystack or "myworkdayjobs.com" in host:
        return "workday"
    if "ashbyhq.com" in host:
        return "ashby"
    if "simplify.hr" in host:
        return "simplify"
    if "successfactors" in haystack:
        return "successfactors"
    if "icims.com" in host:
        return "icims"
    if "jobs.gauteng.gov.za" in host or "professionaljobcentre.gpg.gov.za" in host:
        return "generic"
    return "generic"


def _default_scan_mode(kind: str) -> str:
    if kind == "company_portal":
        return "assist_apply"
    if kind == "public_jobs_portal":
        return "prepare"
    return "discovery"


def build_source(name: str, seed_url: str, kind: str) -> dict[str, Any]:
    clean_name, clean_url = validate_source_input(name, seed_url)
    return {
        "id": str(uuid.uuid4())[:8],
        "name": clean_name,
        "kind": kind,
        "seed_url": clean_url,
        "portal_type": infer_portal_type(clean_url),
        "enabled": True,
        "schedule_enabled": True,
        "scan_mode": _default_scan_mode(kind),
        "last_checked": None,
        "last_result": "",
        "last_error": "",
        "last_warning": "",
        "health_status": "new",
        "scan_count": 0,
        "success_count": 0,
        "warning_count": 0,
        "error_count": 0,
        "consecutive_failures": 0,
        "consecutive_zero_results": 0,
        "total_new_jobs": 0,
        "total_matched_jobs": 0,
        "last_new_jobs": 0,
        "last_matched_jobs": 0,
        "last_duration_ms": 0,
        "last_attempt": None,
        "last_success": None,
        "last_failure_category": "",
        "cooldown_until": "",
        "added_at": utcnow_iso(),
    }


def default_sources() -> list[dict[str, Any]]:
    return [build_source(item["name"], item["seed_url"], item["kind"]) for item in DEFAULT_SOURCE_DEFS]


def sanitize_sources(raw_sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    for raw in raw_sources or []:
        if not isinstance(raw, dict):
            continue
        seed_url = normalize_source_url(raw.get("seed_url") or raw.get("url") or "")
        name = str(raw.get("name") or "").strip()
        kind = str(raw.get("kind") or "company_portal").strip()
        parsed = urlparse(seed_url)
        if (
            not name
            or not seed_url.startswith("http")
            or not parsed.scheme
            or not parsed.netloc
            or " " in parsed.netloc
            or not re.match(r"^[a-z0-9.-]+$", parsed.netloc.lower())
        ):
            continue
        dedupe_key = seed_url.lower().rstrip("/")
        identity_key = (kind.lower(), name.lower())
        if dedupe_key in seen_urls:
            continue
        if kind in {"company_portal", "aggregator_search"} and identity_key in seen_identities:
            continue
        seen_urls.add(dedupe_key)
        seen_identities.add(identity_key)
        clean.append(
            {
                "id": str(raw.get("id") or str(uuid.uuid4())[:8]),
                "name": name,
                "kind": kind,
                "seed_url": seed_url,
                "portal_type": str(raw.get("portal_type") or infer_portal_type(seed_url)),
                "enabled": bool(raw.get("enabled", True)),
                "schedule_enabled": bool(raw.get("schedule_enabled", True)),
                "scan_mode": str(raw.get("scan_mode") or _default_scan_mode(kind)),
                "last_checked": raw.get("last_checked"),
                "last_result": str(raw.get("last_result") or ""),
                "last_error": str(raw.get("last_error") or ""),
                "last_warning": str(raw.get("last_warning") or ""),
                "health_status": str(raw.get("health_status") or "new"),
                "scan_count": int(raw.get("scan_count") or 0),
                "success_count": int(raw.get("success_count") or 0),
                "warning_count": int(raw.get("warning_count") or 0),
                "error_count": int(raw.get("error_count") or 0),
                "consecutive_failures": int(raw.get("consecutive_failures") or 0),
                "consecutive_zero_results": int(raw.get("consecutive_zero_results") or 0),
                "total_new_jobs": int(raw.get("total_new_jobs") or 0),
                "total_matched_jobs": int(raw.get("total_matched_jobs") or 0),
                "last_new_jobs": int(raw.get("last_new_jobs") or 0),
                "last_matched_jobs": int(raw.get("last_matched_jobs") or 0),
                "last_duration_ms": int(raw.get("last_duration_ms") or 0),
                "last_attempt": raw.get("last_attempt"),
                "last_success": raw.get("last_success"),
                "last_failure_category": str(raw.get("last_failure_category") or ""),
                "cooldown_until": str(raw.get("cooldown_until") or ""),
                "added_at": str(raw.get("added_at") or utcnow_iso()),
            }
        )
    existing_urls = {item["seed_url"].lower().rstrip("/") for item in clean}
    existing_identities = {
        (str(item.get("kind") or "").lower(), str(item.get("name") or "").lower())
        for item in clean
        if str(item.get("kind") or "") in {"company_portal", "aggregator_search"}
    }
    for source in default_sources():
        dedupe_key = source["seed_url"].lower().rstrip("/")
        identity_key = (str(source.get("kind") or "").lower(), str(source.get("name") or "").lower())
        if dedupe_key in existing_urls or (
            str(source.get("kind") or "") in {"company_portal", "aggregator_search"} and identity_key in existing_identities
        ):
            continue
        clean.append(source)
        existing_urls.add(dedupe_key)
        existing_identities.add(identity_key)
    return clean


def normalize_job(job: dict[str, Any], source: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    from core.trust import assess_listing_trust
    from core.intelligence import match_snapshot
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or source["name"]).strip()
    apply_url = str(job.get("apply_url") or job.get("url") or source["seed_url"]).strip()
    listing_url = str(job.get("listing_url") or apply_url).strip()
    description = str(job.get("description") or "").strip()
    location = str(job.get("location") or profile.get("location") or "").strip()
    portal_type = str(job.get("portal_type") or infer_portal_type(apply_url, description))
    external_id = str(job.get("external_id") or slugify(f"{company}-{title}-{apply_url}")[:120])
    legacy_score = score_job_fit(
        title,
        description,
        profile,
        {"portal_type": portal_type, "source_name": source["name"], "company": company},
    )
    excluded_keyword = hard_exclusion_keyword(title, description, profile)
    structured_snapshot = match_snapshot(
        {"title": title, "description": description, "location": location, "company": company},
        profile,
    )
    fit_score = structured_snapshot["overall_score"] if structured_snapshot["eligibility"]["eligible"] else 0
    trust = assess_listing_trust({"title": title, "description": description, "apply_url": apply_url, "portal_type": portal_type, "source_kind": source.get("kind")}, source)
    return {
        "id": external_id,
        "source_id": source["id"],
        "source_name": source["name"],
        "source_kind": source["kind"],
        "company": company,
        "department": str(job.get("department") or company).strip(),
        "title": title,
        "location": location,
        "portal_type": portal_type,
        "listing_url": listing_url,
        "apply_url": apply_url,
        "description": description[:4000],
        "fit_score": fit_score,
        "legacy_fit_score": legacy_score,
        "match_snapshot": structured_snapshot,
        "eligible": excluded_keyword is None,
        "eligibility_status": "eligible" if excluded_keyword is None else "excluded",
        "eligibility_reasons": [] if excluded_keyword is None else [f"excluded_keyword:{excluded_keyword}"],
        **trust,
        "last_verified": "",
        "reference_number": str(job.get("reference_number") or "").strip(),
        "closing_date": str(job.get("closing_date") or "").strip(),
        "salary_package": str(job.get("salary_package") or "").strip(),
        "criteria_questions": list(job.get("criteria_questions") or []),
        "application_requirements": dict(job.get("application_requirements") or {}),
        "public_service": bool(job.get("public_service") or source.get("kind") == "public_jobs_portal"),
        "discovered_at": utcnow_iso(),
        "scan_mode": source.get("scan_mode", "discovery"),
        "status": "discovered",
    }


def score_job_fit(title: str, description: str, profile: dict[str, Any], job_meta: dict[str, Any] | None = None) -> int:
    if hard_exclusion_keyword(title, description, profile):
        return 0
    haystack = f"{title} {description}".lower()
    score = 0
    for token in [t.lower() for t in profile.get("target_roles", []) if t]:
        if token in haystack:
            score += 24
    for token in [t.lower() for t in profile.get("search_terms", []) if t]:
        if token in haystack:
            score += 10
    for token in [t.lower() for t in profile.get("skills", []) if t]:
        if token in haystack:
            score += 6
    for token in [t.lower() for t in profile.get("certifications", []) if t]:
        if token in haystack:
            score += 8
    for token in [t.lower() for t in profile.get("licenses", []) if t]:
        if token in haystack:
            score += 8
    for token in ROLE_KEYWORDS:
        if token in haystack:
            score += 12
    pharma_hits = 0
    for token in PHARMA_KEYWORDS:
        if token in haystack:
            pharma_hits += 1
            score += 6
    for location in [item.lower() for item in profile.get("locations", []) if item]:
        if location in haystack:
            score += 5
    if any(token in haystack for token in NEGATIVE_ROLE_HINTS) and pharma_hits == 0:
        score -= 18
    if "medical representative" in title.lower() or "pharmaceutical sales" in title.lower():
        score += 10
    score += feedback_score_adjustment(title, description, profile, job_meta)
    return max(0, min(score, 100))


def supports_assisted_apply(portal_type: str) -> bool:
    return portal_type in {
        "greenhouse",
        "lever",
        "smartrecruiters",
        "workday",
        "ashby",
        "successfactors",
        "icims",
        "simplify",
        "generic",
        "linkedin",
    }


def summarize_application_requirements(requirements: dict[str, Any] | None) -> str:
    requirements = requirements or {}
    parts: list[str] = []
    method = str(requirements.get("application_method") or "unknown")
    if method in PUBLIC_SERVICE_METHOD_LABELS:
        parts.append(PUBLIC_SERVICE_METHOD_LABELS[method])
    if requirements.get("z83_required"):
        parts.append("signed Z83 required")
    if requirements.get("detailed_cv_required"):
        parts.append("detailed CV required")
    if requirements.get("certified_docs_shortlisted_only"):
        parts.append("certified copies only when shortlisted")
    contact_email = str(requirements.get("contact_email") or "").strip()
    if contact_email:
        parts.append(f"contact {contact_email}")
    return "; ".join(parts)


def _extract_emails(text: str) -> list[str]:
    return _uniq(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I))


def _extract_phone_numbers(text: str) -> list[str]:
    return _uniq(re.findall(r"(?:\+?\d[\d\s\-()]{7,}\d)", text))


def extract_public_service_application_requirements(text: str, apply_url: str = "") -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    lower = normalized.lower()

    def phrase_is_negated(phrase: str) -> bool:
        idx = lower.find(phrase)
        if idx < 0:
            return False
        prefix = lower[max(0, idx - 40):idx]
        return "no " in prefix or "not " in prefix

    disallow_hand_delivery = any(token in lower for token in ["no hand-delivered", "no hand delivered"]) or phrase_is_negated("hand-delivered")
    disallow_email = any(token in lower for token in ["no emailed applications", "no emailed application", "no emailed"]) or phrase_is_negated("emailed applications")
    disallow_post = any(token in lower for token in ["no posted applications", "no postal applications"]) or phrase_is_negated("posted applications")
    method_flags = {
        "online_portal": any(token in lower for token in ["submitted strictly online", "online applications", "e-recruitment portal", "apply online", "online application"]),
        "email": any(token in lower for token in ["email applications", "emailed applications", "email your application", "applications must be emailed"]) and not disallow_email,
        "hand_delivery": any(token in lower for token in ["hand-delivered", "hand delivered", "deliver to", "walk in applications"]) and not disallow_hand_delivery,
        "post": any(token in lower for token in ["posted applications", "post to", "mail applications", "applications must be posted"]) and not disallow_post,
    }
    methods = [label for label, enabled in method_flags.items() if enabled]
    if len(methods) > 1:
        application_method = "mixed"
    elif methods:
        application_method = methods[0]
    else:
        application_method = "unknown"

    portal_match = re.search(r"(https?://[^\s)]+)", normalized)
    contact_emails = _extract_emails(normalized)
    contact_phones = _extract_phone_numbers(normalized)
    z83_required = "z83" in lower
    detailed_cv_required = any(token in lower for token in ["detailed cv", "curriculum vitae", "cv will be disqualified", "cv will be disqualif"])
    certified_docs_shortlisted_only = any(
        token in lower for token in ["only when shortlisted", "on or before the day of the interview", "certified copies", "when shortlisted"]
    )
    signed_required = any(token in lower for token in ["signed new z83", "fully completed and signed", "signed by the applicant"])
    notes_summary: list[str] = []
    if application_method == "online_portal":
        notes_summary.append("Apply through the department's online recruitment portal.")
    elif application_method == "email":
        notes_summary.append("Submit the application by email.")
    elif application_method == "hand_delivery":
        notes_summary.append("Submit the application by hand delivery.")
    elif application_method == "post":
        notes_summary.append("Submit the application by post.")
    if z83_required:
        notes_summary.append("A new Z83 form is required.")
    if signed_required:
        notes_summary.append("The Z83 must be fully completed and signed.")
    if detailed_cv_required:
        notes_summary.append("Include a detailed CV.")
    if certified_docs_shortlisted_only:
        notes_summary.append("Certified documents are generally requested only from shortlisted candidates.")
    return {
        "public_service": any(token in lower for token in PUBLIC_SERVICE_CONTACT_HINTS) or z83_required,
        "application_method": application_method,
        "application_portal_url": portal_match.group(1).rstrip(".,);") if portal_match else (apply_url if application_method == "online_portal" else ""),
        "z83_required": z83_required,
        "signed_z83_required": signed_required,
        "detailed_cv_required": detailed_cv_required,
        "certified_docs_shortlisted_only": certified_docs_shortlisted_only,
        "contact_email": contact_emails[0] if contact_emails else "",
        "contact_phone": contact_phones[0] if contact_phones else "",
        "summary": " ".join(notes_summary).strip(),
    }


def build_answer_bank_defaults(profile: dict[str, Any]) -> dict[str, str]:
    return {
        "full_name": profile.get("name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "linkedin_url": profile.get("linkedin") or "",
        "current_location": profile.get("location", ""),
        "current_title": profile.get("job_title", ""),
        "work_authorization": "South African citizen",
        "notice_period": "Immediate to 30 days",
        "drivers_license": "Yes - Code 10 driver's licence",
        "willing_to_travel": "Yes",
        "salary_expectation": "Negotiable based on the role",
        "sapc_registration": "SAPC Registered Pharmacist Assistant (P64933)",
        "experience_years": str(profile.get("experience_years") or ""),
    }


async def _search_query_variants(
    search_fn,
    queries: list[str],
    location: str,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for query in queries:
        try:
            jobs.extend(await search_fn(query, location))
        except Exception as exc:
            errors.append(f"{query}: {exc}")
            continue
    if not jobs and errors:
        raise RuntimeError("; ".join(errors[:3]))
    return dedupe_jobs(jobs)


async def search_linkedin_jobs(keywords: str, location: str) -> list[dict[str, Any]]:
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={quote(keywords)}&location={quote(location)}&start=0"
    )
    jobs: list[dict[str, Any]] = []
    resp = await http_get(url, timeout=25)
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("li"):
        link = card.select_one("a.base-card__full-link, a")
        title = card.select_one("h3")
        company = card.select_one("h4")
        location_tag = card.select_one(".job-search-card__location")
        if not link or not title:
            continue
        href = link.get("href", "").strip()
        if not href:
            continue
        jobs.append(
            {
                "external_id": slugify(href.split("?")[0]),
                "title": title.get_text(" ", strip=True),
                "company": company.get_text(" ", strip=True) if company else "LinkedIn",
                "location": location_tag.get_text(" ", strip=True) if location_tag else location,
                "listing_url": href,
                "apply_url": href,
                "portal_type": "linkedin",
                "description": card.get_text(" ", strip=True)[:800],
            }
        )
    return jobs


async def search_indeed_jobs(keywords: str, location: str) -> list[dict[str, Any]]:
    url = f"https://za.indeed.com/jobs?q={quote(keywords)}&l={quote(location)}"
    jobs: list[dict[str, Any]] = []
    resp = await http_get(url, raise_for_status=False)
    if resp.status_code >= 400 and "security check" in resp.text.lower():
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("div.job_seen_beacon, div.slider_container, div.cardOutline"):
        link = card.select_one("a[href*='/viewjob']")
        title = card.select_one("h2, .jobTitle")
        company = card.select_one("[data-testid='company-name'], .companyName")
        location_tag = card.select_one("[data-testid='text-location'], .companyLocation")
        if not link or not title:
            continue
        href = urljoin("https://za.indeed.com", link.get("href", "").strip())
        if href:
            jobs.append(
                {
                    "external_id": slugify(href),
                    "title": title.get_text(" ", strip=True),
                    "company": company.get_text(" ", strip=True) if company else "Indeed",
                    "location": location_tag.get_text(" ", strip=True) if location_tag else location,
                    "listing_url": href,
                    "apply_url": href,
                    "portal_type": infer_portal_type(href),
                    "description": card.get_text(" ", strip=True)[:800],
                }
            )
    return jobs


def _extract_xml_text(xml_fragment: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", xml_fragment, re.S)
    return BeautifulSoup(match.group(1), "html.parser").get_text(" ", strip=True) if match else ""


async def search_pnet_jobs(keywords: str, location: str) -> list[dict[str, Any]]:
    url = f"https://www.pnet.co.za/jobs.html?ke={quote(keywords)}&ws={quote(location)}"
    resp = await http_get(url, raise_for_status=False)
    if resp.status_code >= 400 and "security" in resp.text.lower():
        return []
    return _extract_jobs_from_listing_html(resp.text, str(resp.url), company_hint="PNet", location=location)


async def search_careers24_jobs(keywords: str, location: str) -> list[dict[str, Any]]:
    slug_keywords = slugify(keywords)
    slug_location = slugify(location or "south-africa")
    url = f"https://www.careers24.com/jobs/lc-{slug_location}/q-{slug_keywords}/"
    resp = await http_get(url, raise_for_status=False)
    if resp.status_code >= 400:
        return []
    return _extract_jobs_from_listing_html(resp.text, str(resp.url), company_hint="Careers24", location=location)


def _gauteng_detail_text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def _extract_gauteng_criteria_questions(soup: BeautifulSoup) -> list[str]:
    questions: list[str] = []
    grid = soup.select_one("#body_GridCriteria")
    if not grid:
        return questions
    for row in grid.select("tr"):
        cols = [col.get_text(" ", strip=True) for col in row.select("td")]
        if len(cols) < 2:
            continue
        question = re.sub(r"\s+", " ", cols[1]).strip()
        if question and "criteria questions" not in question.lower():
            questions.append(question)
    return _uniq(questions)


def parse_gauteng_job_detail(html: str, detail_url: str, base_job: dict[str, Any]) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    department = _gauteng_detail_text(soup, "#body_lblDepartmentName") or base_job.get("company", "")
    title = _gauteng_detail_text(soup, "#body_lblDesc") or base_job.get("title", "")
    reference_number = _gauteng_detail_text(soup, "#body_lblRefNo")
    directorate = _gauteng_detail_text(soup, "#body_lblDirectorate")
    location = _gauteng_detail_text(soup, "#body_lblCentre") or base_job.get("location", "")
    salary_package = _gauteng_detail_text(soup, "#body_lblPackage") or base_job.get("salary_package", "")
    closing_date = _gauteng_detail_text(soup, "#body_lblClosingDate")
    enquiries = _gauteng_detail_text(soup, "#body_lblEnquiries")
    requirements = _gauteng_detail_text(soup, "#body_lblRequirements")
    duties = _gauteng_detail_text(soup, "#body_lblDuties")
    notes = _gauteng_detail_text(soup, "#body_lblNotes")
    criteria_questions = _extract_gauteng_criteria_questions(soup)
    description_parts = []
    if directorate:
        description_parts.append(f"Directorate: {directorate}")
    if requirements:
        description_parts.append(f"Requirements: {requirements}")
    if duties:
        description_parts.append(f"Duties: {duties}")
    if notes:
        description_parts.append(f"Notes: {notes}")
    if criteria_questions:
        description_parts.append("Criteria Questions: " + " | ".join(criteria_questions))
    application_requirements = extract_public_service_application_requirements(notes or " ".join(description_parts), detail_url)
    if not application_requirements.get("contact_phone") and enquiries:
        phones = _extract_phone_numbers(enquiries)
        if phones:
            application_requirements["contact_phone"] = phones[0]
    if application_requirements.get("application_method") == "unknown":
        application_requirements["application_method"] = "online_portal"
        application_requirements["application_portal_url"] = "https://jobs.gauteng.gov.za"
        application_requirements["summary"] = (
            application_requirements.get("summary") or "Apply through the Gauteng e-Recruitment portal."
        )
    return {
        **base_job,
        "external_id": slugify(f"gpg-{reference_number or detail_url}"),
        "title": title,
        "company": department or base_job.get("company", ""),
        "department": department,
        "location": location,
        "listing_url": detail_url,
        "apply_url": detail_url,
        "portal_type": "generic",
        "description": "\n".join(description_parts)[:4000],
        "reference_number": reference_number,
        "closing_date": closing_date,
        "salary_package": salary_package,
        "criteria_questions": criteria_questions,
        "application_requirements": application_requirements,
        "public_service": True,
    }


async def _fetch_gauteng_job_detail(detail_url: str, base_job: dict[str, Any]) -> dict[str, Any]:
    response = await http_get(detail_url, timeout=25)
    return parse_gauteng_job_detail(response.text, detail_url, base_job)


async def scan_gauteng_jobs(source: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    response = await http_get(source["seed_url"], timeout=25)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.select_one("#tblJobs") or soup.select_one("table.display")
    if not table:
        return []
    base_jobs: list[dict[str, Any]] = []
    for row in table.select("tr"):
        cells = row.select("td")
        if len(cells) < 6:
            continue
        detail_link = cells[-1].select_one("a[href*='ViewJob.aspx']")
        if not detail_link:
            continue
        title = cells[0].get_text(" ", strip=True)
        department = cells[1].get_text(" ", strip=True)
        location = cells[2].get_text(" ", strip=True)
        package = cells[3].get_text(" ", strip=True)
        closing = cells[4].get_text(" ", strip=True)
        detail_url = urljoin(source["seed_url"], detail_link.get("href", ""))
        base_jobs.append(
            {
                "external_id": slugify(detail_url),
                "title": title,
                "company": department or source["name"],
                "department": department or source["name"],
                "location": location,
                "salary_package": package,
                "closing_date": closing,
                "listing_url": detail_url,
                "apply_url": detail_url,
                "portal_type": "generic",
                "description": f"{department} {location} {package} closing {closing}",
                "public_service": True,
            }
        )
    if not base_jobs:
        return []

    scored = sorted(
        base_jobs,
        key=lambda item: score_job_fit(item.get("title", ""), item.get("description", ""), profile),
        reverse=True,
    )
    candidates = [job for job in scored if score_job_fit(job.get("title", ""), job.get("description", ""), profile) >= 8] or scored[:20]
    semaphore = asyncio.Semaphore(5)

    async def enrich(job: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await _fetch_gauteng_job_detail(job["apply_url"], job)
            except Exception:
                return job

    detailed = await asyncio.gather(*(enrich(job) for job in candidates[:25]))
    return dedupe_jobs(detailed)


async def scan_greenhouse(source: dict[str, Any]) -> list[dict[str, Any]]:
    url = normalize_source_url(source["seed_url"]).rstrip("/")
    candidates = source_candidate_urls(source) or [url]
    if url and "/boards/" not in url and "/jobs" not in url:
        candidates.extend([url + "/jobs", url + "/boards"])
    candidates = _uniq(candidates)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            resp = await http_get(candidate, timeout=25, raise_for_status=False)
            if resp.status_code >= 400:
                last_error = RuntimeError(f"HTTP {resp.status_code} for {candidate}")
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            jobs: list[dict[str, Any]] = []
            for link in soup.select("a[href*='/jobs/'], a[href*='job-boards.greenhouse.io']"):
                href = urljoin(candidate, link.get("href", ""))
                text = link.get_text(" ", strip=True)
                if text and href:
                    jobs.append(
                        {
                            "external_id": slugify(href),
                            "title": text,
                            "company": source["name"],
                            "listing_url": href,
                            "apply_url": href,
                            "portal_type": "greenhouse",
                            "description": "",
                        }
                    )
            if jobs:
                source["seed_url"] = candidate
                return dedupe_jobs(jobs)
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise RuntimeError(f"Greenhouse scan failed for {source['name']}: {last_error}")
    return []


async def scan_lever(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "lever"
    return jobs


async def scan_smartrecruiters(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "smartrecruiters"
    return jobs


async def scan_workday(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "workday"
    return jobs


async def scan_ashby(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "ashby"
    return jobs


async def scan_icims(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "icims"
    return jobs


async def scan_successfactors(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "successfactors"
    return jobs


async def scan_simplify(source: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = await _scan_source_candidates(source, company_hint=source["name"])
    for job in jobs:
        job["portal_type"] = "simplify"
    return jobs


async def scan_generic_company_site(source: dict[str, Any]) -> list[dict[str, Any]]:
    return await _scan_source_candidates(source, company_hint=source["name"])


def _extract_jobs_from_listing_html(html: str, base_url: str, company_hint: str = "", location: str = "") -> list[dict[str, Any]]:
    if not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    selectors = [
        "a[href*='/jobs/adverts/']",
        "a[href*='job']",
        "a[href*='career']",
        "a[href*='vacanc']",
        "a[href*='position']",
        "a[href*='opening']",
        "article a[href]",
        "li a[href]",
    ]
    for selector in selectors:
        for link in soup.select(selector):
            href = urljoin(base_url, link.get("href", ""))
            text = link.get_text(" ", strip=True)
            lower = f"{href} {text}".lower()
            if href in seen or len(text) < 4:
                continue
            if not any(token in lower for token in ["job", "career", "vacanc", "position", "apply", "opening", "representative", "sales"]):
                continue
            seen.add(href)
            container = link.find_parent(["article", "li", "div", "tr"])
            company = company_hint or urlparse(base_url).netloc
            item_location = location
            description = ""
            if container:
                company_node = container.select_one(".company, .companyName, [data-testid='company-name']")
                location_node = container.select_one(".location, .companyLocation, [data-testid='text-location']")
                if company_node:
                    company = company_node.get_text(" ", strip=True) or company
                if location_node:
                    item_location = location_node.get_text(" ", strip=True) or item_location
                description = container.get_text(" ", strip=True)[:800]
            jobs.append(
                {
                    "external_id": slugify(href),
                    "title": text[:160],
                    "company": company,
                    "location": item_location,
                    "listing_url": href,
                    "apply_url": href,
                    "portal_type": infer_portal_type(href, html),
                    "description": description,
                }
            )
        if jobs:
            break
    return dedupe_jobs(jobs[:30])


async def _scan_source_candidates(source: dict[str, Any], company_hint: str = "", location: str = "") -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for candidate_url in source_candidate_urls(source):
        try:
            resp = await http_get(candidate_url, timeout=25, raise_for_status=False)
            jobs = _extract_jobs_from_listing_html(resp.text, str(resp.url), company_hint=company_hint, location=location)
            if resp.status_code < 400:
                source["seed_url"] = candidate_url
                source["portal_type"] = infer_portal_type(str(resp.url), resp.text)
            if jobs:
                return jobs
            if resp.status_code >= 400:
                last_error = RuntimeError(f"HTTP {resp.status_code} for {candidate_url}")
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise RuntimeError(f"{source['name']}: {last_error}")
    return []


async def _scan_generic_listing_page(url: str, company_hint: str = "", location: str = "") -> list[dict[str, Any]]:
    resp = await http_get(url, timeout=25)
    return _extract_jobs_from_listing_html(resp.text, str(resp.url), company_hint=company_hint, location=location)


def dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        key = str(job.get("external_id") or slugify(job.get("apply_url") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped


async def scan_source(source: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    kind = source.get("kind")
    queries = profile.get("search_terms", []) or [profile.get("search_keywords", "pharmaceutical sales representative")]
    location = profile.get("location", "Gauteng")
    if kind == "linkedin_search":
        jobs = await _search_query_variants(search_linkedin_jobs, queries[:4], location)
    elif kind == "aggregator_search" and "indeed" in source["name"].lower():
        jobs = await _search_query_variants(search_indeed_jobs, queries[:4], location)
    elif kind == "aggregator_search" and "pnet" in source["name"].lower():
        jobs = await _search_query_variants(search_pnet_jobs, queries[:4], location)
    elif kind == "public_jobs_portal" and "jobs.gauteng.gov.za" in source.get("seed_url", ""):
        jobs = await scan_gauteng_jobs(source, profile)
    else:
        portal_type = source.get("portal_type") or infer_portal_type(source["seed_url"])
        if portal_type == "greenhouse":
            jobs = await scan_greenhouse(source)
        elif portal_type == "lever":
            jobs = await scan_lever(source)
        elif portal_type == "smartrecruiters":
            jobs = await scan_smartrecruiters(source)
        elif portal_type == "workday":
            jobs = await scan_workday(source)
        else:
            jobs = await scan_generic_company_site(source)
    threshold = 10 if source.get("kind") == "public_jobs_portal" else 18
    return [normalize_job(job, source, profile) for job in jobs if score_job_fit(job.get("title", ""), job.get("description", ""), profile) >= threshold]


async def scan_enabled_sources(
    sources: list[dict[str, Any]],
    profile: dict[str, Any],
    existing_jobs: dict[str, dict[str, Any]],
    scheduled_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    discovered: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    selected_sources = [
        source for source in sources
        if source.get("enabled", True) and (not scheduled_only or source.get("schedule_enabled", True))
    ]
    semaphore = asyncio.Semaphore(5)

    async def scan_one(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        async with semaphore:
            try:
                return source, await scan_source(source, profile), ""
            except Exception as exc:
                return source, [], str(exc)

    results = await asyncio.gather(*(scan_one(source) for source in selected_sources))
    for source, jobs, error in results:
        new_count = 0
        for job in jobs:
            if job["id"] in existing_jobs:
                continue
            existing_jobs[job["id"]] = job
            discovered.append(job)
            new_count += 1
        source["last_checked"] = utcnow_iso()
        source["last_error"] = error
        source["last_result"] = (f"{new_count} new / {len(jobs)} matched") if not error else f"error: {error[:120]}"
        source_results.append({"source_id": source["id"], "matched": len(jobs), "new": new_count, "error": error})
    discovered.sort(key=lambda item: (item["fit_score"], item["discovered_at"]), reverse=True)
    return discovered, source_results


def build_application_record(user_id: int, job: dict[str, Any]) -> dict[str, Any]:
    requirements = dict(job.get("application_requirements") or {})
    public_service = bool(job.get("public_service") or requirements.get("public_service"))
    state = "manual_review" if public_service or not supports_assisted_apply(job["portal_type"]) else "ready_to_apply"
    review_reason = summarize_application_requirements(requirements)
    return {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "job_id": job["id"],
        "company": job["company"],
        "title": job["title"],
        "department": job.get("department") or job["company"],
        "portal_type": job["portal_type"],
        "apply_url": job["apply_url"],
        "state": state,
        "resume_path": "",
        "attachments": [],
        "required_questions": [],
        "unknown_questions": [],
        "last_error": review_reason if state == "manual_review" and review_reason else "",
        "submitted_at": None,
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        "artifacts": [],
        "upload_plan": {},
        "upload_field_observations": [],
        "resume_parse_version": None,
        "apply_family": "public_service" if public_service else "ats",
        "reference_number": job.get("reference_number", ""),
        "closing_date": job.get("closing_date", ""),
        "salary_package": job.get("salary_package", ""),
        "criteria_questions": list(job.get("criteria_questions") or []),
        "application_requirements": requirements,
        "public_service": public_service,
    }


def answer_key_for_label(label: str) -> str | None:
    lower = re.sub(r"\s+", " ", label.lower()).strip()
    if "experience" in lower and ("years" in lower or "year" in lower):
        return "experience_years"
    mappings = {
        "full_name": ["full name", "name"],
        "email": ["email"],
        "phone": ["phone", "mobile", "telephone"],
        "linkedin_url": ["linkedin"],
        "current_location": ["location", "city", "address"],
        "current_title": ["current title", "current role", "headline"],
        "work_authorization": ["work authorization", "eligible to work", "right to work"],
        "notice_period": ["notice period", "when can you start", "availability"],
        "drivers_license": ["driver", "driver's licence", "drivers license", "license"],
        "willing_to_travel": ["willing to travel", "travel"],
        "salary_expectation": ["salary", "compensation", "ctc", "remuneration"],
        "sapc_registration": ["sapc", "registration"],
        "cover_letter": ["cover letter"],
    }
    for key, patterns in mappings.items():
        if any(pattern in lower for pattern in patterns):
            return key
    return None


def _match_option_label(options: list[str] | None, preferred: str) -> str:
    preferred_lower = preferred.lower()
    for option in options or []:
        cleaned = re.sub(r"\s+", " ", option).strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower == preferred_lower or lower.startswith(preferred_lower):
            return cleaned
    return preferred


def infer_answer_for_prompt(
    label: str,
    options: list[str] | None = None,
    answers: dict[str, Any] | None = None,
    resume_profile: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> str | None:
    answers = answers or {}
    resume_profile = resume_profile or {}
    profile = profile or {}
    lower = re.sub(r"\s+", " ", label.lower()).strip()

    explicit_key = answer_key_for_label(lower)
    if explicit_key:
        explicit_value = str(answers.get(explicit_key, "")).strip()
        if explicit_value:
            return explicit_value

    normalized_options = [re.sub(r"\s+", " ", str(option)).strip() for option in (options or []) if str(option).strip()]
    has_yes = any(option.lower().startswith("yes") for option in normalized_options)
    has_no = any(option.lower().startswith("no") for option in normalized_options)
    if not (has_yes and has_no):
        return None

    raw_resume = " ".join(
        [
            str(resume_profile.get("raw_text", "")),
            " ".join(str(item) for item in resume_profile.get("skills", [])),
            " ".join(str(item) for item in resume_profile.get("target_roles", [])),
            " ".join(str(item) for item in resume_profile.get("licenses", [])),
            " ".join(str(item) for item in resume_profile.get("certifications", [])),
            str(profile.get("job_title", "")),
            str(profile.get("resume_summary", "")),
            str(profile.get("search_keywords", "")),
        ]
    ).lower()

    def yes_or_no(condition: bool) -> str:
        return _match_option_label(normalized_options, "yes" if condition else "no")

    if "sapc" in lower:
        return yes_or_no("sapc" in raw_resume or "sapc" in str(answers.get("sapc_registration", "")).lower())
    if any(token in lower for token in ["bachelor", "degree", "education"]) and "have you completed" in lower:
        education_signals = ["bsc", "bachelor", "degree", "university"]
        return yes_or_no(any(signal in raw_resume for signal in education_signals))
    if any(token in lower for token in ["driver", "driver's licence", "drivers license", "driver license"]):
        drivers_license = str(answers.get("drivers_license", "")).lower()
        return yes_or_no("yes" in drivers_license or "code" in raw_resume or "driver" in raw_resume)
    if "travel" in lower:
        willingness = str(answers.get("willing_to_travel", "")).strip().lower()
        if willingness:
            return yes_or_no(willingness.startswith("y"))
    if any(token in lower for token in ["work authorization", "right to work", "eligible to work"]):
        authorization = str(answers.get("work_authorization", "")).strip().lower()
        if authorization:
            return yes_or_no(not authorization.startswith("n"))
    if "experience" in lower and any(token in lower for token in ["pharma", "pharmaceutical", "medical sales", "sales in pharma", "sales in the pharma", "otc"]):
        experience_signals = [
            "pharmaceutical retail industry",
            "pharmaceutical sales",
            "aspiring pharmaceutical sales representative",
            "pharmacist assistant",
            "otc",
            "consumer health",
            "promoting product sales",
            "driving sales targets",
        ]
        return yes_or_no(any(signal in raw_resume for signal in experience_signals))
    if "sales" in lower and "experience" in lower:
        sales_signals = ["sales", "customer relationship management", "selling skills", "sales targets"]
        return yes_or_no(any(signal in raw_resume for signal in sales_signals))
    return None
