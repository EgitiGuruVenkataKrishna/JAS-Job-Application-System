"""JAS API Routes — FastAPI endpoints for webhooks, triggers, and health checks."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": "JAS - Job Application System"}


@router.get("/stats")
async def get_stats(request: Request):
    """Get current pipeline statistics."""
    db = request.app.state.db
    try:
        stats = await db.get_daily_stats()
        return {
            "status": "ok",
            "stats": stats,
            "pipeline_paused": request.app.state.orchestrator.is_paused,
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/pipeline/trigger")
async def trigger_pipeline(request: Request):
    """Manually trigger the ingestion pipeline (for testing/debugging)."""
    orchestrator = request.app.state.orchestrator
    if orchestrator.is_paused:
        return {"status": "paused", "message": "Pipeline is paused. Use /resume on Telegram."}

    logger.info("Manual pipeline trigger received.")

    # Run pipeline in background to avoid blocking the response
    import asyncio
    asyncio.create_task(orchestrator.run())

    return {"status": "triggered", "message": "Pipeline run started in background."}


@router.api_route("/trigger-scrape", methods=["GET", "POST"])
async def trigger_scrape(request: Request):
    """Manually trigger the scraping pipeline (designed for Cloud Scheduler).

    This endpoint runs synchronously (awaiting the orchestrator) to prevent
    Cloud Run from freezing CPU during execution.
    """
    orchestrator = request.app.state.orchestrator
    if orchestrator.is_paused:
        return {"status": "paused", "message": "Pipeline is paused. Use /resume on Telegram."}

    logger.info("Scraping pipeline trigger received via /trigger-scrape.")

    # Run the pipeline synchronously to keep CPU active on Cloud Run
    stats = await orchestrator.run()

    return {"status": "success", "stats": stats}



@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook.

    This endpoint is registered with Telegram's setWebhook API.
    All incoming messages and callback queries are routed here.
    """
    try:
        update_data = await request.json()
        bot = request.app.state.bot
        dp = request.app.state.dp

        from aiogram.types import Update
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot=bot, update=update)

        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Error processing Telegram webhook: {e}", exc_info=True)
        return Response(status_code=200)  # Always return 200 to Telegram
