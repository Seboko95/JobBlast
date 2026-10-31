"""Application bootstrap for the manual-apply JobBlast Telegram bot."""

from __future__ import annotations

import logging
import os
from datetime import time as dtime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.authorization import parse_allowed_users
from bot.handlers import ManualApplyBot
from services.documents import migrate_legacy_storage
from bot.runtime_guard import SingleInstanceGuard
from storage.repository import Repository

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def configure_logging(base_dir: Path) -> None:
    log_path = base_dir / "jobblast_runtime.log"
    root = logging.getLogger()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "").endswith("jobblast_runtime.log") for handler in root.handlers):
        return
    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(file_handler)


def build_application(base_dir: Path):
    configure_logging(base_dir)
    load_dotenv(base_dir / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Telegram bot token is missing. Set TELEGRAM_BOT_TOKEN in .env.")
    allowed_users = parse_allowed_users()
    if not allowed_users:
        raise RuntimeError("ALLOWED_USERS is required for the private Telegram bot.")

    repo = Repository(base_dir)
    for user_id in allowed_users:
        migrate_legacy_storage(repo, user_id)
    controller = ManualApplyBot(repo, allowed_users=allowed_users)
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", controller.start))
    app.add_handler(CommandHandler("menu", controller.menu))
    app.add_handler(CommandHandler("help", controller.help_command))
    app.add_handler(CommandHandler("scan", controller.scan_command))
    app.add_handler(CommandHandler("status", controller.status_command))
    app.add_handler(CommandHandler("export", controller.export_command))
    app.add_handler(CommandHandler("settings", controller.settings_command))
    app.add_handler(CommandHandler("refreshresume", controller.refresh_resume_command))
    app.add_handler(CommandHandler("addsource", controller.addsource_command))
    app.add_handler(CommandHandler("z83", controller.z83_command))
    app.add_handler(CommandHandler("persona", controller.persona_command))
    app.add_handler(CommandHandler("feedback", controller.feedback_command))

    app.add_handler(CallbackQueryHandler(controller.callback))
    app.add_handler(MessageHandler(filters.Document.ALL, controller.document_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, controller.text_message))
    app.add_error_handler(controller.error_handler)

    if app.job_queue is not None:
        app.job_queue.run_daily(controller.scheduled_scan, time=dtime(8, 0, tzinfo=ZoneInfo("Africa/Johannesburg")))

    app.bot_data["controller"] = controller
    app.bot_data["repository"] = repo
    return app


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    guard = SingleInstanceGuard(base_dir / "jobblast.lock")
    guard.acquire()
    try:
        app = build_application(base_dir)
        app.bot_data["instance_guard"] = guard
        logger.info("Starting JobBlast manual-apply assistant")
        app.run_polling(drop_pending_updates=False)
    finally:
        guard.release()
