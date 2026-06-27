"""JAS Pipeline Orchestrator — Ties all 6 phases into a single async pipeline."""

from __future__ import annotations

import hashlib
import logging

from src.config import get_settings
from src.db.client import DatabaseClient
from src.documents.cover_letter_generator import CoverLetterGenerator
from src.documents.generator import DocumentGenerator
from src.filtering.ai_gate import AiGate
from src.filtering.embedding_engine import EmbeddingEngine
from src.filtering.math_gate import MathGate
from src.filtering.title_gate import passes_title_gate
from src.ingestion.active_crawler import ActiveDiscoveryEngine
from src.ingestion.jd_scraper import JdScraper, _detect_ats

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
        scraper: JdScraper,
        embedding_engine: EmbeddingEngine,
        math_gate: MathGate,
        ai_gate: AiGate,
        doc_generator: DocumentGenerator,
        cover_letter_gen: CoverLetterGenerator,
        send_notification_fn=None,
    ):
        self.db = db
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

    async def hourly_hunt(self) -> dict:
        """Silent background hunt. Runs hourly on the hour (Hour 0 to Hour 2).

        Scrapes trusted platforms, filters them, generates tailored resumes,
        and stages them in the database with status = 'new'.
        """
        if self._paused:
            logger.info("Pipeline is paused. Skipping hourly hunt.")
            return {"status": "paused", "jobs_processed": 0}

        stats = {
            "jobs_discovered": 0,
            "jobs_scraped": 0,
            "duplicates_skipped": 0,
            "filtered_math": 0,
            "filtered_llm": 0,
            "staged": 0,
            "cover_letters_generated": 0,
            "errors": 0,
        }

        try:
            logger.info("═══ Hourly Hunt: Crawling Trusted Platforms ═══")
            crawler = ActiveDiscoveryEngine()
            raw_jobs = await crawler.discover_jobs()
            stats["jobs_discovered"] = len(raw_jobs)

            # Load user profile and resume embedding
            user_profile = await self.db.get_user_profile()
            if not user_profile or not user_profile.get("resume_embedding"):
                logger.error("No user profile or embedding found! Run /update_resume first.")
                return stats

            resume_embedding = user_profile["resume_embedding"]
            resume_json = user_profile["resume_json"]

            # Process each job
            for raw_job in raw_jobs:
                try:
                    # Deduplication Layer 3 Check (90% similarity)
                    jd_text = raw_job.jd_text
                    if not jd_text and raw_job.url:
                        scraped = await self.scraper.scrape(raw_job.url)
                        if scraped:
                            jd_text = scraped.jd_text
                            stats["jobs_scraped"] += 1
                        else:
                            logger.warning(f"Failed to scrape JD from {raw_job.url}")
                            stats["errors"] += 1
                            continue

                    if not jd_text.strip():
                        continue

                    # 1. Zero-Cost Title Gate
                    if not passes_title_gate(raw_job.title):
                        continue

                    # 2. Compute JD embedding (needed for Math Gate and duplicate check)
                    jd_embedding = await self.embedding_engine.get_embedding(jd_text)

                    # 3. Layer 3 Check (90% similarity filter)
                    is_dup = await self.db.check_duplicate_by_embedding(
                        jd_embedding, threshold=0.90
                    )
                    if is_dup:
                        stats["duplicates_skipped"] += 1
                        logger.info(f"Duplicate 90%+ skipped: {raw_job.title} @ {raw_job.company}")
                        continue

                    # 4. Math Gate
                    math_result = await self.math_gate.evaluate(jd_text, resume_embedding)

                    detected_ats = _detect_ats(raw_job.url)
                    platform_name = detected_ats if detected_ats != "unknown" else raw_job.platform

                    job_data = {
                        "url_hash": hashlib.sha256(raw_job.url.encode()).hexdigest(),
                        "url": raw_job.url,
                        "title": raw_job.title,
                        "company": raw_job.company,
                        "location": raw_job.location,
                        "platform": platform_name,
                        "jd_text": jd_text,
                        "jd_embedding": jd_embedding,
                        "cosine_score": math_result.score,
                    }

                    if not math_result.passed:
                        job_data["status"] = "FILTERED_MATH"
                        await self.db.insert_job(job_data)
                        stats["filtered_math"] += 1
                        continue

                    # 5. Layer 2 AI Recruiter Gate
                    ai_result = await self.ai_gate.evaluate(jd_text, resume_json)
                    job_data["llm_score"] = ai_res_score = ai_result.score
                    job_data["llm_reasoning"] = ai_result.reasoning
                    job_data["tailored_bullets"] = ai_result.tailored_bullets

                    if not ai_result.eligible:
                        job_data["status"] = "FILTERED_LLM"
                        await self.db.insert_job(job_data)
                        stats["filtered_llm"] += 1
                        continue

                    # 6. Enhance resume with tailored GitHub projects and generate PDF
                    custom_resume_json = {**resume_json}
                    if hasattr(ai_result, "tailored_projects") and ai_result.tailored_projects:
                        custom_resume_json["projects"] = ai_result.tailored_projects

                    pdf_path = await self.doc_generator.generate_resume(
                        resume_data=custom_resume_json,
                        tailored_bullets=ai_result.tailored_bullets,
                        company=raw_job.company or "Unknown"
                    )
                    job_data["tailored_resume_path"] = str(pdf_path) if pdf_path else None

                    # 7. Generate Cover Letter if score >= 90
                    cover_letter_path = None
                    if ai_res_score >= self.settings.cover_letter_score_threshold:
                        cover_letter_path = await self.cover_letter_gen.generate(
                            jd_text=jd_text,
                            resume_json=resume_json,
                            company=raw_job.company or "Unknown",
                            title=raw_job.title or "Position"
                        )
                        if cover_letter_path:
                            job_data["cover_letter_path"] = str(cover_letter_path)
                            stats["cover_letters_generated"] += 1

                    # Stage as new
                    job_data["status"] = "new"
                    await self.db.insert_job(job_data)
                    stats["staged"] += 1
                    logger.info(f"Staged matching job: {raw_job.title} @ {raw_job.company}")

                except Exception as e:
                    logger.error(f"Error processing staged job {raw_job.url}: {e}", exc_info=True)
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Hourly hunt failed: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    async def release_and_sync(self) -> dict:
        """Release & Sync Workflow. Runs every 3 hours (Hour 3).

        1. Releases staged trusted jobs (status = 'new') to Telegram (Auto-Apply enabled).
        """
        if self._paused:
            logger.info("Pipeline is paused. Skipping release.")
            return {"status": "paused", "jobs_processed": 0}

        stats = {
            "released_staged": 0,
            "errors": 0,
        }

        # ── Step 1: Release Staged Trusted Jobs ───────────────────
        try:
            logger.info("═══ Step 1: Releasing Staged Trusted Jobs ═══")
            db_res = (
                await self.db.client.table("jobs_found")
                .select("*")
                .eq("status", "new")
                .execute()
            )
            staged_jobs = db_res.data or []

            for job in staged_jobs:
                try:
                    job_id = job["id"]
                    await self.db.update_job_status(job_id, "PENDING_USER")

                    if self.send_notification:
                        platform_val = job.get("platform", "unknown")
                        ats_supported = platform_val in ["greenhouse", "lever", "ashby"]
                        notification_data = {
                            "job_id": job_id,
                            "title": job.get("title", "Position"),
                            "company": job.get("company", "Company"),
                            "location": job.get("location", "Remote"),
                            "score": job.get("llm_score", 0),
                            "reasoning": job.get("llm_reasoning", ""),
                            "ats_type": platform_val,
                            "url": job.get("url", ""),
                            "pdf_path": job.get("tailored_resume_path"),
                            "cover_letter_path": job.get("cover_letter_path"),
                            "ats_supported": ats_supported,
                        }
                        await self.send_notification(notification_data)
                    stats["released_staged"] += 1
                except Exception as e:
                    logger.error(
                        f"Failed to release staged job {job.get('id')}: {e}",
                        exc_info=True,
                    )
                    stats["errors"] += 1
            logger.info(f"Released {stats['released_staged']} staged jobs.")
        except Exception as e:
            logger.error(f"Failed to fetch staged jobs: {e}", exc_info=True)
            stats["errors"] += 1

        return stats

    async def run(self) -> dict:
        """Execute a full manual pipeline cycle: hourly hunt followed by release."""
        hunt_stats = await self.hourly_hunt()
        release_stats = await self.release_and_sync()

        # Merge stats
        merged = {
            "jobs_discovered": hunt_stats.get("jobs_discovered", 0),
            "jobs_scraped": hunt_stats.get("jobs_scraped", 0),
            "duplicates_skipped": hunt_stats.get("duplicates_skipped", 0),
            "filtered_math": hunt_stats.get("filtered_math", 0),
            "filtered_llm": hunt_stats.get("filtered_llm", 0),
            "passed_to_user": release_stats.get("released_staged", 0),
            "cover_letters_generated": hunt_stats.get("cover_letters_generated", 0),
            "errors": hunt_stats.get("errors", 0) + release_stats.get("errors", 0),
        }
        return merged
