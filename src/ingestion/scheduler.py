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
    hourly_hunt_callback: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> AsyncIOScheduler:
    """Create, configure, and start the async scheduler.

    Parameters
    ----------
    pipeline_callback:
        Async callable invoked every ``settings.ingestion_interval_hours``
        to run the release & sync pipeline.
    digest_callback:
        Async callable invoked daily at ``settings.digest_hour`` (local time) to
        send the daily job digest.
    hourly_hunt_callback:
        Optional async callable invoked every 1 hour to crawl trusted platforms silently.

    Returns
    -------
    AsyncIOScheduler
        A *started* scheduler instance.
    """
    settings = get_settings()
    # Uses local timezone to trigger daily digest at exact local hour
    scheduler = AsyncIOScheduler()

    # --- Ingestion job: every N hours (default 3) ---
    scheduler.add_job(
        pipeline_callback,
        trigger=IntervalTrigger(hours=settings.ingestion_interval_hours),
        id="ingestion_pipeline",
        name="Ingestion Pipeline (Release & Sync)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled ingestion pipeline — every %d hour(s).",
        settings.ingestion_interval_hours,
    )

    # --- Hourly Hunt job: every 1 hour ---
    if hourly_hunt_callback is not None:
        scheduler.add_job(
            hourly_hunt_callback,
            trigger=IntervalTrigger(hours=1),
            id="hourly_hunt",
            name="Hourly Hunt (Staging)",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("Scheduled hourly hunt pipeline — every 1 hour.")

    # --- Digest job: daily at configured hour (local time) ---
    scheduler.add_job(
        digest_callback,
        trigger=CronTrigger(hour=settings.digest_hour, minute=0),
        id="daily_digest",
        name="Daily Digest",
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled daily digest — every day at %02d:00 local time.",
        settings.digest_hour,
    )

    scheduler.start()
    logger.info("Scheduler started.")
    return scheduler
