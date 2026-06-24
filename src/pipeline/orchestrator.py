"""JAS Pipeline Orchestrator — Ties all 6 phases into a single async pipeline."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from src.config import get_settings
from src.db.client import DatabaseClient
from src.filtering.embedding_engine import EmbeddingEngine
from src.filtering.math_gate import MathGate
from src.filtering.ai_gate import AiGate
from src.ingestion.gmail_reader import InboxInterceptor
from src.ingestion.jd_scraper import JdScraper
from src.documents.generator import DocumentGenerator
from src.documents.cover_letter_generator import CoverLetterGenerator
from src.telegram.alerts import send_system_alert

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Coordinates the complete 6-phase JAS pipeline.

    Phase 1: Fail-Safe Ingestion (Gmail IMAP)
    Phase 2: Zero-Memory Math Gate (text-embedding-004 cosine >= 0.77)
    Phase 3: AI Evaluator + Document Engine (Gemini 2.5 Flash)
    Phase 4: Human Gateway (Telegram notification)
    Phase 5: Stealth Execution (triggered by user callback)
    Phase 6: Daily Analytics Digest (separate cron)
    """

    def __init__(
        self,
        db: DatabaseClient,
        inbox: InboxInterceptor,
        scraper: JdScraper,
        embedding_engine: EmbeddingEngine,
        math_gate: MathGate,
        ai_gate: AiGate,
        doc_generator: DocumentGenerator,
        cover_letter_gen: CoverLetterGenerator,
        send_notification_fn=None,
    ):
        self.db = db
        self.inbox = inbox
        self.scraper = scraper
        self.embedding_engine = embedding_engine
        self.math_gate = math_gate
        self.ai_gate = ai_gate
        self.doc_generator = doc_generator
        self.cover_letter_gen = cover_letter_gen
        self.send_notification = send_notification_fn
        self.settings = get_settings()
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        """Pause the pipeline (user command)."""
        self._paused = True
        logger.info("Pipeline PAUSED by user command.")

    def resume(self) -> None:
        """Resume the pipeline (user command)."""
        self._paused = False
        logger.info("Pipeline RESUMED by user command.")

    async def run(self) -> dict:
        """Execute the full pipeline. Returns summary statistics.

        This is called every N hours by the scheduler.
        """
        if self._paused:
            logger.info("Pipeline is paused. Skipping this run.")
            return {"status": "paused", "jobs_processed": 0}

        stats = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "jobs_discovered": 0,
            "jobs_scraped": 0,
            "duplicates_skipped": 0,
            "filtered_math": 0,
            "filtered_llm": 0,
            "passed_to_user": 0,
            "cover_letters_generated": 0,
            "errors": 0,
        }

        try:
            # ── Phase 1: Fail-Safe Ingestion ─────────────────────────
            logger.info("═══ Phase 1: Ingestion — Fetching new jobs from Gmail ═══")
            raw_jobs = await self.inbox.fetch_new_jobs()
            stats["jobs_discovered"] = len(raw_jobs)

            if not raw_jobs:
                logger.info("No new jobs found in inbox.")
                return stats

            logger.info(f"Discovered {len(raw_jobs)} new job URLs.")

            # Load user profile and resume embedding
            user_profile = await self.db.get_user_profile()
            if not user_profile:
                logger.error("No user profile found! Run /update_resume first.")
                await send_system_alert(
                    "🚨 No user profile found in database.\n"
                    "Please send your resume via /update_resume command.",
                    severity="CRITICAL",
                )
                return stats

            resume_embedding = user_profile.get("resume_embedding")
            if not resume_embedding:
                logger.error("No resume embedding found! Upload resume first.")
                await send_system_alert(
                    "🚨 Resume embedding missing.\n"
                    "Please send your resume via /update_resume command.",
                    severity="CRITICAL",
                )
                return stats

            resume_json = user_profile.get("resume_json", {})

            # ── Process each job through the pipeline ────────────────
            for raw_job in raw_jobs:
                try:
                    await self._process_single_job(
                        raw_job, resume_embedding, resume_json, user_profile, stats
                    )
                except Exception as e:
                    logger.error(f"Error processing job {raw_job.url}: {e}", exc_info=True)
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Pipeline run failed: {e}", exc_info=True)
            await send_system_alert(
                f"🚨 Pipeline run failed!\nError: {e}",
                severity="CRITICAL",
            )

        # Log summary
        logger.info(
            f"Pipeline complete: {stats['jobs_discovered']} discovered, "
            f"{stats['filtered_math']} filtered(math), "
            f"{stats['filtered_llm']} filtered(llm), "
            f"{stats['passed_to_user']} sent to user"
        )
        return stats

    async def _process_single_job(
        self,
        raw_job,
        resume_embedding: list[float],
        resume_json: dict,
        user_profile: dict,
        stats: dict,
    ) -> None:
        """Process a single job through Phases 2-4."""

        # ── Phase 2: Zero-Memory Math Gate ───────────────────────
        logger.info(f"Processing: {raw_job.url}")

        # Step 2a: Scrape job description
        scraped = await self.scraper.scrape(raw_job.url)
        if not scraped:
            logger.warning(f"Failed to scrape JD from {raw_job.url}")
            stats["errors"] += 1
            return

        stats["jobs_scraped"] += 1

        # Step 2b: Deduplication check
        url_hash = hashlib.sha256(raw_job.url.encode()).hexdigest()
        if await self.db.job_exists_by_hash(url_hash):
            logger.info(f"Duplicate skipped: {raw_job.url}")
            stats["duplicates_skipped"] += 1
            return

        # Step 2c: Compute JD embedding (cloud API — zero RAM)
        math_result = await self.math_gate.evaluate(scraped.jd_text, resume_embedding)

        # Store the job regardless of outcome (for analytics)
        job_data = {
            "url_hash": url_hash,
            "url": raw_job.url,
            "title": scraped.title,
            "company": scraped.company,
            "location": scraped.location,
            "platform": raw_job.platform,
            "jd_text": scraped.jd_text,
            "jd_embedding": math_result.jd_embedding,
            "cosine_score": math_result.score,
            "ats_type": scraped.ats_type,
        }

        if not math_result.passed:
            job_data["status"] = "FILTERED_MATH"
            await self.db.insert_job(job_data)
            stats["filtered_math"] += 1
            logger.info(
                f"❌ Math Gate REJECTED: {scraped.title} @ {scraped.company} "
                f"(score: {math_result.score:.3f} < {self.settings.cosine_threshold})"
            )
            return

        logger.info(
            f"✅ Math Gate PASSED: {scraped.title} @ {scraped.company} "
            f"(score: {math_result.score:.3f})"
        )

        # ── Phase 3: AI Evaluator + Document Engine ──────────────
        logger.info(f"═══ Phase 3: AI Gate — Evaluating {scraped.title} @ {scraped.company} ═══")
        ai_result = await self.ai_gate.evaluate(scraped.jd_text, resume_json)

        job_data["llm_score"] = ai_result.score
        job_data["llm_reasoning"] = ai_result.reasoning
        job_data["tailored_bullets"] = ai_result.tailored_bullets

        if not ai_result.eligible:
            job_data["status"] = "FILTERED_LLM"
            await self.db.insert_job(job_data)
            stats["filtered_llm"] += 1
            logger.info(
                f"❌ AI Gate REJECTED: {scraped.title} @ {scraped.company} "
                f"(reason: {ai_result.rejection_reason})"
            )
            return

        logger.info(
            f"✅ AI Gate PASSED: {scraped.title} @ {scraped.company} "
            f"(score: {ai_result.score})"
        )

        # Step 3b: Generate tailored resume PDF
        pdf_path = await self.doc_generator.generate_resume(
            resume_data=resume_json,
            tailored_bullets=ai_result.tailored_bullets,
            company=scraped.company or "Unknown",
        )
        job_data["tailored_resume_path"] = str(pdf_path) if pdf_path else None

        # Step 3c: Generate cover letter for high-confidence matches (>= 90%)
        cover_letter_path = None
        if ai_result.score >= self.settings.cover_letter_score_threshold:
            logger.info(
                f"🔥 HIGH CONFIDENCE ({ai_result.score}%) — Generating cover letter"
            )
            cover_letter_path = await self.cover_letter_gen.generate(
                jd_text=scraped.jd_text,
                resume_json=resume_json,
                company=scraped.company or "Unknown",
                title=scraped.title or "Position",
            )
            job_data["cover_letter_path"] = str(cover_letter_path) if cover_letter_path else None
            stats["cover_letters_generated"] += 1

        # Store as pending
        job_data["status"] = "PENDING_USER"
        job_id = await self.db.insert_job(job_data)

        # ── Phase 4: Human Gateway — Telegram Notification ───────
        logger.info(f"═══ Phase 4: Sending Telegram notification for {scraped.title} ═══")
        if self.send_notification:
            notification_data = {
                "job_id": job_id,
                "title": scraped.title,
                "company": scraped.company,
                "location": scraped.location,
                "score": ai_result.score,
                "reasoning": ai_result.reasoning,
                "ats_type": scraped.ats_type,
                "url": raw_job.url,
                "pdf_path": str(pdf_path) if pdf_path else None,
                "cover_letter_path": str(cover_letter_path) if cover_letter_path else None,
            }
            await self.send_notification(notification_data)

        stats["passed_to_user"] += 1
        logger.info(
            f"📱 Notification sent: {scraped.title} @ {scraped.company} "
            f"(score: {ai_result.score}%)"
        )
