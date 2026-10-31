"""Telegram handlers for the manual-apply JobBlast assistant."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from io import BytesIO

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, TelegramError
from telegram.ext import ContextTypes

from bot import views
from bot.authorization import is_authorized
from core.personas import personas_for_profile, select_persona, upsert_persona
from core.profile import apply_profile_input, normalize_string_list, settings_prompt, toggle_nested_option
from services.application_pack import export_application_pack
from services.cover_letter_docs import export_cover_letter_docx
from services.cover_letters import generate_cover_package
from services.documents import (
    document_summary,
    refresh_resume_profile,
    save_resume_bytes,
    save_supporting_document,
)
from services.upload_validation import MAX_UPLOAD_BYTES, UploadValidationError
from services.job_views import filter_jobs
from services.pipeline_export import export_jobs_csv
from services.public_service import ensure_z83_assets, public_service_text
from services.reminders import collect_job_reminders
from services.scanner import run_scan
from storage.repository import Repository

logger = logging.getLogger(__name__)
DOC_TYPE_ORDER = ["supporting_doc", "certificate", "license", "portfolio", "cover_letter"]


class ManualApplyBot:
    """Telegram UI controller for the manual-apply assistant."""

    def __init__(self, repo: Repository, allowed_users: frozenset[int] | None = None):
        self.repo = repo
        self.allowed_users = allowed_users or frozenset()
        self._active_scans: dict[int, asyncio.Task] = {}

    async def _authorized(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user = update.effective_user
        if is_authorized(user.id if user else None, self.allowed_users):
            return True
        query = update.callback_query
        if query is not None:
            try:
                await query.answer("Access denied.", show_alert=True)
            except TelegramError:
                logger.debug("Unauthorized callback could not be acknowledged", exc_info=True)
            return False
        if update.effective_chat:
            try:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="Access denied.")
            except TelegramError:
                logger.debug("Unauthorized update could not be acknowledged", exc_info=True)
        return False

    async def _typing(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        try:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        except TelegramError:
            logger.debug("Typing action failed", exc_info=True)

    async def _safe_query_answer(self, query) -> None:
        try:
            await query.answer()
        except TelegramError:
            logger.debug("Ignoring callback answer failure", exc_info=True)

    async def _render(
        self,
        *,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup,
        update: Update | None = None,
        query=None,
    ) -> None:
        if query is not None and query.message:
            try:
                await query.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
            except BadRequest as exc:
                lowered = str(exc).lower()
                if "message is not modified" not in lowered:
                    raise
            except TelegramError:
                logger.warning("Telegram menu edit failed user_id=%s; leaving the current screen intact", user_id)
                return
            self.repo.save_ui_state(user_id, menu_chat_id=query.message.chat.id, menu_message_id=query.message.message_id)
            return

        state = self.repo.get_ui_state(user_id)
        chat_id = update.effective_chat.id if update and update.effective_chat else state.get("menu_chat_id")
        if state.get("menu_chat_id") and state.get("menu_message_id"):
            try:
                await context.bot.edit_message_text(
                    chat_id=state["menu_chat_id"],
                    message_id=state["menu_message_id"],
                    text=text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return
            except TelegramError:
                logger.info("Menu edit failed for user_id=%s, sending a fresh menu message.", user_id)
        if chat_id is None:
            raise RuntimeError("No chat id available to render the menu.")
        try:
            message = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, disable_web_page_preview=True)
        except TelegramError:
            logger.warning("Telegram menu send failed user_id=%s", user_id)
            return
        self.repo.save_ui_state(user_id, menu_chat_id=chat_id, menu_message_id=message.message_id)

    async def _reply_transient(
        self,
        update: Update | None,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        text: str,
    ) -> None:
        if update and update.effective_chat:
            try:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text, disable_web_page_preview=True)
            except TelegramError:
                logger.warning("Telegram transient message failed user_id=%s", user_id)
            return
        state = self.repo.get_ui_state(user_id)
        if state.get("menu_chat_id"):
            try:
                await context.bot.send_message(chat_id=state["menu_chat_id"], text=text, disable_web_page_preview=True)
            except TelegramError:
                logger.warning("Telegram transient message failed user_id=%s", user_id)

    def _jobs_for_origin(self, user_id: int, origin: str) -> list[dict]:
        return filter_jobs(self.repo.list_jobs(user_id, include_hidden=False), origin)

    async def _show_doc_detail(self, user_id: int, doc_id: str, page: int, context: ContextTypes.DEFAULT_TYPE, query) -> None:
        doc = next((item for item in self.repo.list_documents(user_id) if item.get("id") == doc_id), None)
        if not doc:
            await self._render(context=context, user_id=user_id, text="Document not found.", keyboard=views.resume_menu_keyboard(), query=query)
            return
        await self._render(
            context=context,
            user_id=user_id,
            text=views.doc_detail_text(doc),
            keyboard=views.doc_detail_keyboard(doc, page),
            query=query,
        )

    async def _ensure_cover_package(self, user_id: int, job: dict) -> dict:
        package = self.repo.get_cover_letter(user_id, job["id"])
        if package:
            return package
        profile = self.repo.get_profile(user_id)
        answers = self.repo.get_answers(user_id)
        resume_profile = self.repo.get_resume_profile(user_id)
        package = await generate_cover_package(profile, resume_profile, answers, job)
        self.repo.save_cover_letter(user_id, job["id"], package)
        return package

    async def show_home(self, update: Update | None, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
        user_id = query.from_user.id if query else update.effective_user.id
        await self._render(
            context=context,
            user_id=user_id,
            text=views.home_text(self.repo, user_id),
            keyboard=views.home_keyboard(),
            update=update,
            query=query,
        )

    async def show_applications(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
        await self._render(
            context=context,
            user_id=user_id,
            text=views.applications_text(self.repo, user_id),
            keyboard=views.applications_keyboard(),
            query=query,
        )

    async def show_government(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
        await self._render(
            context=context,
            user_id=user_id,
            text=views.government_text(self.repo, user_id),
            keyboard=views.government_keyboard(),
            query=query,
        )

    async def _show_stale_job(self, user_id: int, origin: str, page: int, context: ContextTypes.DEFAULT_TYPE, query) -> None:
        await self._render(
            context=context,
            user_id=user_id,
            text="ℹ️ This vacancy is no longer available in the current JobBlast record. It may have expired, been merged, or been removed.",
            keyboard=InlineKeyboardMarkup([views.nav_row(f"nav:list:{origin}:{page}")]),
            query=query,
        )

    async def show_list(self, user_id: int, origin: str, page: int, context: ContextTypes.DEFAULT_TYPE, query) -> None:
        title_map = {
            "top": "🔥 For You",
            "new": "🆕 New Jobs",
            "pipeline": "📂 Pipeline",
            "saved": "⭐ Saved",
            "ready": "🟢 Ready to Apply",
            "preparing": "🧰 Preparation",
            "applied": "📨 Applied",
            "interviews": "🎤 Interviews",
            "offers": "💼 Offers",
            "accepted": "✅ Accepted",
            "rejected": "❌ Rejected",
            "withdrawn": "↩️ Withdrawn",
            "letters": "✉️ Cover Letters",
            "public": "🏛 Government Jobs",
            "urgent": "⏰ Closing Soon",
            "stale": "⚠️ Stale Review",
        }
        jobs = self._jobs_for_origin(user_id, origin)
        await self._render(
            context=context,
            user_id=user_id,
            text=views.list_screen_text(title_map.get(origin, "Jobs"), jobs, page),
            keyboard=views.list_keyboard(jobs, origin, page),
            query=query,
        )

    async def show_job(self, user_id: int, job_id: str, origin: str, page: int, context: ContextTypes.DEFAULT_TYPE, query) -> None:
        job = self.repo.mark_job_viewed(user_id, job_id)
        if not job:
            await self._show_stale_job(user_id, origin, page, context, query)
            return
        await self._render(
            context=context,
            user_id=user_id,
            text=views.job_card_text(job),
            keyboard=views.job_card_keyboard(job, origin, page),
            query=query,
        )

    async def show_settings(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query) -> None:
        await self._render(
            context=context,
            user_id=user_id,
            text=views.settings_text(self.repo, user_id),
            keyboard=views.settings_keyboard(),
            query=query,
        )

    async def show_status(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None, update: Update | None = None) -> None:
        await self._render(
            context=context,
            user_id=user_id,
            text=views.status_text(self.repo, user_id, running_pid=os.getpid()),
            keyboard=views.status_keyboard(),
            update=update,
            query=query,
        )

    async def send_pipeline_export(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None, update: Update | None = None) -> None:
        jobs = filter_jobs(self.repo.list_jobs(user_id, include_hidden=True), "pipeline")
        payload = export_jobs_csv(jobs)
        filename = f"jobblast_pipeline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        chat_id = update.effective_chat.id if update and update.effective_chat else user_id
        await context.bot.send_document(
            chat_id=chat_id,
            document=BytesIO(payload),
            filename=filename,
            caption=f"JobBlast pipeline export: {len(jobs)} job(s)",
        )
        if query is not None:
            await self.show_status(user_id, context, query=query)

    async def _send_z83_assets(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
        try:
            assets = await ensure_z83_assets(self.repo.base_dir)
            await context.bot.send_document(chat_id=user_id, document=assets["editable_z83"].read_bytes(), filename="Editable_Z83.pdf", caption="Editable Z83 form for manual review")
            await context.bot.send_document(chat_id=user_id, document=assets["z83_guide"].read_bytes(), filename="Z83_Guide.pdf", caption="Z83 completion guide")
            if query is not None:
                await self._render(context=context, user_id=user_id, text=views.z83_overview_text(), keyboard=views.z83_overview_keyboard(), query=query)
        except Exception:
            logger.exception("Z83 assets failed user_id=%s", user_id)
            await self._render(context=context, user_id=user_id, text="⚠️ I could not load the Z83 files. Please try again later.", keyboard=InlineKeyboardMarkup([views.nav_row("nav:government")]), query=query)

    async def _show_z83_preparation(self, user_id: int, job_id: str, origin: str, page: int, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
        job = self.repo.get_job(user_id, job_id)
        if not job:
            await self._show_stale_job(user_id, origin, page, context, query)
            return
        await self._render(
            context=context,
            user_id=user_id,
            text=views.z83_preparation_text(job, self.repo.get_profile(user_id)),
            keyboard=views.z83_preparation_keyboard(job_id, origin, page),
            query=query,
        )

    def _scan_is_running(self, user_id: int) -> bool:
        task = self._active_scans.get(user_id)
        return task is not None and not task.done()

    def _clear_scan_task(self, user_id: int, task: asyncio.Task) -> None:
        current = self._active_scans.get(user_id)
        if current is task:
            self._active_scans.pop(user_id, None)

    async def _run_scan_background(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            async def progress(source_result: dict, done: int, total: int) -> None:
                if done == 1 or done == total or done % 5 == 0 or source_result.get("error"):
                    await self._reply_transient(
                        None,
                        context,
                        user_id,
                        (
                            f"Scan progress {done}/{total}: {source_result['source_name']} | "
                            f"{source_result['new']} new / {source_result['matched']} matched"
                            + (" | warning" if source_result.get("warning") else "")
                            + (" | error" if source_result.get("error") else "")
                        ),
                    )

            result = await run_scan(self.repo, user_id, progress_callback=progress)
            text = "\n".join(
                [
                    "Scan complete",
                    "",
                    f"New jobs: {len(result['new_jobs'])}",
                    f"Reconciled: {len(result.get('reconciled_jobs', []))}",
                    f"Persisted by scanner: {len(result.get('persisted_jobs', []))}",
                    f"Eligible: {result.get('eligible_count', 0)} | Strong matches: {result.get('strong_match_count', 0)}",
                    f"New public-service matches: {result.get('new_public_matches', 0)}",
                    f"Source errors: {len(result['errors'])}",
                    *([f"Source warnings: {len(result['warnings'])}"] if result.get("warnings") else []),
                    "",
                    "Use Top Matches, New Jobs, or Public Service to review the results.",
                ]
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [views.nav_row()[0], views.nav_row()[1]],
                    [views.home_keyboard().inline_keyboard[0][1], views.home_keyboard().inline_keyboard[1][0]],
                ]
            )
            await self._render(context=context, user_id=user_id, text=text, keyboard=keyboard)
        except Exception as exc:
            logger.exception("Background scan failed user_id=%s", user_id)
            await self._reply_transient(
                None,
                context,
                user_id,
                "JobBlast could not complete the scan. The failure was logged; use 🔎 Scan to try again later.",
            )

    async def _start_scan(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None, update: Update | None = None) -> None:
        if self._scan_is_running(user_id):
            await self._reply_transient(
                update,
                context,
                user_id,
                "A scan is already running. I’ll update the menu when it finishes.",
            )
            return

        placeholder = "Scanning enabled sources...\n\nJobBlast stays in manual-apply mode and only prepares ranked matches. You can keep using the bot while this runs."
        await self._render(
            context=context,
            user_id=user_id,
            text=placeholder,
            keyboard=InlineKeyboardMarkup([views.nav_row()]),
            update=update,
            query=query,
        )
        task = context.application.create_task(self._run_scan_background(user_id, context))
        self._active_scans[user_id] = task
        task.add_done_callback(lambda finished, uid=user_id: self._clear_scan_task(uid, finished))

    async def run_scan_and_render(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, query=None, update: Update | None = None) -> None:
        await self._start_scan(user_id, context, query=query, update=update)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        self.repo.get_profile(user_id)
        self.repo.get_answers(user_id)
        await self.show_home(update, context)

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self.show_home(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        await self._render(
            context=context,
            user_id=user_id,
            text=views.help_text(),
            keyboard=InlineKeyboardMarkup([views.nav_row()]),
            update=update,
        )

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self.run_scan_and_render(update.effective_user.id, context, update=update)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self.show_status(update.effective_user.id, context, update=update)

    async def export_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self.send_pipeline_export(update.effective_user.id, context, update=update)

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        await self._render(
            context=context,
            user_id=user_id,
            text=views.settings_text(self.repo, user_id),
            keyboard=views.settings_keyboard(),
            update=update,
        )

    async def refresh_resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        ok, message, parsed = refresh_resume_profile(self.repo, user_id)
        text = f"{'Success' if ok else 'Resume issue'}\n\n{message}\nSearch terms: {', '.join(parsed.get('search_terms', [])[:8]) or 'none'}"
        await self._render(
            context=context,
            user_id=user_id,
            text=text,
            keyboard=views.resume_menu_keyboard(),
            update=update,
        )

    async def addsource_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        raw = update.message.text.partition(" ")[2].strip()
        if "|" not in raw:
            await update.message.reply_text("Usage: /addsource Name | https://example.com/jobs")
            return
        name, _, url = raw.partition("|")
        try:
            source = self.repo.add_custom_source(user_id, name, url)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        await update.message.reply_text(f"Added source: {source['name']} | {source['seed_url']}")
        sources = self.repo.list_sources(user_id)
        await self._render(
            context=context,
            user_id=user_id,
            text=views.sources_text(sources, 0),
            keyboard=views.sources_keyboard(sources, 0),
            update=update,
        )

    async def z83_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        job = self.repo.get_job(user_id, context.args[0]) if context.args else None
        assets = await ensure_z83_assets(self.repo.base_dir)
        if job:
            await update.message.reply_text(public_service_text(job))
        else:
            await update.message.reply_text("Public-service / Z83 support\n\nUse /z83 <job_id> for a vacancy-specific summary.")
        await context.bot.send_document(chat_id=user_id, document=assets["editable_z83"].read_bytes(), filename="Editable_Z83.pdf", caption="Editable Z83 form")
        await context.bot.send_document(chat_id=user_id, document=assets["z83_guide"].read_bytes(), filename="Z83_Guide.pdf", caption="Official Z83 guide")

    async def persona_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        profile = self.repo.get_profile(user_id)
        personas = personas_for_profile(profile)
        requested = context.args[0] if context.args else ""
        if requested == "add":
            raw = update.message.text.partition(" ")[2].partition(" ")[2]
            parts = [part.strip() for part in raw.split("|")]
            if len(parts) != 4:
                await update.message.reply_text("Usage: /persona add id | name | role 1, role 2 | location 1, location 2")
                return
            updated = upsert_persona(profile, {
                "id": parts[0], "name": parts[1],
                "target_roles": normalize_string_list(parts[2]),
                "preferred_locations": normalize_string_list(parts[3]),
                "keywords": normalize_string_list(parts[2]),
                "excluded_keywords": [], "blocked_employers": [],
            })
            self.repo.save_profile(user_id, updated)
            await update.message.reply_text(f"Persona saved: {parts[1]}")
            return
        if requested:
            selected = select_persona(profile, requested)
            if not selected:
                await update.message.reply_text("Persona not found. Use /persona to list available personas.")
                return
            profile["active_persona_id"] = str(selected["id"])
            self.repo.save_profile(user_id, profile)
            await update.message.reply_text(f"Active persona: {selected.get('name', selected['id'])}")
            return
        active_id = str(profile.get("active_persona_id") or "default")
        lines = ["Personas:"]
        lines.extend(f"{'*' if str(item.get('id')) == active_id else '-'} {item.get('id')}: {item.get('name', item.get('id'))}" for item in personas)
        lines.append("\nUse /persona <id> to switch search persona.")
        await update.message.reply_text("\n".join(lines))

    async def feedback_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        if context.args and context.args[0].lower() == "reset":
            self.repo.clear_feedback(user_id)
            await update.message.reply_text("Feedback learning reset.")
            return
        await update.message.reply_text(views.feedback_text(self.repo, user_id))

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Authorize and shield every callback so stale Telegram messages cannot break polling."""
        if not await self._authorized(update, context):
            return
        query = update.callback_query
        if query is None:
            return
        await self._safe_query_answer(query)
        try:
            await self._callback_dispatch(update, context)
        except Exception:
            logger.exception("Callback failed user_id=%s data=%s", query.from_user.id, query.data)
            await self._render(
                context=context,
                user_id=query.from_user.id,
                text="⚠️ That action could not be completed. The current menu is still available.",
                keyboard=views.home_keyboard(),
                query=query,
            )

    async def _callback_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        user_id = query.from_user.id
        data = query.data or ""

        if data == "nav:home":
            self.repo.clear_pending_input(user_id)
            await self.show_home(None, context, query=query)
            return
        if data == "nav:scan":
            await self.run_scan_and_render(user_id, context, query=query)
            return
        if data == "nav:settings":
            self.repo.clear_pending_input(user_id)
            await self.show_settings(user_id, context, query)
            return
        if data == "nav:applications":
            self.repo.clear_pending_input(user_id)
            await self.show_applications(user_id, context, query)
            return
        if data == "nav:government":
            self.repo.clear_pending_input(user_id)
            await self.show_government(user_id, context, query)
            return
        if data == "nav:documents":
            self.repo.clear_pending_input(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.resume_menu_text(self.repo, user_id),
                keyboard=views.resume_menu_keyboard(),
                query=query,
            )
            return
        if data == "nav:z83":
            await self._send_z83_assets(user_id, context, query)
            return
        if data == "nav:help":
            await self._render(context=context, user_id=user_id, text=views.help_text(), keyboard=InlineKeyboardMarkup([views.nav_row()]), query=query)
            return
        if data == "nav:personas":
            await self._render(
                context=context,
                user_id=user_id,
                text=views.personas_text(self.repo, user_id),
                keyboard=views.personas_keyboard(self.repo, user_id),
                query=query,
            )
            return
        if data == "nav:evidence":
            self.repo.clear_pending_input(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.evidence_text(self.repo, user_id),
                keyboard=views.evidence_keyboard(),
                query=query,
            )
            return
        if data == "nav:status":
            await self.show_status(user_id, context, query=query)
            return
        if data == "nav:export":
            await self.send_pipeline_export(user_id, context, query=query)
            return
        if data.startswith("evidence:list:"):
            _, _, category, raw_page = data.split(":", 3)
            facts = [fact for fact in self.repo.get_evidence_facts(user_id) if str(fact.get("fact_type") or "other") == category]
            page = max(0, int(raw_page))
            await self._render(
                context=context,
                user_id=user_id,
                text=views.evidence_list_text(facts, category, page),
                keyboard=views.evidence_list_keyboard(facts, category, page),
                query=query,
            )
            return
        if data.startswith("evidence:view:"):
            _, _, raw_index, category, raw_page = data.split(":", 4)
            facts = [fact for fact in self.repo.get_evidence_facts(user_id) if str(fact.get("fact_type") or "other") == category]
            index = int(raw_index)
            if index < 0 or index >= len(facts):
                await self._render(context=context, user_id=user_id, text="⚠️ That evidence item is no longer available.", keyboard=InlineKeyboardMarkup([views.nav_row("nav:evidence")]), query=query)
                return
            fact = facts[index]
            usage_count = sum(1 for job in self.repo.list_jobs(user_id) if any(str(fact.get("normalized_value") or "").lower() in str(item).lower() for item in (job.get("why_match") or [])))
            await self._render(
                context=context,
                user_id=user_id,
                text=views.evidence_fact_text(fact, usage_count),
                keyboard=views.evidence_fact_keyboard(fact, category, int(raw_page)),
                query=query,
            )
            return
        if data.startswith("evidence:removeask:"):
            _, _, fact_type, category, page = data.split(":", 4)
            await self._render(
                context=context,
                user_id=user_id,
                text="⚠️ Remove this user confirmation? Source-document evidence will be preserved.",
                keyboard=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, remove", callback_data=f"evidence:remove:{fact_type}:{category}:{page}"), InlineKeyboardButton("❌ Cancel", callback_data=f"evidence:list:{category}:{page}")],
                ]),
                query=query,
            )
            return
        if data.startswith("evidence:remove:"):
            _, _, fact_type, category, page = data.split(":", 4)
            self.repo.remove_confirmed_fact(user_id, fact_type)
            facts = [fact for fact in self.repo.get_evidence_facts(user_id) if str(fact.get("fact_type") or "other") == category]
            await self._render(context=context, user_id=user_id, text=views.evidence_list_text(facts, category, int(page)), keyboard=views.evidence_list_keyboard(facts, category, int(page)), query=query)
            return
        if data.startswith("nav:list:"):
            self.repo.clear_pending_input(user_id)
            _, _, origin, page = data.split(":", 3)
            await self.show_list(user_id, origin, int(page), context, query)
            return

        if data.startswith("job:view:"):
            _, _, job_id, origin, page = data.split(":", 4)
            await self.show_job(user_id, job_id, origin, int(page), context, query)
            return

        if data.startswith("app:view:"):
            self.repo.clear_pending_input(user_id)
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            await self._render(context=context, user_id=user_id, text=views.application_text(job), keyboard=views.application_keyboard(job, origin, int(page)), query=query)
            return

        if data.startswith("app:state:"):
            _, _, state, job_id, origin, page = data.split(":", 5)
            job = self.repo.update_job_state(user_id, job_id, state)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            await self._render(context=context, user_id=user_id, text=views.application_text(job), keyboard=views.application_keyboard(job, origin, int(page)), query=query)
            return

        if data.startswith("app:edit:"):
            _, _, field, job_id, origin, page = data.split(":", 5)
            if not self.repo.get_job(user_id, job_id):
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            self.repo.save_ui_state(user_id, pending_input={"type": "job_field", "field": field, "job_id": job_id, "origin": origin, "page": int(page)})
            await self._render(
                context=context,
                user_id=user_id,
                text=f"📝 Application update\n\n{views.lifecycle_prompt(field)}",
                keyboard=InlineKeyboardMarkup([views.nav_row(f"app:view:{job_id}:{origin}:{page}")]),
                query=query,
            )
            return

        if data.startswith("persona:select:"):
            persona_id = data.split(":", 2)[2]
            profile = self.repo.get_profile(user_id)
            selected = select_persona(profile, persona_id)
            if selected:
                profile["active_persona_id"] = str(selected["id"])
                self.repo.save_profile(user_id, profile)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.personas_text(self.repo, user_id),
                keyboard=views.personas_keyboard(self.repo, user_id),
                query=query,
            )
            return

        if data.startswith("persona:scan:"):
            persona_id = data.split(":", 2)[2]
            profile = self.repo.get_profile(user_id)
            selected = select_persona(profile, persona_id)
            if selected:
                profile["active_persona_id"] = str(selected["id"])
                self.repo.save_profile(user_id, profile)
            await self.run_scan_and_render(user_id, context, query=query)
            return

        if data.startswith("job:how:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            await self._render(
                context=context,
                user_id=user_id,
                text=views.how_to_apply_text(job),
                keyboard=views.text_result_keyboard(job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:why:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            await self._render(
                context=context,
                user_id=user_id,
                text=views.why_text(job),
                keyboard=views.text_result_keyboard(job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:prepare:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            await self._render(
                context=context,
                user_id=user_id,
                text=views.preparation_text(job),
                keyboard=views.preparation_keyboard(job, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("evidence:confirm:"):
            _, _, answer, field, job_id, origin, page = data.split(":", 6)
            self.repo.save_confirmed_fact(user_id, field, "yes" if answer == "yes" else "no")
            job = self.repo.recalculate_job_readiness(user_id, job_id)
            if job:
                await self._render(
                    context=context,
                    user_id=user_id,
                    text=views.preparation_text(job),
                    keyboard=views.preparation_keyboard(job, origin, int(page)),
                    query=query,
                )
            else:
                await self._show_stale_job(user_id, origin, int(page), context, query)
            return

        if data.startswith("job:cover:") or data.startswith("job:answers:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            package = await self._ensure_cover_package(user_id, job)
            profile = self.repo.get_profile(user_id)
            text = views.cover_letter_text(profile, job, package) if data.startswith("job:cover:") else views.answers_text(job, package)
            await self._render(
                context=context,
                user_id=user_id,
                text=text,
                keyboard=views.cover_letter_result_keyboard(job_id, origin, int(page))
                if data.startswith("job:cover:")
                else views.text_result_keyboard(job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:gap:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            package = await self._ensure_cover_package(user_id, job)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.gap_analysis_text(job, package),
                keyboard=views.text_result_keyboard(job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:pack:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            try:
                package = await self._ensure_cover_package(user_id, job)
                profile = self.repo.get_profile(user_id)
                filename, payload = export_application_pack(profile, job, package)
                await context.bot.send_document(
                    chat_id=user_id,
                    document=BytesIO(payload),
                    filename=filename,
                    caption=f"Application pack for {job.get('title', 'this role')}",
                )
                await self.show_job(user_id, job_id, origin, int(page), context, query)
            except Exception as exc:
                logger.exception("Application pack export failed user_id=%s job_id=%s", user_id, job_id)
                await self._render(
                    context=context,
                    user_id=user_id,
                    text="⚠️ I could not build that application pack. Your vacancy is still available.",
                    keyboard=views.text_result_keyboard(job_id, origin, int(page)),
                    query=query,
                )
            return

        if data.startswith("job:coverdoc:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            try:
                package = await self._ensure_cover_package(user_id, job)
                profile = self.repo.get_profile(user_id)
                filename, payload = export_cover_letter_docx(profile, job, package)
                await context.bot.send_document(
                    chat_id=user_id,
                    document=BytesIO(payload),
                    filename=filename,
                    caption=f"Cover letter DOCX for {job.get('title', 'this role')}",
                )
                await self._render(
                    context=context,
                    user_id=user_id,
                    text=views.cover_letter_text(profile, job, package),
                    keyboard=views.cover_letter_result_keyboard(job_id, origin, int(page)),
                    query=query,
                )
            except Exception as exc:
                logger.exception("Cover letter DOCX export failed user_id=%s job_id=%s", user_id, job_id)
                await self._render(
                    context=context,
                    user_id=user_id,
                    text="⚠️ I could not export the cover letter. Please try again.",
                    keyboard=views.cover_letter_result_keyboard(job_id, origin, int(page)),
                    query=query,
                )
            return

        if data.startswith("job:save:"):
            _, _, job_id, origin, page = data.split(":", 4)
            self.repo.update_job_state(user_id, job_id, "saved")
            await self.show_job(user_id, job_id, origin, int(page), context, query)
            return

        if data.startswith("job:feedbackmenu:"):
            _, _, job_id, origin, page = data.split(":", 4)
            await self._render(
                context=context,
                user_id=user_id,
                text="Why is this job not for you? Choose the closest reason.",
                keyboard=views.feedback_reason_keyboard(job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:feedbackreason:"):
            _, _, reason, job_id, origin, page = data.split(":", 5)
            feedback = self.repo.save_feedback(user_id, job_id, "not_for_me", reason=reason)
            await self._render(
                context=context,
                user_id=user_id,
                text="✅ Feedback saved. This preference will be used conservatively in future ranking.",
                keyboard=views.feedback_saved_keyboard(int(feedback["id"]), job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:feedback:"):
            _, _, label, job_id, origin, page = data.split(":", 5)
            feedback = self.repo.save_feedback(user_id, job_id, label)
            await self._render(
                context=context,
                user_id=user_id,
                text="✅ Feedback saved. This preference will be used conservatively in future ranking.",
                keyboard=views.feedback_saved_keyboard(int(feedback["id"]), job_id, origin, int(page)),
                query=query,
            )
            return

        if data.startswith("job:feedbackundo:"):
            _, _, feedback_id, job_id, origin, page = data.split(":", 5)
            removed = self.repo.delete_feedback(user_id, int(feedback_id))
            job = self.repo.get_job(user_id, job_id)
            if removed:
                text = "↩ Feedback undone. The previous feedback state has been restored."
            else:
                text = "That feedback entry is no longer available."
            if job:
                await self._render(
                    context=context,
                    user_id=user_id,
                    text=text,
                    keyboard=views.feedback_undo_result_keyboard(job_id, origin, int(page)),
                    query=query,
                )
            else:
                await self._show_stale_job(user_id, origin, int(page), context, query)
            return

        if data.startswith("job:mark:"):
            _, _, job_id, origin, page = data.split(":", 4)
            self.repo.update_job_state(
                user_id,
                job_id,
                "applied_by_user",
                applied_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            )
            updated = self.repo.get_job(user_id, job_id)
            if updated:
                await self._render(context=context, user_id=user_id, text=views.application_text(updated), keyboard=views.application_keyboard(updated, origin, int(page)), query=query)
            else:
                await self._show_stale_job(user_id, origin, int(page), context, query)
            return

        if data.startswith("job:state:"):
            _, _, state, job_id, origin, page = data.split(":", 5)
            updated = self.repo.update_job_state(user_id, job_id, state)
            if not updated:
                await self._show_stale_job(user_id, origin, int(page), context, query)
            elif state in {"interview", "offer", "accepted", "rejected", "withdrawn", "applied_by_user"}:
                await self._render(context=context, user_id=user_id, text=views.application_text(updated), keyboard=views.application_keyboard(updated, origin, int(page)), query=query)
            else:
                await self.show_job(user_id, job_id, origin, int(page), context, query)
            return

        if data.startswith("job:skip:"):
            _, _, job_id, origin, page = data.split(":", 4)
            self.repo.update_job_state(user_id, job_id, "skipped")
            await self.show_list(user_id, origin, int(page), context, query)
            return

        if data.startswith("job:remove:"):
            _, _, job_id, origin, page = data.split(":", 4)
            await self._render(
                context=context,
                user_id=user_id,
                text="⚠️ Remove this vacancy from JobBlast? Application history is kept only while the vacancy record remains.",
                keyboard=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, remove", callback_data=f"job:remove_confirm:{job_id}:{origin}:{page}"), InlineKeyboardButton("❌ Cancel", callback_data=f"job:view:{job_id}:{origin}:{page}")],
                ]),
                query=query,
            )
            return

        if data.startswith("job:remove_confirm:"):
            _, _, job_id, origin, page = data.split(":", 4)
            removed = self.repo.delete_job(user_id, job_id)
            if removed:
                await self.show_list(user_id, origin, int(page), context, query)
            else:
                await self._render(
                    context=context,
                    user_id=user_id,
                    text="Job not found.",
                    keyboard=views.home_keyboard(),
                    query=query,
                )
            return

        if data.startswith("job:z83:"):
            _, _, job_id, origin, page = data.split(":", 4)
            await self._show_z83_preparation(user_id, job_id, origin, int(page), context, query)
            return

        if data.startswith("z83:assets:"):
            _, _, job_id, origin, page = data.split(":", 4)
            try:
                assets = await ensure_z83_assets(self.repo.base_dir)
                await context.bot.send_document(chat_id=user_id, document=assets["editable_z83"].read_bytes(), filename="Editable_Z83.pdf", caption="Editable Z83 form for manual review")
                await context.bot.send_document(chat_id=user_id, document=assets["z83_guide"].read_bytes(), filename="Z83_Guide.pdf", caption="Z83 completion guide")
            except Exception:
                logger.exception("Z83 job assets failed user_id=%s job_id=%s", user_id, job_id)
                await self._render(context=context, user_id=user_id, text="⚠️ I could not load the Z83 files. Review the official listing directly and continue manually.", keyboard=views.z83_preparation_keyboard(job_id, origin, int(page)), query=query)
                return
            await self._show_z83_preparation(user_id, job_id, origin, int(page), context, query)
            return

        if data.startswith("z83:instructions:"):
            _, _, job_id, origin, page = data.split(":", 4)
            job = self.repo.get_job(user_id, job_id)
            if not job:
                await self._show_stale_job(user_id, origin, int(page), context, query)
                return
            requirements = dict(job.get("application_requirements") or {})
            instructions = job.get("submission_instructions") or "Follow the official advert exactly."
            text = "\n".join([
                "📋 Z83 SUBMISSION CHECKLIST",
                "",
                f"- Vacancy reference: {job.get('reference_number') or 'Confirm from the official advert'}",
                f"- Closing date/time: {job.get('closing_date') or 'Confirm from the official advert'}",
                f"- Required Z83: {'Yes' if requirements.get('z83_required') else 'Confirm from the official advert'}",
                f"- Supporting documents: {', '.join(str(item) for item in job.get('missing_docs') or []) or 'Confirm from the official advert'}",
                "",
                "Official submission instructions:",
                str(instructions),
                "",
                "Before sending: review every answer, sign yourself, certify only where required, and submit through the official route yourself.",
            ])
            await self._render(context=context, user_id=user_id, text=text, keyboard=views.z83_preparation_keyboard(job_id, origin, int(page)), query=query)
            return

        if data == "set:tone":
            profile = self.repo.get_profile(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text="Choose the default cover letter tone.",
                keyboard=views.tone_keyboard(profile.get("cover_letter_tone", "confident")),
                query=query,
            )
            return

        if data == "set:length":
            profile = self.repo.get_profile(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text="Choose the default cover letter length.",
                keyboard=views.length_keyboard(profile.get("cover_letter_length", "medium")),
                query=query,
            )
            return

        if data == "set:notifications":
            profile = self.repo.get_profile(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text="Notification preferences",
                keyboard=views.notifications_keyboard(profile),
                query=query,
            )
            return

        if data == "set:public":
            profile = self.repo.get_profile(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text="Public-service options",
                keyboard=views.public_service_keyboard(profile),
                query=query,
            )
            return

        if data == "set:contact":
            await self._render(
                context=context,
                user_id=user_id,
                text="Contact and summary settings",
                keyboard=views.contact_keyboard(),
                query=query,
            )
            return

        if data == "set:reset_jobs":
            await self._render(
                context=context,
                user_id=user_id,
                text=views.reset_jobs_text(self.repo, user_id),
                keyboard=views.reset_jobs_keyboard(),
                query=query,
            )
            return

        if data == "set:reset_jobs:confirm":
            self.repo.reset_jobs(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text="Jobs and generated cover letters were reset.\n\n" + views.home_text(self.repo, user_id),
                keyboard=views.home_keyboard(),
                query=query,
            )
            return

        if data.startswith("set:choice:"):
            _, _, field, value = data.split(":", 3)
            profile = self.repo.get_profile(user_id)
            profile[field] = value
            self.repo.save_profile(user_id, profile)
            await self.show_settings(user_id, context, query)
            return

        if data.startswith("set:toggle:"):
            _, _, section, key = data.split(":", 3)
            profile = self.repo.get_profile(user_id)
            updated = toggle_nested_option(profile, section, key)
            self.repo.save_profile(user_id, updated)
            if section == "notification_preferences":
                await self._render(
                    context=context,
                    user_id=user_id,
                    text="Notification preferences",
                    keyboard=views.notifications_keyboard(updated),
                    query=query,
                )
            else:
                await self._render(
                    context=context,
                    user_id=user_id,
                    text="Public-service options",
                    keyboard=views.public_service_keyboard(updated),
                    query=query,
                )
            return

        if data.startswith("set:edit:"):
            _, _, field = data.split(":", 2)
            self.repo.save_ui_state(user_id, pending_input={"type": "setting", "field": field})
            await self._render(
                context=context,
                user_id=user_id,
                text=f"Settings input\n\n{field}\n\n{settings_prompt(field)}",
                keyboard=InlineKeyboardMarkup([views.nav_row("nav:settings")]),
                query=query,
            )
            return

        if data.startswith("set:sources:"):
            page = int(data.rsplit(":", 1)[1])
            sources = self.repo.list_sources(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.sources_text(sources, page),
                keyboard=views.sources_keyboard(sources, page),
                query=query,
            )
            return

        if data.startswith("set:sourcehealth:"):
            page = int(data.rsplit(":", 1)[1])
            sources = self.repo.list_sources(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.source_health_text(sources, page),
                keyboard=views.source_health_keyboard(sources, page),
                query=query,
            )
            return

        if data.startswith("src:toggle:"):
            _, _, source_id, page = data.split(":", 3)
            source = self.repo.get_source(user_id, source_id)
            if source:
                source["enabled"] = not source.get("enabled", True)
                self.repo.save_source(user_id, source)
            sources = self.repo.list_sources(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.sources_text(sources, int(page)),
                keyboard=views.sources_keyboard(sources, int(page)),
                query=query,
            )
            return

        if data == "set:resume":
            self.repo.clear_pending_input(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.resume_menu_text(self.repo, user_id),
                keyboard=views.resume_menu_keyboard(),
                query=query,
            )
            return

        if data == "set:resume_refresh":
            ok, message, parsed = refresh_resume_profile(self.repo, user_id)
            text = (
                f"{views.resume_menu_text(self.repo, user_id)}\n\n"
                f"{'Success' if ok else 'Resume issue'}: {message}\n"
                f"Search terms: {', '.join(parsed.get('search_terms', [])[:8]) or 'none'}"
            )
            await self._render(context=context, user_id=user_id, text=text, keyboard=views.resume_menu_keyboard(), query=query)
            return

        if data.startswith("set:upload:"):
            kind = data.rsplit(":", 1)[1]
            self.repo.save_ui_state(user_id, pending_input={"type": "upload", "kind": kind})
            label = "resume" if kind == "resume" else "supporting document"
            await self._render(
                context=context,
                user_id=user_id,
                text=f"📄 Upload {label.title()}\n\nAccepted: PDF or DOCX\nMaximum: 10 MiB\n\nSend the file now. It will be validated locally before storage.",
                keyboard=InlineKeyboardMarkup([views.nav_row("set:resume")]),
                query=query,
            )
            return

        if data.startswith("set:docs:"):
            page = int(data.rsplit(":", 1)[1])
            docs = self.repo.list_documents(user_id)
            await self._render(
                context=context,
                user_id=user_id,
                text=views.resume_menu_text(self.repo, user_id),
                keyboard=views.docs_keyboard(docs, page),
                query=query,
            )
            return

        if data.startswith("doc:view:"):
            _, _, doc_id, page = data.split(":", 3)
            await self._show_doc_detail(user_id, doc_id, int(page), context, query)
            return

        if data.startswith("doc:toggle:"):
            _, _, doc_id, page = data.split(":", 3)
            doc = next((item for item in self.repo.list_documents(user_id) if item.get("id") == doc_id), None)
            if doc:
                doc["active"] = not doc.get("active", True)
                self.repo.save_document(user_id, doc)
            await self._show_doc_detail(user_id, doc_id, int(page), context, query)
            return

        if data.startswith("doc:type:"):
            _, _, doc_id, page = data.split(":", 3)
            doc = next((item for item in self.repo.list_documents(user_id) if item.get("id") == doc_id), None)
            if doc:
                current = doc.get("doc_type", "supporting_doc")
                index = DOC_TYPE_ORDER.index(current) if current in DOC_TYPE_ORDER else 0
                doc["doc_type"] = DOC_TYPE_ORDER[(index + 1) % len(DOC_TYPE_ORDER)]
                self.repo.save_document(user_id, doc)
            await self._show_doc_detail(user_id, doc_id, int(page), context, query)
            return

        if data.startswith("doc:delete:"):
            _, _, doc_id, page = data.split(":", 3)
            await self._render(
                context=context,
                user_id=user_id,
                text="⚠️ Delete this supporting document? Its source evidence will be removed from future refreshes.",
                keyboard=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, delete", callback_data=f"doc:delete_confirm:{doc_id}:{page}"), InlineKeyboardButton("❌ Cancel", callback_data=f"doc:view:{doc_id}:{page}")],
                ]),
                query=query,
            )
            return

        if data.startswith("doc:delete_confirm:"):
            _, _, doc_id, page = data.split(":", 3)
            doc = self.repo.delete_document(user_id, doc_id)
            docs = self.repo.list_documents(user_id)
            text = "Document deleted." if doc else "Document not found."
            await self._render(
                context=context,
                user_id=user_id,
                text=f"{text}\n\n{views.resume_menu_text(self.repo, user_id)}",
                keyboard=views.docs_keyboard(docs, int(page)),
                query=query,
            )
            return

        await self._render(
            context=context,
            user_id=user_id,
            text="That action is no longer available. Use the current menu to continue.",
            keyboard=views.home_keyboard(),
            query=query,
        )

    async def text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        pending = self.repo.get_ui_state(user_id).get("pending_input")
        if not pending:
            await update.message.reply_text("Use 🏠 Home to continue, or /menu if the menu is not visible.")
            return
        if pending.get("type") == "job_field":
            await self._save_job_field_from_text(update, context, pending)
            return
        if pending.get("type") != "setting":
            await update.message.reply_text("Send the requested file, or tap ⬅️ Back to cancel this flow.")
            return
        profile = self.repo.get_profile(user_id)
        updated, error = apply_profile_input(profile, pending["field"], update.message.text)
        if error:
            await update.message.reply_text(error)
            return
        self.repo.save_profile(user_id, updated)
        self.repo.clear_pending_input(user_id)
        await self._render(
            context=context,
            user_id=user_id,
            text=views.settings_text(self.repo, user_id),
            keyboard=views.settings_keyboard(),
            update=update,
        )

    async def _save_job_field_from_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict) -> None:
        user_id = update.effective_user.id
        value = str(update.message.text or "").strip()
        field = str(pending.get("field") or "")
        job_id = str(pending.get("job_id") or "")
        job = self.repo.get_job(user_id, job_id)
        if not job:
            self.repo.clear_pending_input(user_id)
            await update.message.reply_text("⚠️ That vacancy is no longer available. Use 🏠 Home to continue.")
            return
        if len(value) > 1000:
            await update.message.reply_text("That entry is too long. Please send a shorter value.")
            return
        if field in {"application_date", "follow_up_at"} and value.lower() in {"none", "clear", "off"}:
            updates = {field: ""}
        elif field in {"application_date", "follow_up_at"}:
            normalized = value.replace("/", "-")
            try:
                datetime.fromisoformat(normalized)
            except ValueError:
                try:
                    datetime.strptime(normalized, "%Y-%m-%d")
                except ValueError:
                    await update.message.reply_text("Use an ISO date such as 2026-08-27.")
                    return
            updates = {field: normalized}
        elif field == "contact":
            parts = [item.strip() for item in value.split("|")]
            if len(parts) < 1 or not parts[0]:
                await update.message.reply_text("Send at least a contact name, using Name | Role | Email | Phone | Notes.")
                return
            updates = {"contact": {"name": parts[0], "role": parts[1] if len(parts) > 1 else "", "email": parts[2] if len(parts) > 2 else "", "phone": parts[3] if len(parts) > 3 else "", "notes": parts[4] if len(parts) > 4 else ""}}
        elif field == "interview":
            parts = [item.strip() for item in value.split("|")]
            updates = {"interview_at": parts[0], "interview_location": parts[1] if len(parts) > 1 else "", "interview_notes": parts[2] if len(parts) > 2 else ""}
        elif field == "offer":
            parts = [item.strip() for item in value.split("|")]
            updates = {"offer": {"compensation": parts[0] if parts else "", "received_at": parts[1] if len(parts) > 1 else "", "decision_deadline": parts[2] if len(parts) > 2 else "", "notes": parts[3] if len(parts) > 3 else ""}}
        else:
            updates = {field: value}
        updated = self.repo.update_job_fields(user_id, job_id, **updates)
        self.repo.clear_pending_input(user_id)
        if not updated:
            await update.message.reply_text("⚠️ I could not update that application. Use 🏠 Home to continue.")
            return
        await self._render(context=context, user_id=user_id, text=views.application_text(updated), keyboard=views.application_keyboard(updated, str(pending.get("origin") or "applications"), int(pending.get("page") or 0)), update=update)

    async def document_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        pending = self.repo.get_ui_state(user_id).get("pending_input")
        if not pending or pending.get("type") != "upload":
            await update.message.reply_text("Open Settings -> Active Resume first, then choose Upload Resume or Upload Document.")
            return
        document = update.message.document
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            self.repo.clear_pending_input(user_id)
            await update.message.reply_text("Document rejected: the uploaded file is larger than the 10 MiB limit.")
            return
        file = await document.get_file()
        payload = await file.download_as_bytearray()
        if pending.get("kind") == "resume":
            ok, message, parsed = save_resume_bytes(self.repo, user_id, document.file_name or "resume.pdf", bytes(payload))
            self.repo.clear_pending_input(user_id)
            text = f"{'Resume updated' if ok else 'Resume issue'}\n\n{message}\nSearch terms: {', '.join(parsed.get('search_terms', [])[:8]) or 'none'}"
            await self._render(context=context, user_id=user_id, text=text, keyboard=views.resume_menu_keyboard(), update=update)
            return
        try:
            saved = save_supporting_document(self.repo, user_id, document.file_name or "document.pdf", bytes(payload))
        except UploadValidationError as exc:
            self.repo.clear_pending_input(user_id)
            await update.message.reply_text(f"Document rejected: {exc}")
            return
        self.repo.clear_pending_input(user_id)
        await self._render(
            context=context,
            user_id=user_id,
            text=f"Supporting document saved\n\n{saved['original_name']}\nType: {saved['doc_type']}\nSummary: {document_summary(self.repo, user_id)}",
            keyboard=views.resume_menu_keyboard(),
            update=update,
        )

    async def scheduled_scan(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        for user_id in self.repo.list_user_ids():
            if not is_authorized(user_id, self.allowed_users):
                continue
            profile = self.repo.get_profile(user_id)
            prefs = profile.get("notification_preferences", {})
            if not any(prefs.get(key) for key in ["new_matches", "public_service", "scan_errors", "daily_digest", "deadlines", "closing_soon", "follow_ups", "interviews", "important_updates", "trust_warnings"]):
                continue
            try:
                result = await run_scan(self.repo, user_id, scheduled_only=True)
            except Exception:
                logger.exception("Scheduled scan failed user_id=%s", user_id)
                if prefs.get("scan_errors"):
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="Scheduled scan could not complete. The failure was logged; use Scan to try again later.",
                        )
                    except TelegramError:
                        logger.warning("Scheduled scan failure notification failed user_id=%s", user_id)
                continue
            lines = []
            reminders = collect_job_reminders(self.repo, user_id)
            strong_new = [job for job in result.get("new_jobs", []) if job.get("strong_match")]
            actionable_new = [job for job in strong_new if job.get("actionability_state") == "actionable"]
            prep_new = [job for job in strong_new if job.get("actionability_state") == "prep_needed"]
            if prefs.get("new_matches") and strong_new:
                notification_key = "strong-new:" + ",".join(sorted(str(job.get("id")) + ":" + str(job.get("content_hash") or "") for job in strong_new))
                if self.repo.record_notification(user_id, notification_key, "strong_new_match", {"count": len(strong_new)}):
                    lines.append(
                        f"Scheduled scan found {len(actionable_new)} ready-to-apply and "
                        f"{len(prep_new)} strong match(es) needing preparation."
                    )
            if prefs.get("public_service") and result.get("new_public_matches"):
                lines.append(f"New public-service matches: {result['new_public_matches']}.")
            if prefs.get("scan_errors") and result["errors"]:
                error_key = "source-errors:" + datetime.now(timezone.utc).date().isoformat() + ":" + ",".join(sorted(str(item.get("source_id")) for item in result["errors"]))
                if self.repo.record_notification(user_id, error_key, "source_failure", {"count": len(result["errors"])}):
                    lines.append(f"Source errors: {len(result['errors'])}.")
            if (prefs.get("deadlines") or prefs.get("closing_soon")) and reminders["deadlines"]:
                lines.extend(["", "Deadline reminders:"] + [f"- {item}" for item in reminders["deadlines"]])
            if prefs.get("follow_ups") and reminders["follow_ups"]:
                lines.extend(["", "Follow-up reminders:"] + [f"- {item}" for item in reminders["follow_ups"]])
            if (prefs.get("follow_ups") or prefs.get("interviews")) and reminders.get("interviews"):
                lines.extend(["", "Interview reminders:"] + [f"- {item}" for item in reminders["interviews"]])
            if (prefs.get("follow_ups") or prefs.get("interviews")) and reminders.get("outcomes"):
                lines.extend(["", "Application updates:"] + [f"- {item}" for item in reminders["outcomes"]])
            if prefs.get("important_updates"):
                updated_jobs = [job for job in result.get("reconciled_jobs", []) if job.get("revision_changes")]
                if updated_jobs:
                    lines.extend(["", f"Important updates: {len(updated_jobs)} tracked vacancy(s) changed."])
            if prefs.get("trust_warnings"):
                trust_jobs = [job for job in result.get("persisted_jobs", []) if str(job.get("verification_status") or "") in {"conflicted", "degraded", "official_unavailable"} or str(job.get("trust_status") or "") in {"caution", "high_risk"}]
                if trust_jobs:
                    lines.extend(["", f"Trust warnings: review {len(trust_jobs)} vacancy(s) before relying on them."])
            if prefs.get("daily_digest") and not lines:
                lines.append("Scheduled scan ran with no major changes.")
            if lines:
                await context.bot.send_message(chat_id=user_id, text="\n".join(lines))

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Conflict):
            logger.error("Telegram polling conflict detected. Another bot instance is already using this token. Stopping this local instance.")
            application = getattr(context, "application", None)
            if application and not application.bot_data.get("conflict_stop_requested"):
                application.bot_data["conflict_stop_requested"] = True
                try:
                    application.stop_running()
                except Exception:
                    logger.exception("Failed to stop JobBlast cleanly after polling conflict")
            return
        logger.exception("Unhandled JobBlast error", exc_info=context.error)
        telegram_update = update if isinstance(update, Update) else None
        user_id = telegram_update.effective_user.id if telegram_update and telegram_update.effective_user else None
        if not is_authorized(user_id, self.allowed_users):
            return
        if user_id is None:
            return
        try:
            await self._reply_transient(
                telegram_update,
                context,
                user_id,
                "JobBlast hit an unexpected error. The failure was logged and the menu is still available with /menu.",
            )
        except Exception:
            logger.exception("Failed to notify user about error user_id=%s", user_id)
