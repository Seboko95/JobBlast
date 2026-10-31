"""Inline keyboard and screen rendering helpers."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.jobs import state_label
from core.feedback import NEGATIVE_FEEDBACK_REASONS
from services.cover_letter_format import formatted_cover_letter_text
from services.documents import document_summary, resume_status
from services.gap_analysis import gap_analysis_text as render_gap_analysis_text
from services.job_views import bot_status
from services.source_health import source_health_badge


def nav_row(back: str = "nav:home") -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton("\u2b05\ufe0f Back", callback_data=back),
        InlineKeyboardButton("\U0001f3e0 Home", callback_data="nav:home"),
    ]


def short(text: str, limit: int = 34) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


def home_text(repo, user_id: int) -> str:
    profile = repo.get_profile(user_id)
    counts = repo.job_counts(user_id)
    sources = repo.source_stats(user_id)
    active_persona = profile.get("active_persona_id") or "default"
    return "\n".join(
        [
            "\U0001f680 JOBBLAST",
            "Personal job intelligence command centre",
            "",
            f"\U0001f3af Persona: {active_persona}",
            f"\U0001f4c4 CV: {resume_status(repo, user_id)} | \U0001f4ce Documents: {document_summary(repo, user_id)}",
            f"\U0001f4e1 Sources: {sources['enabled']}/{sources['total']} enabled | \U0001f3db Public: {sources['public_sector']}",
            "",
            f"\U0001f525 Strong: {counts['strong_matches']} | \U0001f195 New: {counts['new']} | \u23f0 Closing soon: {counts['closing_soon']}",
            f"\U0001f7e2 Ready: {counts.get('ready_to_apply', 0)} | \U0001f7e1 Preparation: {counts.get('preparing', 0)} | \u26a0\ufe0f Review: {counts['stale_unverified']}",
            f"\U0001f4c2 Saved: {counts['saved']} | \U0001f4e8 Applied: {counts['applied_by_user']} | \U0001f399 Interviews: {counts.get('interview', 0)} | \U0001f4bc Offers: {counts.get('offer', 0)}",
            "",
            "Manual-apply mode is active. Final submission always stays with you.",
        ]
    )


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f50e Scan", callback_data="nav:scan"),
                InlineKeyboardButton("\U0001f525 For You", callback_data="nav:list:top:0"),
            ],
            [
                InlineKeyboardButton("\U0001f7e2 Ready to Apply", callback_data="nav:list:ready:0"),
                InlineKeyboardButton("\u2b50 Saved", callback_data="nav:list:saved:0"),
            ],
            [
                InlineKeyboardButton("\U0001f4c2 Applications", callback_data="nav:applications"),
                InlineKeyboardButton("\U0001f9f0 Prepare", callback_data="nav:list:preparing:0"),
            ],
            [
                InlineKeyboardButton("\U0001f4c4 Documents", callback_data="nav:documents"),
                InlineKeyboardButton("\U0001f4da My Evidence", callback_data="nav:evidence"),
            ],
            [
                InlineKeyboardButton("\U0001f3af Personas", callback_data="nav:personas"),
                InlineKeyboardButton("\U0001f3db Government", callback_data="nav:government"),
            ],
            [
                InlineKeyboardButton("\u2699\ufe0f Settings", callback_data="nav:settings"),
                InlineKeyboardButton("\U0001f6e1 Status", callback_data="nav:status"),
            ],
        ]
    )


def list_screen_text(title: str, jobs: list[dict], page: int, per_page: int = 5) -> str:
    start = page * per_page
    subset = jobs[start:start + per_page]
    if not subset:
        empty = {
            "\u2b50 Saved": "You have not saved any jobs yet. Open a match and tap \u2b50 Save.",
            "\U0001f4c2 Applications": "No tracked applications yet. Mark a vacancy applied after submitting manually.",
            "\U0001f7e2 Ready to Apply": "No opportunities are currently ready for manual submission.",
            "\U0001f9f0 Preparation": "No jobs currently need preparation.",
            "\U0001f3db Government Jobs": "No public-service jobs are available in this view.",
        }.get(title, "Nothing to show here yet.")
        return f"{title}\n\n{empty}"
    lines = [title, ""]
    for index, job in enumerate(subset, start=1 + start):
        closing = f" | Closes {job['closing_date']}" if job.get("closing_date") else ""
        lines.append(
            f"{index}. {job.get('title', 'Untitled')}\n"
            f"   {job.get('company', 'Unknown')} | Score {job.get('fit_score', 0)} | {state_label(job)}{closing}"
        )
    return "\n\n".join(lines)


def list_keyboard(jobs: list[dict], origin: str, page: int, per_page: int = 5, back: str = "nav:home") -> InlineKeyboardMarkup:
    start = page * per_page
    subset = jobs[start:start + per_page]
    rows: list[list[InlineKeyboardButton]] = []
    for job in subset:
        rows.append([InlineKeyboardButton(short(job.get("title", "Untitled")), callback_data=f"job:view:{job['id']}:{origin}:{page}")])
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("\u2b05\ufe0f Previous", callback_data=f"nav:list:{origin}:{page - 1}"))
    if start + per_page < len(jobs):
        nav_buttons.append(InlineKeyboardButton("\u27a1\ufe0f Next", callback_data=f"nav:list:{origin}:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(nav_row(back))
    return InlineKeyboardMarkup(rows)


def job_card_text(job: dict) -> str:
    closing = job.get("closing_date") or "Not stated"
    why = job.get("why_match") or ["No explanation available."]
    required_docs = job.get("required_docs") or ["Resume / CV"]
    dimensions = dict((job.get("match_snapshot") or {}).get("dimensions") or {})
    dimension_details = dict((job.get("match_snapshot") or {}).get("dimension_details") or {})
    trust = job.get("trust_score")
    unknown_dimensions = [key.replace("_fit", "").replace("_score", "").replace("_", " ").title() for key, item in dimension_details.items() if isinstance(item, dict) and item.get("value") is None]
    history = list(job.get("state_history") or [])
    return "\n".join(
        [
            f"{job.get('title', 'Untitled role')}",
            f"Company: {job.get('company', 'Unknown')}",
            f"Location: {job.get('location') or 'Not stated'}",
            f"Source: {job.get('source_name', 'Unknown')} ({job.get('portal_type', 'generic')})",
            f"Closing date: {closing}",
            f"Match score: {job.get('fit_score', 0)}",
            f"Priority score: {job.get('recommendation_score', job.get('fit_score', 0))}",
            f"Evidence coverage: {round(float((job.get('match_snapshot') or {}).get('score_coverage', 0)) * 100)}% ({(job.get('match_snapshot') or {}).get('score_confidence', 'unknown')} confidence)",
            f"Candidate fit: {(job.get('match_snapshot') or {}).get('candidate_fit_score', 'Unknown')} | CV evidence: {(job.get('match_snapshot') or {}).get('resume_evidence_status', 'UNKNOWN')}",
            f"Eligibility: {str(job.get('eligibility_status') or 'unknown').replace('_', ' ').title()} | {'Strong match' if job.get('strong_match') else 'Review'}",
            f"Actionability: {str(job.get('actionability_state') or 'not_actionable').replace('_', ' ').title()}",
            f"Trust: {trust if trust is not None else 'Unknown'} ({job.get('trust_status', 'review')})",
            f"Freshness: {job.get('freshness_status', 'unknown').replace('_', ' ').title()} | Verified: {job.get('last_verified') or 'Not yet'}",
            "Fit: " + " | ".join(
                f"{key.replace('_fit', '').replace('_score', '').replace('_', ' ').title()} {value}"
                for key, value in dimensions.items()
                if value is not None
            ),
            f"Revision: {job.get('revision', 0)} | Sources: {len(job.get('source_listings') or job.get('source_ids') or [])}",
            f"Application readiness: {(job.get('application_readiness') or {}).get('score', '?')}% | {(job.get('application_readiness') or {}).get('status', job.get('actionability_state', 'unknown')).replace('_', ' ')}",
            f"State: {state_label(job)}",
            f"Applied: {job.get('applied_at') or 'Not marked applied'}",
            f"History entries: {len(history)}",
            "",
            "Why it matches:",
            *[f"- {item}" for item in why],
            *(["", "Unavailable for scoring:", *[f"- {item}" for item in unknown_dimensions]] if unknown_dimensions else []),
            "",
            f"Apply link:\n{job.get('apply_url', '') or job.get('listing_url', '')}",
            f"Application method: {job.get('application_method_label', 'Check job link')}",
            "Required docs:",
            *[f"- {item}" for item in required_docs],
            "",
            f"Recommended next action: {job.get('recommended_next_action', 'Review the vacancy and apply manually.')}",
            *( ["", "Actionability reason:", f"- {job.get('actionability_reason')}"] if job.get('actionability_reason') else [] ),
            *( ["Preparation:", *[f"- {item}" for item in (job.get('actionability_preparation') or [])]] if job.get('actionability_preparation') else [] ),
        ]
    )


def job_card_keyboard(job: dict, origin: str, page: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    apply_url = job.get("apply_url") or job.get("listing_url")
    if apply_url:
        buttons.append([InlineKeyboardButton("\U0001f517 Official Listing", url=job.get("authoritative_url") or apply_url)])
    if job.get("public_service"):
        buttons.append([InlineKeyboardButton("\U0001f4c4 Z83 Files", callback_data=f"job:z83:{job['id']}:{origin}:{page}")])
    buttons.extend(
        [
            [
                InlineKeyboardButton("\U0001f9ed How", callback_data=f"job:how:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\U0001f4a1 Why?", callback_data=f"job:why:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\u2709\ufe0f Cover Letter", callback_data=f"job:cover:{job['id']}:{origin}:{page}"),
            ],
            [
                InlineKeyboardButton("\U0001f50e Gaps", callback_data=f"job:gap:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\U0001f4dd Answers", callback_data=f"job:answers:{job['id']}:{origin}:{page}"),
            ],
            [InlineKeyboardButton("\U0001f9f0 Prepare", callback_data=f"job:prepare:{job['id']}:{origin}:{page}")],
            [InlineKeyboardButton("\U0001f4c2 Application Workspace", callback_data=f"app:view:{job['id']}:{origin}:{page}")],
            [
                InlineKeyboardButton("\U0001f4e6 Pack", callback_data=f"job:pack:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\u2b50 Save", callback_data=f"job:save:{job['id']}:{origin}:{page}"),
            ],
            [
                InlineKeyboardButton("\U0001f44d Relevant", callback_data=f"job:feedback:relevant:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\U0001f44e Not for me", callback_data=f"job:feedbackmenu:{job['id']}:{origin}:{page}"),
            ],
            [
                InlineKeyboardButton("\U0001f4e8 Mark Applied", callback_data=f"job:mark:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\u23ed Skip", callback_data=f"job:skip:{job['id']}:{origin}:{page}"),
            ],
            [
                InlineKeyboardButton("\U0001f7e1 Preparing", callback_data=f"job:state:preparing:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\U0001f399 Interview", callback_data=f"job:state:interview:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\U0001f4bc Offer", callback_data=f"job:state:offer:{job['id']}:{origin}:{page}"),
            ],
            [
                InlineKeyboardButton("\u2705 Accepted", callback_data=f"job:state:accepted:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\u274c Rejected", callback_data=f"job:state:rejected:{job['id']}:{origin}:{page}"),
                InlineKeyboardButton("\u21a9\ufe0f Withdrawn", callback_data=f"job:state:withdrawn:{job['id']}:{origin}:{page}"),
            ],
            [InlineKeyboardButton("\U0001f5d1 Remove", callback_data=f"job:remove:{job['id']}:{origin}:{page}")],
            nav_row(f"nav:list:{origin}:{page}"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def applications_text(repo, user_id: int) -> str:
    counts = repo.job_counts(user_id)
    return "\n".join(
        [
            "\U0001f4c2 APPLICATIONS",
            "",
            f"\U0001f7e1 Preparing: {counts.get('preparing', 0)}",
            f"\U0001f7e2 Ready to Apply: {counts.get('ready_to_apply', 0)}",
            f"\U0001f4e8 Applied: {counts.get('applied_by_user', 0)}",
            f"\U0001f399 Interviews: {counts.get('interview', 0)}",
            f"\U0001f4bc Offers: {counts.get('offer', 0)}",
            f"\u2705 Accepted: {counts.get('accepted', 0)}",
            f"\u274c Rejected: {counts.get('rejected', 0)}",
            f"\u21a9\ufe0f Withdrawn: {counts.get('withdrawn', 0)}",
            "",
            "Submission remains manual. JobBlast only records the status you choose.",
        ]
    )


def applications_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f7e1 Preparing", callback_data="nav:list:preparing:0"), InlineKeyboardButton("\U0001f7e2 Ready", callback_data="nav:list:ready:0")],
            [InlineKeyboardButton("\U0001f4e8 Applied", callback_data="nav:list:applied:0"), InlineKeyboardButton("\U0001f399 Interviews", callback_data="nav:list:interviews:0")],
            [InlineKeyboardButton("\U0001f4bc Offers", callback_data="nav:list:offers:0"), InlineKeyboardButton("\u2705 Accepted", callback_data="nav:list:accepted:0")],
            [InlineKeyboardButton("\u274c Rejected", callback_data="nav:list:rejected:0"), InlineKeyboardButton("\u21a9\ufe0f Withdrawn", callback_data="nav:list:withdrawn:0")],
            nav_row(),
        ]
    )


def application_text(job: dict) -> str:
    contact = dict(job.get("contact") or {})
    offer = dict(job.get("offer") or {})
    lines = [
        "\U0001f4c2 APPLICATION",
        "",
        f"{job.get('title', 'Untitled role')}\n{job.get('company', 'Unknown')}",
        f"State: {state_label(job)}",
        f"Applied: {job.get('application_date') or job.get('applied_at') or 'Not recorded'}",
        f"Method: {job.get('application_method') or job.get('application_method_label') or 'Not recorded'}",
        f"Follow-up: {job.get('follow_up_at') or 'Not set'}",
        "",
        "Interview:",
        f"- {job.get('interview_at') or 'Not scheduled'}",
        f"- {job.get('interview_location') or job.get('interview_link') or 'No location or link'}",
        f"- {job.get('interview_notes') or 'No notes'}",
        "",
        "Offer:",
        f"- Compensation: {offer.get('compensation') or 'Not recorded'}",
        f"- Received: {offer.get('received_at') or 'Not recorded'}",
        f"- Decision deadline: {offer.get('decision_deadline') or 'Not set'}",
        f"- Notes: {offer.get('notes') or 'No notes'}",
        "",
        "Contact:",
        f"- {contact.get('name') or 'No contact recorded'}",
        f"- {contact.get('role') or ''} {contact.get('email') or ''} {contact.get('phone') or ''}".strip(),
        f"- Notes: {contact.get('notes') or 'No notes'}",
        "",
        "Application notes:",
        f"- {job.get('application_notes') or 'No notes'}",
    ]
    return "\n".join(lines)


def application_keyboard(job: dict, origin: str, page: int) -> InlineKeyboardMarkup:
    job_id = job["id"]
    def edit(field: str, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"app:edit:{field}:{job_id}:{origin}:{page}")
    return InlineKeyboardMarkup(
        [
            [edit("application_date", "\U0001f4c5 Applied date"), edit("application_method", "\U0001f4e8 Method")],
            [edit("follow_up_at", "\U0001f4c5 Follow-up"), edit("application_url", "\U0001f517 Application URL")],
            [edit("contact", "\U0001f464 Contact"), edit("application_notes", "\U0001f4dd Notes")],
            [edit("interview", "\U0001f399 Interview"), edit("offer", "\U0001f4bc Offer")],
            [InlineKeyboardButton("\U0001f7e1 Preparing", callback_data=f"app:state:preparing:{job_id}:{origin}:{page}"), InlineKeyboardButton("\U0001f399 Interview", callback_data=f"app:state:interview:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("\U0001f4bc Offer", callback_data=f"app:state:offer:{job_id}:{origin}:{page}"), InlineKeyboardButton("\u2705 Accepted", callback_data=f"app:state:accepted:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("\u274c Rejected", callback_data=f"app:state:rejected:{job_id}:{origin}:{page}"), InlineKeyboardButton("\u21a9\ufe0f Withdrawn", callback_data=f"app:state:withdrawn:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("\U0001f4e8 Mark Applied", callback_data=f"job:mark:{job_id}:{origin}:{page}")],
            nav_row(f"nav:list:{origin}:{page}"),
        ]
    )


def lifecycle_prompt(field: str) -> str:
    prompts = {
        "application_date": "Send the date you applied, for example 2026-08-27.",
        "application_method": "Send the method used, for example portal, email, or hand delivery.",
        "application_url": "Send the application URL used. Leave it blank only if you did not apply online.",
        "follow_up_at": "Send a follow-up date, for example 2026-09-03, or 'none' to disable it.",
        "contact": "Send contact details in one line: Name | Role | Email | Phone | Notes.",
        "application_notes": "Send an application note. Existing notes are replaced.",
        "interview": "Send interview details: date/time | location or link | notes.",
        "offer": "Send offer details: compensation | received date | decision deadline | notes.",
    }
    return prompts.get(field, "Send the requested application detail.")


def evidence_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f393 Qualifications", callback_data="evidence:list:qualification:0"), InlineKeyboardButton("\U0001f4bc Experience", callback_data="evidence:list:experience:0")],
            [InlineKeyboardButton("\U0001faaa Registrations", callback_data="evidence:list:licence:0"), InlineKeyboardButton("\U0001f6e0 Skills", callback_data="evidence:list:skill:0")],
            [InlineKeyboardButton("\U0001f4dc Certifications", callback_data="evidence:list:certification:0"), InlineKeyboardButton("\U0001f697 Licences", callback_data="evidence:list:drivers_license:0")],
            [InlineKeyboardButton("\U0001f3e2 Employers", callback_data="evidence:list:employer:0"), InlineKeyboardButton("\U0001f5e3 Languages", callback_data="evidence:list:language:0")],
            [InlineKeyboardButton("\U0001f4c4 Source Documents", callback_data="set:resume")],
            nav_row(),
        ]
    )


def evidence_list_text(facts: list[dict], category: str, page: int, per_page: int = 6) -> str:
    subset = facts[page * per_page:(page + 1) * per_page]
    title = category.replace("_", " ").title()
    if not subset:
        return f"\U0001f4da {title}\n\nNo evidence is stored in this category yet."
    lines = [f"\U0001f4da {title}", ""]
    for fact in subset:
        marker = "\u2705" if fact.get("user_verified") else "\U0001f4c4"
        lines.append(f"{marker} {fact.get('display_value') or fact.get('normalized_claim') or 'Unnamed fact'}")
    return "\n".join(lines)


def evidence_list_keyboard(facts: list[dict], category: str, page: int, per_page: int = 6) -> InlineKeyboardMarkup:
    subset = facts[page * per_page:(page + 1) * per_page]
    rows = [[InlineKeyboardButton(short(fact.get("display_value") or fact.get("normalized_claim"), 42), callback_data=f"evidence:view:{index}:{category}:{page}")] for index, fact in enumerate(subset)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("\u2b05\ufe0f Previous", callback_data=f"evidence:list:{category}:{page - 1}"))
    if (page + 1) * per_page < len(facts):
        nav.append(InlineKeyboardButton("\u27a1\ufe0f Next", callback_data=f"evidence:list:{category}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(nav_row("nav:evidence"))
    return InlineKeyboardMarkup(rows)


def evidence_fact_text(fact: dict, usage_count: int = 0) -> str:
    return "\n".join([
        f"\U0001f4da {fact.get('fact_type', 'Evidence').replace('_', ' ').title()}",
        "",
        str(fact.get("display_value") or fact.get("normalized_claim") or "Unnamed fact"),
        f"Confidence: {fact.get('confidence') or 'unknown'}",
        f"Source: {fact.get('source_document') or fact.get('source_reference') or 'Stored evidence'}",
        f"Verification: {'Confirmed by you' if fact.get('user_verified') else 'Derived from a source document'}",
        f"Used for: {usage_count} current strong matches",
    ])


def evidence_fact_keyboard(fact: dict, category: str, page: int) -> InlineKeyboardMarkup:
    rows = []
    if fact.get("user_verified"):
        fact_type = str(fact.get("fact_type") or "")
        rows.append([InlineKeyboardButton("🗑 Remove Confirmation", callback_data=f"evidence:removeask:{fact_type}:{category}:{page}")])
    rows.append(nav_row(f"evidence:list:{category}:{page}"))
    return InlineKeyboardMarkup(rows)


def feedback_reason_keyboard(job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    rows = []
    reasons = list(NEGATIVE_FEEDBACK_REASONS.items())
    for index in range(0, len(reasons), 2):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"job:feedbackreason:{key}:{job_id}:{origin}:{page}")
            for key, label in reasons[index:index + 2]
        ])
    rows.append(nav_row(f"job:view:{job_id}:{origin}:{page}"))
    return InlineKeyboardMarkup(rows)


def feedback_saved_keyboard(feedback_id: int, job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩ Undo", callback_data=f"job:feedbackundo:{feedback_id}:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("Back to Job", callback_data=f"job:view:{job_id}:{origin}:{page}")],
            nav_row(),
        ]
    )


def feedback_undo_result_keyboard(job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Back to Job", callback_data=f"job:view:{job_id}:{origin}:{page}")],
            nav_row(),
        ]
    )


def government_text(repo, user_id: int) -> str:
    counts = repo.job_counts(user_id)
    public_jobs = len([job for job in repo.list_jobs(user_id) if job.get("public_service")])
    return "\n".join([
        "\U0001f3db GOVERNMENT JOBS",
        "",
        f"Available public-service vacancies: {public_jobs}",
        f"\U0001f525 Best matches: {len([job for job in repo.list_jobs(user_id) if job.get('public_service') and job.get('strong_match')])}",
        f"\u23f0 Closing soon: {counts.get('closing_soon', 0)} total vacancy matches",
        "",
        "Z83 and supporting documents are prepared for your review. Submission and signing remain manual.",
    ])


def government_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f525 Best Matches", callback_data="nav:list:public:0"), InlineKeyboardButton("\U0001f195 New", callback_data="nav:list:new:0")],
        [InlineKeyboardButton("\u23f0 Closing Soon", callback_data="nav:list:urgent:0"), InlineKeyboardButton("\U0001f4c4 Z83 Preparation", callback_data="nav:z83")],
        [InlineKeyboardButton("\U0001f4e6 Application Packs", callback_data="nav:list:public:0")],
        nav_row(),
    ])


def z83_preparation_text(job: dict, profile: dict | None = None) -> str:
    requirements = dict(job.get("application_requirements") or {})
    profile = profile or {}
    lines = [
        "📄 Z83 PREPARATION",
        "",
        "Review this vacancy and complete the official Z83 form manually. JobBlast never signs, certifies, or submits it.",
        "",
        "Vacancy fields to verify:",
        f"- Reference: {job.get('reference_number') or 'Not supplied by source'}",
        f"- Department: {job.get('department') or job.get('company') or 'Not supplied by source'}",
        f"- Position: {job.get('title') or 'Not supplied by source'}",
        f"- Closing date/time: {(job.get('closing_date') or 'Not supplied by source')}{(' ' + str(job.get('closing_time'))) if job.get('closing_time') else ''}",
        f"- Centre/province: {job.get('centre') or job.get('location') or 'Not supplied by source'}{(' / ' + str(job.get('province'))) if job.get('province') else ''}",
        "",
        "Personal fields to review:",
        f"- Name: {profile.get('name') or 'Confirm before completing'}",
        f"- Email: {profile.get('email') or 'Confirm before completing'}",
        f"- Phone: {profile.get('phone') or 'Confirm before completing'}",
        f"- Address: {profile.get('address_line') or 'Confirm before completing'}",
        "",
        "Documents and instructions:",
        "- Detailed CV: review the active version before attaching.",
        f"- Z83 required: {'Yes' if requirements.get('z83_required') else 'Confirm in the official advert'}",
        f"- Supporting documents: {', '.join(str(item) for item in job.get('missing_docs') or []) or 'Review the advert for requirements'}",
        f"- Submission: {job.get('submission_instructions') or 'Follow the official advert exactly.'}",
        "",
        "Manual confirmation required: closing date, vacancy reference, personal details, signatures, certifications, and final submission.",
    ]
    return "\n".join(lines)


def z83_overview_text() -> str:
    return "\n".join(
        [
            "📄 Z83 PREPARATION",
            "",
            "Use the official Z83 form and guide for manual review. JobBlast can prepare vacancy-specific checklists, but it does not sign, certify, or submit applications.",
            "",
            "Open a public-service vacancy to review its reference, department, position, closing date, personal fields, required documents, and official submission instructions.",
        ]
    )


def z83_preparation_keyboard(job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Download Z83 Files", callback_data=f"z83:assets:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("👤 Review Personal Fields", callback_data="set:contact")],
            [InlineKeyboardButton("📋 Submission Checklist", callback_data=f"z83:instructions:{job_id}:{origin}:{page}")],
            [InlineKeyboardButton("Back to Job", callback_data=f"job:view:{job_id}:{origin}:{page}")],
        ]
    )


def z83_overview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([nav_row("nav:government")])


def how_to_apply_text(job: dict) -> str:
    notes = job.get("notes") or []
    missing = job.get("missing_docs") or []
    lines = [
        f"How to apply for {job.get('title', 'this job')}",
        "",
        f"Application method: {job.get('application_method_label', 'Check the vacancy link')}",
        f"Apply link:\n{job.get('apply_url', '') or job.get('listing_url', '')}",
        "",
        "What to do next:",
        f"- {job.get('recommended_next_action', 'Review the vacancy and apply manually.')}",
    ]
    if missing:
        lines.extend(["", "Missing documents:"] + [f"- {item}" for item in missing])
    if notes:
        lines.extend(["", "Preparation notes:"] + [f"- {item}" for item in notes])
    return "\n".join(lines)


def why_text(job: dict) -> str:
    snapshot = job.get("match_snapshot") or {}
    details = snapshot.get("dimension_details") or {}
    eligibility = snapshot.get("eligibility") or {}
    lines = [
        f"Why this match: {job.get('title', 'this vacancy')}",
        "",
        f"Overall: {job.get('fit_score', 0)} | Strong: {'yes' if job.get('strong_match') else 'no'} | Actionable: {'yes' if job.get('actionable') else 'no'}",
        f"Actionability: {str(job.get('actionability_state') or 'not_actionable').replace('_', ' ')}",
        f"Eligibility: {eligibility.get('status', job.get('eligibility_status', 'unknown'))}",
        "",
        "Scoring evidence:",
    ]
    for key, item in details.items():
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        lines.append(f"- {key.replace('_', ' ').title()}: {item['value']} ({item.get('confidence', 'unknown')} confidence) - {item.get('reason', '')}")
    blockers = eligibility.get("blockers") or job.get("eligibility_blockers") or []
    unknowns = eligibility.get("unknowns") or []
    if blockers:
        lines.extend(["", "Hard blockers:"] + [f"- {item.get('type')}: {item.get('requirement')}" for item in blockers])
    if unknowns:
        lines.extend(["", "Needs confirmation:"] + [f"- {item.get('type')}: {item.get('requirement')}" for item in unknowns])
    if job.get("actionability_blockers"):
        lines.extend(["", "Actionability blockers:"] + [f"- {item}" for item in job["actionability_blockers"]])
    if job.get("actionability_preparation"):
        lines.extend(["", "Preparation needed:"] + [f"- {item}" for item in job["actionability_preparation"]])
    lines.extend(["", "Explanation:"] + [f"- {item}" for item in (snapshot.get("explanation") or job.get("why_match") or [])])
    lines.extend(["", f"Engine: {snapshot.get('engine_version', 'unknown')} | Weights: {snapshot.get('weights_version', 'unknown')}"])
    return "\n".join(lines)


def preparation_text(job: dict) -> str:
    plan = dict(job.get("preparation_plan") or {})
    lines = [
        f"Application preparation: {job.get('title', 'this vacancy')}",
        "",
        f"Status: {str(plan.get('status') or job.get('actionability_state') or 'unknown').replace('_', ' ').title()}",
        "",
        "Confirmed evidence:",
    ]
    lines.extend(f"- {item}" for item in plan.get("confirmed_items") or [])
    if not plan.get("confirmed_items"):
        lines.append("- No requirement has been confirmed from the current audit.")
    for title, key in [
        ("Blockers", "blocking_items"),
        ("Required preparation", "preparation_items"),
        ("Needs confirmation", "confirmation_items"),
        ("Optional improvements", "optional_items"),
        ("Generated materials", "generated_items"),
        ("Final manual step", "manual_items"),
    ]:
        values = plan.get(key) or []
        if values:
            lines.extend(["", f"{title}:"] + [f"- {item}" for item in values])
    return "\n".join(lines)


def preparation_keyboard(job: dict, origin: str, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    for item in (job.get("preparation_plan") or {}).get("confirmation_items") or []:
        label = str(item).lower()
        field = "drivers_license" if "driver" in label else "professional_registration" if "registration" in label or "sapc" in label else "qualification" if "qualification" in label or "degree" in label else ""
        if not field or field in seen:
            continue
        seen.add(field)
        title = field.replace("_", " ").title()
        rows.append([
            InlineKeyboardButton(f"I have {title}", callback_data=f"evidence:confirm:yes:{field}:{job['id']}:{origin}:{page}"),
            InlineKeyboardButton(f"I do not have {title}", callback_data=f"evidence:confirm:no:{field}:{job['id']}:{origin}:{page}"),
        ])
    rows.append([
        InlineKeyboardButton("✉️ Cover Letter", callback_data=f"job:cover:{job['id']}:{origin}:{page}"),
        InlineKeyboardButton("📦 Application Pack", callback_data=f"job:pack:{job['id']}:{origin}:{page}"),
    ])
    apply_url = job.get("authoritative_url") or job.get("apply_url") or job.get("listing_url")
    if apply_url:
        rows.append([InlineKeyboardButton("🔗 Official Listing", url=apply_url), InlineKeyboardButton("📨 Mark Applied", callback_data=f"job:mark:{job['id']}:{origin}:{page}")])
    rows.append([InlineKeyboardButton("Back to Job", callback_data=f"job:view:{job['id']}:{origin}:{page}")])
    return InlineKeyboardMarkup(rows)


def evidence_text(repo, user_id: int) -> str:
    facts = repo.get_evidence_facts(user_id)
    grouped: dict[str, list[str]] = {}
    for fact in facts:
        kind = str(fact.get("fact_type") or "other").replace("_", " ").title()
        grouped.setdefault(kind, []).append(str(fact.get("display_value") or fact.get("normalized_claim") or ""))
    lines = ["My evidence", "", "Stored facts are derived from the profile, resume, answers, and active supporting documents."]
    for kind in sorted(grouped):
        lines.extend(["", f"{kind}:"] + [f"- {value}" for value in grouped[kind][:8]])
    if not facts:
        lines.extend(["", "No structured evidence is available yet."])
    lines.extend(["", "User-confirmed facts are managed through the confirmation prompts on preparation plans."])
    return "\n".join(lines)


def cover_letter_text(profile: dict, job: dict, package: dict) -> str:
    return formatted_cover_letter_text(profile, job, package)


def answers_text(job: dict, package: dict) -> str:
    lines = [
        f"Application notes for {job.get('title', 'this job')}",
        "",
        "Suggested answers:",
    ]
    lines.extend(f"- {item}" for item in package.get("suggested_answers", []))
    lines.extend(["", "CV alignment notes:"])
    lines.extend(f"- {item}" for item in package.get("cv_alignment_notes", []))
    return "\n".join(lines)


def gap_analysis_text(job: dict, package: dict) -> str:
    return render_gap_analysis_text(job, package)


def text_result_keyboard(job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([nav_row(f"job:view:{job_id}:{origin}:{page}")])


def cover_letter_result_keyboard(job_id: str, origin: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Download DOCX", callback_data=f"job:coverdoc:{job_id}:{origin}:{page}"),
                InlineKeyboardButton("Application Pack", callback_data=f"job:pack:{job_id}:{origin}:{page}"),
            ],
            nav_row(f"job:view:{job_id}:{origin}:{page}"),
        ]
    )


def settings_text(repo, user_id: int) -> str:
    profile = repo.get_profile(user_id)
    notifications = profile.get("notification_preferences", {})
    public_options = profile.get("public_service_options", {})
    return "\n".join(
        [
            "Settings",
            "",
            f"Target roles: {', '.join(profile.get('target_roles', [])) or 'Not set'}",
            f"Preferred locations: {', '.join(profile.get('preferred_locations', [])) or 'Not set'}",
            f"Keywords: {', '.join(profile.get('keywords', [])) or 'Not set'}",
            f"Excluded keywords: {', '.join(profile.get('excluded_keywords', [])) or 'None'}",
            f"Minimum match threshold: {profile.get('minimum_match_threshold', 18)}",
            f"Cover letter tone: {profile.get('cover_letter_tone', 'confident')}",
            f"Cover letter length: {profile.get('cover_letter_length', 'medium')}",
            (
                "Notifications: "
                f"matches {notifications.get('new_matches')} | "
                f"public service {notifications.get('public_service')} | "
                f"scan errors {notifications.get('scan_errors')} | "
                f"deadlines {notifications.get('deadlines')} | "
                f"follow-ups {notifications.get('follow_ups')}"
            ),
            f"Active resume: {resume_status(repo, user_id)}",
            (
                "Public-service options: "
                f"enabled {public_options.get('enabled')} | "
                f"prioritize {public_options.get('prioritize')} | "
                f"send Z83 files {public_options.get('send_z83_files')}"
            ),
        ]
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Target Roles", callback_data="set:edit:target_roles"), InlineKeyboardButton("Locations", callback_data="set:edit:preferred_locations")],
            [InlineKeyboardButton("Keywords", callback_data="set:edit:keywords"), InlineKeyboardButton("Excluded Keywords", callback_data="set:edit:excluded_keywords")],
            [InlineKeyboardButton("Minimum Match", callback_data="set:edit:minimum_match_threshold"), InlineKeyboardButton("Cover Tone", callback_data="set:tone")],
            [InlineKeyboardButton("Cover Length", callback_data="set:length"), InlineKeyboardButton("Notifications", callback_data="set:notifications")],
            [InlineKeyboardButton("Source Toggles", callback_data="set:sources:0"), InlineKeyboardButton("Source Health", callback_data="set:sourcehealth:0")],
            [InlineKeyboardButton("Active Resume", callback_data="set:resume"), InlineKeyboardButton("Contact / Summary", callback_data="set:contact")],
            [InlineKeyboardButton("My Evidence", callback_data="nav:evidence")],
            [InlineKeyboardButton("Public-Service Options", callback_data="set:public")],
            [InlineKeyboardButton("Reset Jobs / Pipeline", callback_data="set:reset_jobs")],
            nav_row(),
        ]
    )


def reset_jobs_text(repo, user_id: int) -> str:
    counts = repo.job_counts(user_id)
    total_letters = len(repo.list_cover_letters(user_id))
    return "\n".join(
        [
            "Reset Jobs / Pipeline",
            "",
            "This deletes all stored jobs and generated cover letters.",
            "Profile, resume, documents, answers, and source settings are kept.",
            "",
            f"Jobs to remove: {counts.get('total', 0)}",
            f"Cover letters to remove: {total_letters}",
        ]
    )


def reset_jobs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Reset Everything", callback_data="set:reset_jobs:confirm")],
            nav_row("nav:settings"),
        ]
    )


def tone_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for tone in ["confident", "formal", "warm"]:
        label = f"* {tone.title()}" if tone == current else tone.title()
        rows.append([InlineKeyboardButton(label, callback_data=f"set:choice:cover_letter_tone:{tone}")])
    rows.append(nav_row("nav:settings"))
    return InlineKeyboardMarkup(rows)


def length_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for length in ["short", "medium", "long"]:
        label = f"* {length.title()}" if length == current else length.title()
        rows.append([InlineKeyboardButton(label, callback_data=f"set:choice:cover_letter_length:{length}")])
    rows.append(nav_row("nav:settings"))
    return InlineKeyboardMarkup(rows)


def notifications_keyboard(profile: dict) -> InlineKeyboardMarkup:
    prefs = profile.get("notification_preferences", {})
    rows = [
        [InlineKeyboardButton(f"🔥 Strong matches: {'ON' if prefs.get('new_matches') else 'OFF'}", callback_data="set:toggle:notification_preferences:new_matches")],
        [InlineKeyboardButton(f"🏛 Public service: {'ON' if prefs.get('public_service') else 'OFF'}", callback_data="set:toggle:notification_preferences:public_service")],
        [InlineKeyboardButton(f"⚠️ Scan errors: {'ON' if prefs.get('scan_errors') else 'OFF'}", callback_data="set:toggle:notification_preferences:scan_errors")],
        [InlineKeyboardButton(f"⏰ Closing soon: {'ON' if prefs.get('closing_soon') else 'OFF'}", callback_data="set:toggle:notification_preferences:closing_soon")],
        [InlineKeyboardButton(f"📅 Follow-ups: {'ON' if prefs.get('follow_ups') else 'OFF'}", callback_data="set:toggle:notification_preferences:follow_ups")],
        [InlineKeyboardButton(f"🎤 Interviews: {'ON' if prefs.get('interviews') else 'OFF'}", callback_data="set:toggle:notification_preferences:interviews")],
        [InlineKeyboardButton(f"🔄 Important updates: {'ON' if prefs.get('important_updates') else 'OFF'}", callback_data="set:toggle:notification_preferences:important_updates")],
        [InlineKeyboardButton(f"🛡 Trust warnings: {'ON' if prefs.get('trust_warnings') else 'OFF'}", callback_data="set:toggle:notification_preferences:trust_warnings")],
        [InlineKeyboardButton(f"📬 Daily digest: {'ON' if prefs.get('daily_digest') else 'OFF'}", callback_data="set:toggle:notification_preferences:daily_digest")],
        nav_row("nav:settings"),
    ]
    return InlineKeyboardMarkup(rows)


def public_service_keyboard(profile: dict) -> InlineKeyboardMarkup:
    prefs = profile.get("public_service_options", {})
    rows = [
        [InlineKeyboardButton(f"Enabled: {'ON' if prefs.get('enabled') else 'OFF'}", callback_data="set:toggle:public_service_options:enabled")],
        [InlineKeyboardButton(f"Prioritize in Matches: {'ON' if prefs.get('prioritize') else 'OFF'}", callback_data="set:toggle:public_service_options:prioritize")],
        [InlineKeyboardButton(f"Send Z83 Files: {'ON' if prefs.get('send_z83_files') else 'OFF'}", callback_data="set:toggle:public_service_options:send_z83_files")],
        nav_row("nav:settings"),
    ]
    return InlineKeyboardMarkup(rows)


def contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Name", callback_data="set:edit:name"), InlineKeyboardButton("Email", callback_data="set:edit:email")],
            [InlineKeyboardButton("Address", callback_data="set:edit:address_line"), InlineKeyboardButton("City / Postal", callback_data="set:edit:city_province_postal")],
            [InlineKeyboardButton("Phone", callback_data="set:edit:phone"), InlineKeyboardButton("LinkedIn", callback_data="set:edit:linkedin")],
            [InlineKeyboardButton("Resume Summary", callback_data="set:edit:resume_summary")],
            nav_row("nav:settings"),
        ]
    )


def sources_text(sources: list[dict], page: int, per_page: int = 6) -> str:
    start = page * per_page
    subset = sources[start:start + per_page]
    if not subset:
        return "Source Toggles\n\nNo sources configured."
    lines = ["Source Toggles", "", "Use /addsource Name | URL to add a custom source.", ""]
    for item in subset:
        lines.append(
            f"{item.get('name', 'Source')} | {'ON' if item.get('enabled', True) else 'OFF'} | "
            f"{item.get('scan_mode', 'discovery')} | {item.get('last_result') or 'never scanned'}"
        )
    return "\n".join(lines)


def sources_keyboard(sources: list[dict], page: int, per_page: int = 6) -> InlineKeyboardMarkup:
    start = page * per_page
    subset = sources[start:start + per_page]
    rows = []
    for item in subset:
        icon = "\U0001F7E2" if item.get("enabled", True) else "\u26AA"
        label = f"{icon} {short(item.get('name', 'Source'), 28)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"src:toggle:{item['id']}:{page}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("< Prev", callback_data=f"set:sources:{page - 1}"))
    if start + per_page < len(sources):
        nav_buttons.append(InlineKeyboardButton("Next >", callback_data=f"set:sources:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(nav_row("nav:settings"))
    return InlineKeyboardMarkup(rows)


def source_health_text(sources: list[dict], page: int, per_page: int = 5) -> str:
    start = page * per_page
    subset = sources[start:start + per_page]
    if not subset:
        return "Source Health\n\nNo sources configured."
    lines = ["Source Health", ""]
    for item in subset:
        lines.extend(
            [
                f"{item.get('name', 'Source')} | {source_health_badge(item)}",
                (
                    f"Last: {item.get('last_result') or 'never scanned'} | "
                    f"Scans {int(item.get('scan_count') or 0)} | "
                    f"OK {int(item.get('success_count') or 0)} | "
                    f"Warn {int(item.get('warning_count') or 0)} | "
                    f"Err {int(item.get('error_count') or 0)}"
                ),
                (
                    f"New total {int(item.get('total_new_jobs') or 0)} | "
                    f"Matched total {int(item.get('total_matched_jobs') or 0)} | "
                    f"Zero-result streak {int(item.get('consecutive_zero_results') or 0)}"
                ),
                (
                    f"Last attempt {item.get('last_attempt') or 'never'} | "
                    f"Last success {item.get('last_success') or 'never'} | "
                    f"Failure: {item.get('last_failure_category') or 'none'}"
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def source_health_keyboard(sources: list[dict], page: int, per_page: int = 5) -> InlineKeyboardMarkup:
    start = page * per_page
    rows: list[list[InlineKeyboardButton]] = []
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("< Prev", callback_data=f"set:sourcehealth:{page - 1}"))
    if start + per_page < len(sources):
        nav_buttons.append(InlineKeyboardButton("Next >", callback_data=f"set:sourcehealth:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(nav_row("nav:settings"))
    return InlineKeyboardMarkup(rows)


def resume_menu_text(repo, user_id: int) -> str:
    profile = repo.get_profile(user_id)
    docs = repo.list_documents(user_id)
    lines = [
        "Active Resume and Documents",
        "",
        f"Resume: {resume_status(repo, user_id)}",
        f"Active resume file: {profile.get('active_resume_path', 'not set')}",
        f"Supporting docs: {document_summary(repo, user_id)}",
        "",
    ]
    if docs:
        lines.append("Documents:")
        for item in docs[:8]:
            status = "active" if item.get("active", True) else "inactive"
            lines.append(f"- {item.get('id')} | {item.get('original_name')} | {item.get('doc_type')} | {status}")
    else:
        lines.append("No supporting documents uploaded yet.")
    return "\n".join(lines)


def resume_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Upload Resume", callback_data="set:upload:resume"), InlineKeyboardButton("Upload Document", callback_data="set:upload:document")],
            [InlineKeyboardButton("Refresh Resume Parse", callback_data="set:resume_refresh"), InlineKeyboardButton("Manage Documents", callback_data="set:docs:0")],
            nav_row("nav:settings"),
        ]
    )


def docs_keyboard(documents: list[dict], page: int, per_page: int = 5) -> InlineKeyboardMarkup:
    start = page * per_page
    subset = documents[start:start + per_page]
    rows = []
    for item in subset:
        icon = "\U0001F7E2" if item.get("active", True) else "\u26AA"
        label = f"{icon} {short(item.get('original_name', 'document'), 26)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"doc:view:{item['id']}:{page}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("< Prev", callback_data=f"set:docs:{page - 1}"))
    if start + per_page < len(documents):
        nav_buttons.append(InlineKeyboardButton("Next >", callback_data=f"set:docs:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(nav_row("set:resume"))
    return InlineKeyboardMarkup(rows)


def doc_detail_text(doc: dict) -> str:
    return "\n".join(
        [
            f"{doc.get('original_name', 'Document')}",
            f"Type: {doc.get('doc_type', 'supporting_doc')}",
            f"Status: {'active' if doc.get('active', True) else 'inactive'}",
            f"Tags: {', '.join(doc.get('tags', [])) or 'None'}",
        ]
    )


def doc_detail_keyboard(doc: dict, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Toggle Active", callback_data=f"doc:toggle:{doc['id']}:{page}"), InlineKeyboardButton("Cycle Type", callback_data=f"doc:type:{doc['id']}:{page}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"doc:delete:{doc['id']}:{page}")],
            nav_row(f"set:docs:{page}"),
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "Help",
            "",
            "JobBlast now works as a manual-apply assistant.",
            "",
            "What it does:",
            "- scans multiple job sources",
            "- ranks jobs against your profile and resume",
            "- explains how to apply",
            "- generates tailored cover letters, gap analysis, and suggested answers",
            "- exports application packs and DOCX cover letters",
            "- reminds you about deadlines and follow-ups",
            "- tracks source health so weak or broken sources are visible",
            "- supports public-service and Z83 guidance",
            "",
            "Main commands:",
            "/start",
            "/menu",
            "/scan",
            "/settings",
            "/status",
            "/export",
            "/feedback [reset]",
            "/persona [id]",
            "/addsource Name | URL",
            "/z83 [job_id]",
            "/refreshresume",
        ]
    )


def feedback_text(repo, user_id: int) -> str:
    events = repo.list_feedback(user_id)
    relevant = sum(1 for event in events if event.get("label") == "relevant")
    not_for_me = sum(1 for event in events if event.get("label") == "not_for_me")
    reasons: dict[str, int] = {}
    for event in events:
        reason = str(event.get("reason") or "unspecified")
        reasons[reason] = reasons.get(reason, 0) + 1
    lines = [
        "Feedback learning",
        "",
        f"Relevant: {relevant}",
        f"Not for me: {not_for_me}",
        "",
        "Stored reasons:",
    ]
    lines.extend(f"- {reason}: {count}" for reason, count in sorted(reasons.items()))
    lines.append("\nUse /feedback reset to clear learned feedback.")
    return "\n".join(lines)


def personas_text(repo, user_id: int) -> str:
    from core.personas import personas_for_profile

    profile = repo.get_profile(user_id)
    active_id = str(profile.get("active_persona_id") or "default")
    lines = ["Search Personas", ""]
    for persona in personas_for_profile(profile):
        marker = "ACTIVE" if str(persona.get("id")) == active_id else ""
        roles = ", ".join(persona.get("target_roles") or []) or "No target roles set"
        lines.append(f"{persona.get('name', persona.get('id'))} {marker}\nRoles: {roles}")
    lines.append("\nUse /persona add or /persona <id> to manage targeting.")
    return "\n\n".join(lines)


def personas_keyboard(repo, user_id: int) -> InlineKeyboardMarkup:
    from core.personas import personas_for_profile

    rows = []
    for persona in personas_for_profile(repo.get_profile(user_id)):
        persona_id = persona.get("id")
        rows.append([InlineKeyboardButton(f"✅ Activate {short(persona.get('name', persona_id), 24)}", callback_data=f"persona:select:{persona_id}"), InlineKeyboardButton("🔎 Scan", callback_data=f"persona:scan:{persona_id}")])
    rows.append([InlineKeyboardButton("📊 Active Results", callback_data="nav:list:top:0"), InlineKeyboardButton("⚙️ Preferences", callback_data="nav:settings")])
    rows.append(nav_row())
    return InlineKeyboardMarkup(rows)


def status_text(repo, user_id: int, running_pid: int | None = None) -> str:
    status = bot_status(repo, user_id, running_pid=running_pid)
    counts = status["counts"]
    sources = status["source_stats"]
    return "\n".join(
        [
            "JobBlast Status",
            "",
            f"Process: {'running' if running_pid else 'unknown'}" + (f" | PID {running_pid}" if running_pid else ""),
            f"Last scan: {status['last_scan_at'] or 'not recorded'}",
            f"Sources: {sources['enabled']}/{sources['total']} enabled | warnings {status['source_warnings']} | errors {status['source_errors']}",
            f"Jobs: {counts['total']} total | {counts['new']} new | {counts['matched']} matched",
            f"Pipeline: {counts['saved']} saved | {counts['needs_documents']} needs docs | {counts['ready_for_manual_apply']} ready | {counts['applied_by_user']} applied",
            f"Urgent this week: {status['urgent_jobs']} | Stale review: {status['stale_jobs']}",
            f"Documents: {status['documents']} | Cover letters: {status['cover_letters']}",
        ]
    )


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Scan Jobs", callback_data="nav:scan"),
                InlineKeyboardButton("Source Health", callback_data="set:sourcehealth:0"),
            ],
            [
                InlineKeyboardButton("Urgent This Week", callback_data="nav:list:urgent:0"),
                InlineKeyboardButton("Export Pipeline", callback_data="nav:export"),
            ],
            nav_row(),
        ]
    )
