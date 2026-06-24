"""APScheduler-based scheduler for the JAS ingestion and digest pipelines.

Provides a single ``setup_scheduler`` factory that wires two recurring jobs
and returns a *started* ``AsyncIOScheduler``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_settings

logger = logging.getLogger(__name__)


def setup_scheduler(
    pipeline_callback: Callable[[], Coroutine[Any, Any, None]],
    digest_callback: Callable[[], Coroutine[Any, Any, None]],
) -> AsyncIOScheduler:
    """Create, configure, and start the async scheduler.

    Parameters
    ----------
    pipeline_callback:
        Async callable invoked every ``settings.ingestion_interval_hours``
        to run the full ingestion pipeline.
    digest_callback:
        Async callable invoked daily at ``settings.digest_hour`` (UTC) to
        send the daily job digest.

    Returns
    -------
    AsyncIOScheduler
        A *started* scheduler instance.  The caller is responsible for
        keeping the event loop alive.
    """
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")

    # --- Ingestion job: every N hours ---
    scheduler.add_job(
        pipeline_callback,
        trigger=IntervalTrigger(hours=settings.ingestion_interval_hours),
        id="ingestion_pipeline",
        name="Ingestion Pipeline",
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled ingestion pipeline — every %d hour(s).",
        settings.ingestion_interval_hours,
    )

    # --- Digest job: daily at configured hour ---
    scheduler.add_job(
        digest_callback,
        trigger=CronTrigger(hour=settings.digest_hour, minute=0),
        id="daily_digest",
        name="Daily Digest",
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled daily digest — every day at %02d:00 UTC.",
        settings.digest_hour,
    )

    scheduler.start()
    logger.info("Scheduler started.")
    return scheduler
