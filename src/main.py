"""JAS — Main Application Entry Point.

Cloud-Native AI Job Application System.
FastAPI server with Telegram webhook, background scheduling, and pipeline orchestration.
"""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import get_settings
from src.api.routes import router
from src.db.client import DatabaseClient
from src.filtering.embedding_engine import EmbeddingEngine
from src.filtering.math_gate import MathGate
from src.filtering.ai_gate import AiGate
from src.ingestion.gmail_reader import InboxInterceptor
from src.ingestion.jd_scraper import JdScraper
from src.ingestion.scheduler import setup_scheduler
from src.documents.generator import DocumentGenerator
from src.documents.cover_letter_generator import CoverLetterGenerator
from src.telegram.bot import setup_bot
from src.telegram.handlers import register_handlers, send_job_notification
from src.telegram.digest import DigestGenerator
from src.pipeline.orchestrator import PipelineOrchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown logic.

    Startup:
      1. Initialize all service components (no local ML models — instant boot)
      2. Connect to Supabase
      3. Set up Telegram bot + webhook
      4. Start background scheduler (ingestion every 4h, digest at 21:00)

    Shutdown:
      1. Stop scheduler
      2. Close browser contexts
      3. Disconnect services
    """
    settings = get_settings()
    logger.info("═══════════════════════════════════════════════")
    logger.info("  JAS — Job Application System Starting Up")
    logger.info("═══════════════════════════════════════════════")

    # ── 1. Initialize database ─────────────────────────────────
    logger.info("Initializing Supabase connection...")
    db = DatabaseClient()
    await db.initialize()
    app.state.db = db

    # ── 2. Initialize filtering components (zero RAM) ──────────
    logger.info("Initializing cloud embedding engine (text-embedding-004)...")
    embedding_engine = EmbeddingEngine()

    math_gate = MathGate(embedding_engine=embedding_engine)
    ai_gate = AiGate()

    # ── 3. Initialize document engine ──────────────────────────
    logger.info("Initializing document generator (Jinja2 + Tectonic)...")
    doc_generator = DocumentGenerator()
    cover_letter_gen = CoverLetterGenerator()

    # ── 4. Initialize ingestion ────────────────────────────────
    logger.info("Initializing Gmail inbox interceptor...")
    inbox = InboxInterceptor()
    scraper = JdScraper()

    # ── 5. Set up Telegram bot ─────────────────────────────────
    logger.info("Setting up Telegram bot...")
    bot, dp = setup_bot()
    app.state.bot = bot
    app.state.dp = dp

    # ── 6. Create pipeline orchestrator ────────────────────────
    async def notification_fn(job_data):
        await send_job_notification(bot, job_data)

    orchestrator = PipelineOrchestrator(
        db=db,
        inbox=inbox,
        scraper=scraper,
        embedding_engine=embedding_engine,
        math_gate=math_gate,
        ai_gate=ai_gate,
        doc_generator=doc_generator,
        cover_letter_gen=cover_letter_gen,
        send_notification_fn=notification_fn,
    )
    app.state.orchestrator = orchestrator

    # ── 7. Register Telegram handlers ──────────────────────────
    register_handlers(dp, db, orchestrator, embedding_engine)
    logger.info("Telegram handlers registered.")

    # ── 8. Set up Telegram webhook / polling ────────────────────
    # In local development, we delete the webhook and start long-polling in the background.
    logger.info("Starting Telegram bot in polling mode for local development...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    async def start_polling():
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            
    polling_task = asyncio.create_task(start_polling())
    app.state.polling_task = polling_task

    # ── 9. Start background scheduler ──────────────────────────
    digest_gen = DigestGenerator(db=db)

    async def run_pipeline():
        await orchestrator.run()

    async def run_digest():
        await digest_gen.send_daily_digest(bot)

    scheduler = setup_scheduler(
        pipeline_callback=run_pipeline,
        digest_callback=run_digest,
    )
    app.state.scheduler = scheduler
    logger.info(
        f"Scheduler started: ingestion every {settings.ingestion_interval_hours}h, "
        f"digest at {settings.digest_hour}:00"
    )

    logger.info("═══════════════════════════════════════════════")
    logger.info("  JAS is LIVE — Waiting for jobs...")
    logger.info(f"  Cosine Threshold: {settings.cosine_threshold}")
    logger.info(f"  Cover Letter Threshold: {settings.cover_letter_score_threshold}%")
    logger.info("═══════════════════════════════════════════════")

    yield

    # ── Shutdown ───────────────────────────────────────────────
    logger.info("JAS shutting down...")
    scheduler.shutdown(wait=False)
    await dp.stop_polling()
    await bot.session.close()
    logger.info("JAS shutdown complete.")


# ── Create FastAPI App ─────────────────────────────────────────
app = FastAPI(
    title="JAS — Job Application System",
    description="Cloud-Native AI Job Application Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
