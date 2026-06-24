"""JAS Telegram Handlers — Commands, callbacks, and notification dispatch.

All handlers are registered on the Dispatcher via ``register_handlers(dp)``.
The module also exposes ``send_job_notification`` for the pipeline to push
new job matches into the Telegram chat.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder / optional imports — components built by other agents.
# ---------------------------------------------------------------------------
try:
    from src.db.client import DatabaseClient  # type: ignore[import-untyped]
except ImportError:
    DatabaseClient = None  # type: ignore[assignment,misc]
    logger.warning("src.db.client not available — DB operations will be stubs.")

try:
    from src.applier.engine import auto_apply  # type: ignore[import-untyped]
except ImportError:
    auto_apply = None  # type: ignore[assignment]
    logger.warning("src.applier.engine not available — auto-apply is a stub.")

try:
    from src.filtering.embedding_engine import EmbeddingEngine
    
    _embedding_engine_instance: EmbeddingEngine | None = None
    
    async def compute_embedding(text: str) -> list[float]:
        global _embedding_engine_instance
        if _embedding_engine_instance is None:
            _embedding_engine_instance = EmbeddingEngine()
        return await _embedding_engine_instance.get_embedding(text)
except ImportError:
    compute_embedding = None  # type: ignore[assignment]
    logger.warning("src.filtering.embedding_engine not available — embeddings are a stub.")

# ---------------------------------------------------------------------------
# Global pipeline pause flag  (in-memory; a persistent store can replace it)
# ---------------------------------------------------------------------------
_pipeline_paused: bool = False
_start_time: datetime = datetime.now(timezone.utc)


def is_pipeline_paused() -> bool:
    """Return the current pipeline pause state."""
    return _pipeline_paused


# ---------------------------------------------------------------------------
# Helper — get a ``DatabaseClient`` instance (lazy singleton).
# ---------------------------------------------------------------------------
_db_instance: Any = None
_orchestrator_instance: Any = None


def _get_db() -> Any:
    """Return a ``DatabaseClient`` singleton, or ``None`` if unavailable."""
    global _db_instance  # noqa: WPS420
    if _db_instance is None and DatabaseClient is not None:
        _db_instance = DatabaseClient()
    return _db_instance


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = Router(name="jas_handlers")


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message explaining JAS."""
    text = (
        "👋 <b>Welcome to JAS — Job Application System</b>\n\n"
        "I'm your automated job-hunting co-pilot. Here's what I can do:\n\n"
        "• 📧 <b>Ingest</b> job alerts from your Gmail inbox\n"
        "• 🧠 <b>Score</b> each listing with AI + cosine similarity\n"
        "• 🚀 <b>Auto-apply</b> to supported ATS platforms\n"
        "• 📝 <b>Generate</b> tailored CVs and cover letters\n"
        "• 📊 <b>Digest</b> your daily stats every evening\n\n"
        "<b>Commands:</b>\n"
        "/run — manually trigger the pipeline check immediately\n"
        "/status — pipeline statistics\n"
        "/digest — on-demand daily digest\n"
        "/set_threshold &lt;0.0–1.0&gt; — update cosine threshold\n"
        "/pause — pause the pipeline\n"
        "/resume — resume the pipeline\n"
        "/update_resume — hot-swap your resume (reply with PDF)"
    )
    await message.answer(text)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Detailed pipeline status, uptime, database connectivity, and job scraping statistics."""
    db = _get_db()
    db_status = "🟢 Connected" if db is not None else "🔴 Disconnected"
    
    # Calculate uptime
    uptime_delta = datetime.now(timezone.utc) - _start_time
    hours, remainder = divmod(int(uptime_delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    if db is None:
        await message.answer(
            f"🤖 <b>JAS Bot Status</b>\n\n"
            f"• <b>Status:</b> 🟢 Online\n"
            f"• <b>Uptime:</b> {uptime_str}\n"
            f"• <b>Database:</b> {db_status}\n\n"
            f"⚠️ Database client unavailable — cannot retrieve stats."
        )
        return

    try:
        stats = await db.get_pipeline_stats()
        
        # Format domains list
        top_domains = stats.get("top_domains", [])
        domains_str = ", ".join(top_domains) if top_domains else "None"
        
        # Format platform breakdown
        breakdown = stats.get("platform_breakdown", {})
        breakdown_str = ""
        if breakdown:
            breakdown_str = "\n".join([f"   - <i>{plat}</i>: <b>{count}</b>" for plat, count in breakdown.items()])
        else:
            breakdown_str = "   - <i>None</i>"

        text = (
            f"🤖 <b>JAS Bot Status & Analytics</b>\n\n"
            f"⚡ <b>System Health:</b>\n"
            f"• <b>Status:</b> 🟢 Online\n"
            f"• <b>Uptime:</b> {uptime_str}\n"
            f"• <b>Database:</b> {db_status}\n\n"
            f"📊 <b>Job Pipeline Stats:</b>\n"
            f"• <b>Total Jobs Scanned:</b> <b>{stats.get('scanned', 0)}</b>\n"
            f"• <b>Applied (Auto):</b> 🟢 <b>{stats.get('applied_auto', 0)}</b>\n"
            f"• <b>Applied (Manual):</b> 🔵 <b>{stats.get('applied_manual', 0)}</b>\n"
            f"• <b>Pending Review:</b> 🟡 <b>{stats.get('pending', 0)}</b>\n"
            f"• <b>Skipped (Matches &lt; Threshold):</b> ⚪ <b>{stats.get('skipped', 0)}</b>\n\n"
            f"🌐 <b>Web Scanning Metrics:</b>\n"
            f"• <b>Unique Websites Visited:</b> <b>{stats.get('unique_domains_count', 0)}</b>\n"
            f"• <b>Top Target Domains:</b> <i>{domains_str}</i>\n"
            f"• <b>Jobs by Platform:</b>\n{breakdown_str}"
        )
    except Exception:
        logger.exception("Failed to fetch pipeline stats")
        text = (
            f"🤖 <b>JAS Bot Status</b>\n\n"
            f"• <b>Status:</b> 🟢 Online\n"
            f"• <b>Uptime:</b> {uptime_str}\n"
            f"• <b>Database:</b> {db_status}\n\n"
            f"❌ Could not retrieve pipeline stats."
        )
    await message.answer(text)


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Trigger an on-demand daily digest."""
    # Lazy import to avoid circular dependency at module load.
    from src.telegram.digest import DigestGenerator  # noqa: WPS433

    try:
        generator = DigestGenerator()
        await generator.send_daily_digest(message.bot)  # type: ignore[arg-type]
    except Exception:
        logger.exception("On-demand digest failed")
        await message.answer("❌ Failed to generate digest.")


