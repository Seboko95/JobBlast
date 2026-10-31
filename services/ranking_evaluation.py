"""Small anonymized ranking benchmark for repeatable JobBlast checks."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from core.intelligence import canonical_job_key, match_snapshot
from jobblast_ats import score_job_fit


def evaluation_cases() -> list[dict[str, Any]]:
    candidate = {
        "target_roles": ["Medical Representative", "Pharmacist Assistant"],
        "skills": ["pharmaceutical sales", "customer service", "stock control"],
        "certifications": ["pharmacy qualification"],
        "licenses": ["driver licence"],
        "locations": ["Johannesburg", "Gauteng"],
        "experience_years": 5,
        "resume_profile": {
            "raw_text": "Pharmaceutical sales and customer service experience. Pharmacy qualification. Driver licence.",
            "education": ["Pharmacy qualification"],
            "experience_years": 5,
        },
        "answer_bank": {},
        "excluded_keywords": ["internship"],
        "blocked_employers": ["bad employer"],
    }
    base = [
        {"id": "excellent", "label": 1, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Johannesburg, Gauteng", "description": "Pharmaceutical sales, customer service and stock control. 3 years experience required.", "listing_url": "https://example.org/jobs/1"}},
        {"id": "alias", "label": 1, "candidate": candidate, "job": {"title": "Pharma Rep", "company": "Example Pharma", "location": "Gauteng", "description": "Pharmaceutical sales representative role.", "listing_url": "https://example.org/jobs/2"}},
        {"id": "related", "label": 1, "candidate": candidate, "job": {"title": "Product Adviser", "company": "Example Devices", "location": "Johannesburg", "description": "Healthcare product specialist and customer service role.", "listing_url": "https://example.org/jobs/3"}},
        {"id": "medium", "label": 1, "candidate": candidate, "job": {"title": "Pharmacist Assistant", "company": "Example Pharmacy", "location": "Gauteng", "description": "Assist with pharmacy operations and stock control.", "listing_url": "https://example.org/jobs/4"}},
        {"id": "irrelevant", "label": 0, "candidate": candidate, "job": {"title": "Receptionist", "company": "Example Office", "location": "Johannesburg", "description": "Front desk administration and switchboard duties.", "listing_url": "https://example.org/jobs/5"}},
        {"id": "excluded", "label": 0, "candidate": candidate, "job": {"title": "Medical Representative Internship", "company": "Example Pharma", "location": "Johannesburg", "description": "Pharmaceutical sales internship.", "listing_url": "https://example.org/jobs/6"}},
        {"id": "wrong-career", "label": 0, "candidate": candidate, "job": {"title": "Software Engineer", "company": "Example Tech", "location": "Johannesburg", "description": "Python and web development.", "listing_url": "https://example.org/jobs/7"}},
        {"id": "too-senior", "label": 0, "candidate": candidate, "job": {"title": "Regional Sales Director", "company": "Example Pharma", "location": "Gauteng", "description": "10 years experience required for executive leadership.", "listing_url": "https://example.org/jobs/8"}},
        {"id": "qualification-unknown", "label": 0, "candidate": {**candidate, "resume_profile": {**candidate["resume_profile"], "education": []}, "certifications": []}, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Gauteng", "description": "Bachelor degree required for this position.", "listing_url": "https://example.org/jobs/9"}},
        {"id": "duplicate", "label": 1, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Johannesburg, Gauteng", "description": "Pharmaceutical sales, customer service and stock control.", "listing_url": "https://example.org/jobs/1?utm_source=test"}},
        {"id": "expired", "label": 0, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Johannesburg", "description": "Pharmaceutical sales role.", "closing_date": "01-01-2020", "listing_url": "https://example.org/jobs/10"}},
        {"id": "stale", "label": 0, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Johannesburg", "description": "Pharmaceutical sales role.", "last_seen_at": "2020-01-01T00:00:00Z", "listing_url": "https://example.org/jobs/11"}},
        {"id": "suspicious", "label": 0, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Unknown", "location": "Johannesburg", "description": "Pay a fee to process your application.", "listing_url": "https://bit.ly/example"}},
        {"id": "weak-evidence", "label": 0, "candidate": {**candidate, "resume_profile": {"raw_text": "", "education": []}, "skills": [], "certifications": [], "licenses": []}, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Johannesburg", "description": "Pharmaceutical sales role.", "listing_url": "https://example.org/jobs/12"}},
        {"id": "official", "label": 1, "candidate": candidate, "job": {"title": "Medical Representative", "company": "Example Pharma", "location": "Gauteng", "description": "Pharmaceutical sales role.", "source_kind": "company_portal", "listing_url": "https://jobs.example.org/role/13"}},
        {"id": "government", "label": 1, "candidate": candidate, "job": {"title": "Administration Clerk", "company": "Department Example", "location": "Gauteng", "description": "Government administration vacancy.", "reference_number": "EX-13", "closing_date": "31-12-2099", "listing_url": "https://department.gov.za/vacancy/13"}},
    ]
    # Privacy-safe fixtures cover ranking edge cases without using user records.
    specs = [
        ("synonym-title", 1, "Pharmaceutical Sales Representative", "Pharma sales and customer engagement.", "Johannesburg", {}),
        ("related-occupation", 1, "Healthcare Product Specialist", "Healthcare product training and customer service.", "Gauteng", {}),
        ("adjacent-wrong-role", 0, "Medical Receptionist", "Healthcare administration and switchboard.", "Johannesburg", {}),
        ("keyword-trap", 0, "Software Sales Engineer", "Python, APIs, and enterprise software sales.", "Johannesburg", {}),
        ("too-junior", 0, "Junior Sales Intern", "Internship for students with no experience.", "Johannesburg", {}),
        ("preferred-missing", 1, "Medical Representative", "Pharmaceutical sales role; SAP experience preferred.", "Gauteng", {}),
        ("salary-missing", 1, "Medical Representative", "Pharmaceutical sales and customer service.", "Johannesburg", {}),
        ("location-missing", 1, "Medical Representative", "Pharmaceutical sales and customer service.", "", {}),
        ("posting-date-missing", 1, "Medical Representative", "Rich requirements and pharmaceutical sales duties.", "Gauteng", {}),
        ("closing-date-missing", 1, "Medical Representative", "Pharmaceutical sales role with customer service.", "Gauteng", {}),
        ("closed-advert", 0, "Medical Representative", "Pharmaceutical sales role.", "Gauteng", {"availability_status": "closed"}),
        ("remote-mismatch", 0, "Medical Representative", "Remote role requiring permanent residence outside Gauteng.", "Remote - Cape Town", {}),
        ("hybrid-match", 1, "Medical Representative", "Hybrid pharmaceutical sales and customer service.", "Johannesburg / Hybrid", {}),
        ("geography-blocker", 0, "Medical Representative", "Must be based in Cape Town.", "Cape Town", {}),
        ("registration-required", 1, "Pharmacist Assistant", "Professional registration with SAPC is required.", "Gauteng", {}),
        ("licence-required", 1, "Medical Representative", "A driver's licence is required for territory travel.", "Johannesburg", {}),
        ("qualification-synonym", 1, "Pharmacy Assistant", "Diploma or equivalent pharmacy qualification.", "Gauteng", {}),
        ("years-required", 1, "Medical Representative", "At least 3 years pharmaceutical sales experience.", "Johannesburg", {}),
        ("skill-synonym", 1, "Medical Representative", "Office suite proficiency and client engagement.", "Johannesburg", {}),
        ("employer-blocked", 0, "Medical Representative", "Pharmaceutical sales role.", "Johannesburg", {"company": "Bad Employer Holdings"}),
        ("salary-below", 1, "Medical Representative", "Pharmaceutical sales role. Salary R100000.", "Johannesburg", {}),
        ("unknown-salary", 1, "Medical Representative", "Pharmaceutical sales role; package not stated.", "Johannesburg", {}),
        ("weak-trust", 0, "Medical Representative", "Pharmaceutical sales role.", "Johannesburg", {"trust_status": "high_risk"}),
        ("trusted-medium-fit", 1, "Territory Manager", "Healthcare territory coordination and customer service.", "Gauteng", {"source_kind": "company_portal"}),
        ("new-irrelevant", 0, "Data Analyst", "SQL, dashboards, and Python.", "Johannesburg", {"discovered_at": "2026-08-27T00:00:00Z"}),
        ("old-valid", 1, "Medical Representative", "Pharmaceutical sales role.", "Johannesburg", {"last_seen_at": "2026-08-20T00:00:00Z"}),
        ("official-contradiction", 1, "Medical Representative", "Official source says pharmaceutical sales; aggregator says internship.", "Gauteng", {"source_kind": "company_portal"}),
        ("revision-requirement", 1, "Medical Representative", "Updated requirements: pharmaceutical sales and customer service.", "Gauteng", {"revision": 2}),
        ("revision-closing", 1, "Medical Representative", "Pharmaceutical sales role.", "Gauteng", {"closing_date": "31-12-2099", "revision": 2}),
        ("removed-restored", 1, "Medical Representative", "Pharmaceutical sales role restored.", "Gauteng", {"availability_status": "active"}),
        ("office-suite", 1, "Sales Representative", "Microsoft Office and customer service required.", "Johannesburg", {}),
        ("retail-pharmacy", 1, "Retail Pharmacy Assistant", "Retail pharmacy experience and stock control.", "Gauteng", {}),
        ("suspicious-fee", 0, "Medical Representative", "Pay a fee to process your application.", "Johannesburg", {"listing_url": "https://bit.ly/unsafe"}),
        ("low-info", 1, "Medical Representative", "Medical Representative", "Gauteng", {}),
    ]
    expanded = []
    for index, (case_id, label, title, description, location, extra) in enumerate(specs, start=20):
        job = {
            "title": title,
            "company": extra.pop("company", f"Synthetic Employer {index}"),
            "location": location,
            "description": description,
            "listing_url": extra.pop("listing_url", f"https://synthetic.example/jobs/{index}"),
            **extra,
        }
        expanded.append({"id": case_id, "label": label, "relevance_grade": 3 if label else 0, "eligibility": "eligible" if label else "ineligible", "expected_actionability": "prep_needed" if label else "not_actionable", "candidate": candidate, "job": job})
    return base + expanded


def _ndcg(labels: list[int], limit: int) -> float:
    ranked = labels[:limit]
    dcg = sum((2 ** label - 1) / math.log2(index + 2) for index, label in enumerate(ranked))
    ideal = sorted(labels, reverse=True)[:limit]
    idcg = sum((2 ** label - 1) / math.log2(index + 2) for index, label in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _deduplicate_ranked(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in sorted(items, key=lambda value: value["snapshot"].get("recommendation_score", value["snapshot"]["overall_score"]), reverse=True):
        key = canonical_job_key(item["job"])
        previous = selected.get(key)
        if previous is None or item["snapshot"].get("recommendation_score", item["snapshot"]["overall_score"]) > previous["snapshot"].get("recommendation_score", previous["snapshot"]["overall_score"]):
            selected[key] = item
    return sorted(selected.values(), key=lambda value: value["snapshot"].get("recommendation_score", value["snapshot"]["overall_score"]), reverse=True)


def evaluate_cases(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cases = cases or evaluation_cases()
    scored = []
    for case in cases:
        snapshot = match_snapshot(case["job"], case["candidate"])
        scored.append({**case, "snapshot": snapshot, "legacy_score": score_job_fit(case["job"].get("title", ""), case["job"].get("description", ""), case["candidate"])})
    raw_ranked = sorted(scored, key=lambda item: item["snapshot"].get("recommendation_score", item["snapshot"]["overall_score"]), reverse=True)
    ranked = _deduplicate_ranked(scored)
    labels = [int(item["label"]) for item in ranked]
    duplicate_keys = [canonical_job_key(case["job"]) for case in cases]
    raw_top_keys = [canonical_job_key(item["job"]) for item in raw_ranked[:10]]
    raw_top_duplicate_exposure = len(raw_top_keys) - len(set(raw_top_keys))
    duplicate_input_count = len(duplicate_keys) - len(set(duplicate_keys))
    ranked_keys = [canonical_job_key(item["job"]) for item in ranked[:10]]
    expired = [item for item in scored if item["snapshot"]["freshness_status"] == "expired"]
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "case_count": len(cases),
        "unique_case_count": len(ranked),
        "hard_exclusion_violation_rate": sum(1 for item in scored if not item["snapshot"]["eligibility"]["eligible"] and item["snapshot"]["strong_match"]) / max(1, len(scored)),
        "duplicate_input_rate": duplicate_input_count / max(1, len(cases)),
        "raw_top_duplicate_exposure_rate": raw_top_duplicate_exposure / max(1, len(raw_ranked[:10])),
        "duplicate_exposure_rate": (len(ranked_keys) - len(set(ranked_keys))) / max(1, len(ranked_keys)),
        "expired_job_exposure_rate": sum(1 for item in expired if item["snapshot"]["strong_match"]) / max(1, len(expired)),
        "precision_at_5": sum(labels[:5]) / max(1, min(5, len(labels))),
        "precision_at_10": sum(labels[:10]) / max(1, min(10, len(labels))),
        "ndcg_at_5": round(_ndcg(labels, 5), 4),
        "ndcg_at_10": round(_ndcg(labels, 10), 4),
        "recall_at_50": sum(labels[:50]) / max(1, sum(int(item["label"]) for item in ranked)),
        "strong_match_precision": sum(int(item["label"]) for item in ranked[:10] if item["snapshot"]["strong_match"]) / max(1, sum(1 for item in ranked[:10] if item["snapshot"]["strong_match"])),
        "actionability_precision": sum(int(item["label"]) for item in scored if item["snapshot"].get("actionable")) / max(1, sum(1 for item in scored if item["snapshot"].get("actionable"))),
        "false_positive_count": sum(1 for item in scored if item["snapshot"]["strong_match"] and not item["label"]),
        "false_negative_count": sum(1 for item in scored if item["label"] and not item["snapshot"]["strong_match"]),
        "coverage_distribution": {
            "low": sum(1 for item in scored if item["snapshot"].get("score_confidence") == "low"),
            "medium": sum(1 for item in scored if item["snapshot"].get("score_confidence") == "medium"),
            "high": sum(1 for item in scored if item["snapshot"].get("score_confidence") == "high"),
        },
        "explanation_faithfulness_rate": sum(1 for item in scored if item["snapshot"]["eligibility"]["eligible"] or any(item["snapshot"]["eligibility"]["reasons"][0] in explanation for explanation in item["snapshot"]["explanation"])) / max(1, len(scored)),
        "strong_match_ids": [item["id"] for item in ranked if item["snapshot"]["strong_match"]],
        "legacy_top_ids": [item["id"] for item in sorted(scored, key=lambda item: item["legacy_score"], reverse=True)[:10]],
        "v2_top_ids": [item["id"] for item in ranked[:10]],
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_cases(), indent=2))