@router.message(Command("set_threshold"))
async def cmd_set_threshold(message: Message, command: CommandObject) -> None:
    """Update the cosine similarity threshold (0.0 – 1.0)."""
    args = command.args
    if not args:
        await message.answer("Usage: /set_threshold 0.80")
        return

    try:
        value = float(args.strip())
    except ValueError:
        await message.answer("❌ Please provide a valid float between 0.0 and 1.0.")
        return

    if not 0.0 <= value <= 1.0:
        await message.answer("❌ Threshold must be between 0.0 and 1.0.")
        return

    settings = get_settings()
    # Pydantic Settings are frozen; we mutate the cached instance directly.
    object.__setattr__(settings, "cosine_threshold", value)

    db = _get_db()
    if db is not None:
        try:
            await db.update_setting("cosine_threshold", value)
        except Exception:
            logger.exception("Failed to persist threshold to DB")

    await message.answer(f"✅ Cosine threshold updated to <b>{value}</b>.")


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    """Pause the pipeline globally."""
    global _pipeline_paused, _orchestrator_instance  # noqa: WPS420
    _pipeline_paused = True
    if _orchestrator_instance is not None:
        _orchestrator_instance.pause()
    logger.info("Pipeline PAUSED by user %s", message.from_user)
    await message.answer("⏸ Pipeline <b>paused</b>. Use /resume to continue.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    """Resume the pipeline globally."""
    global _pipeline_paused, _orchestrator_instance  # noqa: WPS420
    _pipeline_paused = False
    if _orchestrator_instance is not None:
        _orchestrator_instance.resume()
    logger.info("Pipeline RESUMED by user %s", message.from_user)
    await message.answer("▶️ Pipeline <b>resumed</b>.")


@router.message(Command("run"))
async def cmd_run(message: Message) -> None:
    """Manually trigger the pipeline check immediately."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        await message.answer("⚠️ Pipeline orchestrator is not initialized yet.")
        return

    await message.answer("🔄 <b>Starting manual pipeline run...</b>\nFetching new job alerts from Gmail and processing them. Please wait.")
    
    try:
        stats = await _orchestrator_instance.run()
        text = (
            "✅ <b>Pipeline Run Completed!</b>\n\n"
            f"📥 Jobs Discovered: <b>{stats.get('jobs_discovered', 0)}</b>\n"
            f"🔍 Jobs Scraped: <b>{stats.get('jobs_scraped', 0)}</b>\n"
            f"⏭ Duplicates Skipped: <b>{stats.get('duplicates_skipped', 0)}</b>\n"
            f"📐 Filtered by Math Gate: <b>{stats.get('filtered_math', 0)}</b>\n"
            f"🧠 Filtered by AI Gate: <b>{stats.get('filtered_llm', 0)}</b>\n"
            f"✉️ Passed to User (Matched): <b>{stats.get('passed_to_user', 0)}</b>\n"
            f"📝 Cover Letters Generated: <b>{stats.get('cover_letters_generated', 0)}</b>\n"
            f"⚠️ Errors: <b>{stats.get('errors', 0)}</b>"
        )
        await message.answer(text)
    except Exception as e:
        logger.exception("Manual pipeline run failed")
        await message.answer(f"❌ <b>Pipeline run failed:</b> {e}")


# ---------------------------------------------------------------------------
# /update_resume — expects a follow-up document message with a PDF
# ---------------------------------------------------------------------------

@router.message(Command("update_resume"))
async def cmd_update_resume(message: Message) -> None:
    """Prompt the user to send a new resume PDF."""
    await message.answer(
        "📄 Please send your updated resume as a <b>PDF</b> attachment now.\n"
        "I'll extract the text, re-compute embeddings, and update your profile."
    )


async def parse_resume_text(resume_text: str) -> dict:
    """Parse resume raw text into structured JSON using Gemini 2.5 Flash."""
    from google import genai
    from google.genai import types
    import json
    
    try:
        settings = get_settings()
        client = genai.Client(api_key=settings.gemini_api_key)
        
        system_prompt = (
            "You are an expert ATS resume parser. Parse the provided raw resume text into a structured JSON object. "
            "Extract the candidate's name (as name), email, phone, and linkedin_url. "
            "Also extract their education (list of dicts containing institution, degree, dates, gpa), "
            "experience (list of dicts containing title, company, dates, bullets), "
            "projects (list of dicts containing name, tech_stack, description, bullets), "
            "and skills (dict mapping category string to list of strings). "
            "Do not invent any data; only extract information present in the resume text."
        )
        
        user_prompt = f"Resume Text:\n\n{resume_text}"
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        data = json.loads(response.text)
        return data
    except Exception as e:
        logger.error("Failed to parse resume with Gemini: %s", e)
        return {}


@router.message(F.document.mime_type == "application/pdf")
async def handle_resume_pdf(message: Message) -> None:
    """Receive a PDF document, extract text, and update resume embeddings.

    Flow:
    1. Download the PDF bytes from Telegram.
    2. Extract raw text (basic extraction via ``PyPDF2`` / ``pdfplumber``).
    3. Clear old resume embeddings in the database.
    4. Compute new embedding vector.
    5. Store the new embedding and update the user profile.
    """
    document = message.document
    if document is None:
        return

    await message.answer("⏳ Processing your resume…")

    # 1. Download PDF bytes --------------------------------------------------
    bot: Bot = message.bot  # type: ignore[assignment]
    file = await bot.get_file(document.file_id)
    if file.file_path is None:
        await message.answer("❌ Could not download the file from Telegram.")
        return

    file_bytes_io = io.BytesIO()
    await bot.download_file(file.file_path, file_bytes_io)
    file_bytes_io.seek(0)
    pdf_bytes = file_bytes_io.read()

    # 2. Extract text --------------------------------------------------------
    resume_text = _extract_text_from_pdf(pdf_bytes)
    if not resume_text.strip():
        await message.answer("❌ Could not extract text from the PDF. Is it image-based?")
        return

    # 3-5. Embedding pipeline ------------------------------------------------
    db = _get_db()
    if db is None:
        await message.answer("⚠️ Database client unavailable — cannot update embeddings.")
        return

    try:
        # Clear old vectors
        await db.clear_resume_embedding()

        # Compute new embedding
        if compute_embedding is not None:
            new_embedding = await compute_embedding(resume_text)
        else:
            logger.warning("Embedding engine unavailable; storing placeholder.")
            new_embedding = []

        # Store new embedding
        await db.update_resume_embedding(new_embedding)

        # Parse resume text to structured JSON
        parsed_data = await parse_resume_text(resume_text)

        profile_data = {
            "resume_text": resume_text,
            "full_name": parsed_data.get("name") or parsed_data.get("full_name") or "",
            "email": parsed_data.get("email") or "",
            "phone": parsed_data.get("phone") or "",
            "linkedin_url": parsed_data.get("linkedin_url") or "",
            "resume_json": parsed_data
        }

        # Update user profile with parsed resume data
        await db.upsert_user_profile(profile_data)

        await message.answer(
            "✅ <b>Resume updated!</b> Old vectors cleared, new embeddings stored."
        )
        logger.info("Resume updated successfully for user %s", message.from_user)
    except Exception as exc:
        logger.exception("Resume update pipeline failed")
        await message.answer(f"❌ Resume update failed: {exc}")


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Best-effort text extraction from raw PDF bytes.

    Tries ``pdfplumber`` first (better table/layout support), then
    falls back to ``PyPDF2``.  Returns an empty string on total failure.
    """
    # Attempt 1: pdfplumber
    try:
        import pdfplumber  # type: ignore[import-untyped]

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages)
        if text.strip():
            return text
    except Exception:
        logger.debug("pdfplumber extraction failed, trying PyPDF2")

    # Attempt 2: PyPDF2
    try:
        from PyPDF2 import PdfReader  # type: ignore[import-untyped]

        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except Exception:
        logger.debug("PyPDF2 extraction also failed")

    return ""


# ═══════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("apply_"))
async def cb_apply(callback: CallbackQuery) -> None:
    """Trigger the auto-apply pipeline for the given job.

    Callback data format: ``apply_{job_id}``
    """
    await callback.answer()
    job_id = (callback.data or "").removeprefix("apply_")
    db = _get_db()

    # Acknowledge immediately
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏳ Applying to job <code>{job_id}</code>…"
    )

    # Fetch job data from DB for context
    job: dict[str, Any] = {}
    if db is not None:
        try:
            job = await db.get_job(job_id) or {}
        except Exception:
            logger.exception("Failed to fetch job %s from DB", job_id)

    company = job.get("company", "Unknown Company")
    url = job.get("url", "N/A")

    # Run auto-apply
    if auto_apply is not None:
        try:
            result = await auto_apply(job_id)
            if result.get("success"):
                # Log to DB
                if db is not None:
                    await db.update_job_status(job_id, "APPLIED_AUTO")
                await callback.message.edit_text(  # type: ignore[union-attr]
                    f"✅ Application submitted to <b>{company}</b>. Database logged."
                )
            else:
                error = result.get("error", "Unknown error")
                await callback.message.edit_text(  # type: ignore[union-attr]
                    f"❌ Auto-apply failed. {error}.\n"
                    f"Here's the manual link: {url}"
                )
        except Exception as exc:
            logger.exception("Auto-apply raised for job %s", job_id)
            await callback.message.edit_text(  # type: ignore[union-attr]
                f"❌ Auto-apply failed. {exc}.\n"
                f"Here's the manual link: {url}"
            )
    else:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"❌ Auto-apply engine not available.\n"
            f"Here's the manual link: {url}"
        )


@router.callback_query(F.data.startswith("skip_"))
async def cb_skip(callback: CallbackQuery) -> None:
    """Mark a job as SKIPPED.

    Callback data format: ``skip_{job_id}``
    """
    await callback.answer()
    job_id = (callback.data or "").removeprefix("skip_")
    db = _get_db()

    job: dict[str, Any] = {}
    if db is not None:
        try:
            job = await db.get_job(job_id) or {}
            await db.update_job_status(job_id, "SKIPPED")
        except Exception:
            logger.exception("Failed to skip job %s", job_id)

    title = job.get("title", "Job")
    company = job.get("company", "Unknown Company")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"⏭ Skipped <b>{title}</b> at <b>{company}</b>"
    )


@router.callback_query(F.data.startswith("manual_"))
async def cb_manual(callback: CallbackQuery) -> None:
    """Log the job as a manual application.

    Callback data format: ``manual_{job_id}``
    """
    await callback.answer()
    job_id = (callback.data or "").removeprefix("manual_")
    db = _get_db()

    job: dict[str, Any] = {}
    if db is not None:
        try:
            job = await db.get_job(job_id) or {}
            await db.update_job_status(job_id, "APPLIED_MANUAL")
        except Exception:
            logger.exception("Failed to mark manual for job %s", job_id)

    title = job.get("title", "Job")
    company = job.get("company", "Unknown Company")
    url = job.get("url", "N/A")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📝 Logged <b>{title}</b> at <b>{company}</b> as manual application.\n"
        f"🔗 Apply here: {url}"
    )


@router.callback_query(F.data.startswith("dismiss_"))
async def cb_dismiss(callback: CallbackQuery) -> None:
    """Dismiss (clear) a job from the notification queue.

    Callback data format: ``dismiss_{job_id}``
    """
    await callback.answer()
    job_id = (callback.data or "").removeprefix("dismiss_")
    db = _get_db()

    if db is not None:
        try:
            await db.update_job_status(job_id, "DISMISSED")
        except Exception:
            logger.exception("Failed to dismiss job %s", job_id)

    await callback.message.edit_text(  # type: ignore[union-attr]
        "🗑 Dismissed from queue."
    )


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATION DISPATCH  (called by the pipeline, not by Telegram)
# ═══════════════════════════════════════════════════════════════════════════

async def send_job_notification(
    bot: Bot,
    job_data: dict[str, Any],
    pdf_path: str | Path,
    cover_letter_path: str | Path | None = None,
) -> None:
    """Send a rich job-match notification to the configured Telegram chat.

    Three visual variants depending on the match context:

    1. **Standard match** (supported ATS, score < 90, no cover letter)
    2. **High-confidence match** (score ≥ 90, cover letter attached)
    3. **Unsupported ATS** (manual-apply link, no auto-apply option)

    Parameters
    ----------
    bot:
        The aiogram Bot instance.
    job_data:
        Dict with keys: ``job_id``, ``title``, ``company``, ``score``,
        ``reasoning``, ``ats_type``, ``url``, ``ats_supported``.
    pdf_path:
        Path to the tailored CV PDF.
    cover_letter_path:
        Optional path to a cover letter PDF (only for score ≥ 90).
    """
    settings = get_settings()
    chat_id = settings.telegram_chat_id

    job_id = job_data.get("job_id", "unknown")
    title = job_data.get("title", "Unknown Title")
    company = job_data.get("company", "Unknown Company")
    score = job_data.get("score", 0)
    reasoning = job_data.get("reasoning", "No AI notes available.")
    ats_type = job_data.get("ats_type", "Unknown")
    ats_supported = job_data.get("ats_supported", True)

    pdf_path = Path(pdf_path)

    # ------------------------------------------------------------------
    # Variant 3 — Unsupported ATS
    # ------------------------------------------------------------------
    if not ats_supported:
        text = (
            f"⚠️ <b>Notice:</b> {ats_type} (Custom Auth required)\n\n"
            f"📎 Attached: Materials"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Apply Manually Here",
                    callback_data=f"manual_{job_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Dismiss",
                    callback_data=f"dismiss_{job_id}",
                ),
            ],
        ])

        await _send_documents_with_caption(
            bot=bot,
            chat_id=chat_id,
            text=text,
            keyboard=keyboard,
            pdf_path=pdf_path,
            cover_letter_path=Path(cover_letter_path) if cover_letter_path else None,
        )
        return

    # ------------------------------------------------------------------
    # Variant 2 — High-confidence match (score ≥ 90)
    # ------------------------------------------------------------------
    if score >= 90 and cover_letter_path is not None:
        text = (
            f"🎯 <b>New Match:</b> {title} at {company}\n"
            f"📊 Score: <b>{score}%</b>  🔥 HIGH CONFIDENCE\n"
            f"🤖 AI Notes: {reasoning}\n\n"
            f"📎 Attached: CV + Cover Letter"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Auto-Apply",
                    callback_data=f"apply_{job_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ Skip",
                    callback_data=f"skip_{job_id}",
                ),
            ],
        ])

        await _send_documents_with_caption(
            bot=bot,
            chat_id=chat_id,
            text=text,
            keyboard=keyboard,
            pdf_path=pdf_path,
            cover_letter_path=Path(cover_letter_path),
        )
        return

    # ------------------------------------------------------------------
    # Variant 1 — Standard match
    # ------------------------------------------------------------------
    text = (
        f"🎯 <b>New Match:</b> {title} at {company}\n"
        f"📊 Score: <b>{score}%</b>\n"
        f"🤖 AI Notes: {reasoning}\n\n"
        f"📎 Attached: {company}_Tailored_CV.pdf"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Auto-Apply",
                callback_data=f"apply_{job_id}",
            ),
            InlineKeyboardButton(
                text="⏭ Skip",
                callback_data=f"skip_{job_id}",
            ),
        ],
    ])

    await _send_documents_with_caption(
        bot=bot,
        chat_id=chat_id,
        text=text,
        keyboard=keyboard,
        pdf_path=pdf_path,
        cover_letter_path=None,
    )


# ---------------------------------------------------------------------------
# Internal helper — send one or two documents with a rich caption
# ---------------------------------------------------------------------------

async def _send_documents_with_caption(
    *,
    bot: Bot,
    chat_id: str,
    text: str,
    keyboard: InlineKeyboardMarkup,
    pdf_path: Path,
    cover_letter_path: Path | None,
) -> None:
    """Send PDF document(s) with an HTML caption and inline keyboard.

    If two documents need to be sent (CV + cover letter), the first is
    sent without a keyboard and the second carries the caption + buttons.
    If only one document, it carries everything.
    """
    try:
        if cover_letter_path is not None and cover_letter_path.exists():
            # Send CV first (silent)
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(pdf_path),
            )
            # Send cover letter with caption + keyboard
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(cover_letter_path),
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(pdf_path),
                caption=text,
                reply_markup=keyboard,
            )
    except Exception:
        logger.exception("Failed to send job notification for chat %s", chat_id)
        # Fallback — send as a plain text message if file delivery fails.
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text + "\n\n⚠️ <i>Could not attach documents.</i>",
                reply_markup=keyboard,
            )
        except Exception:
            logger.exception("Fallback text notification also failed")


# ═══════════════════════════════════════════════════════════════════════════
# HANDLER REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════

def register_handlers(dp: Dispatcher, db: Any = None, orchestrator: Any = None, embedding_engine: Any = None) -> None:
    """Attach all JAS handlers to the given Dispatcher.

    Call this once during bot setup, *before* starting polling or webhooks.
    """
    global _db_instance, auto_apply, compute_embedding, _pipeline_paused, _orchestrator_instance

    if db is not None:
        _db_instance = db

    if orchestrator is not None:
        _orchestrator_instance = orchestrator

    if embedding_engine is not None:
        # Wrap the embedding engine's get_embedding method
        async def _compute(text: str) -> list[float]:
            return await embedding_engine.get_embedding(text)
        compute_embedding = _compute

    dp.include_router(router)
    logger.info("JAS Telegram handlers registered on Dispatcher.")
