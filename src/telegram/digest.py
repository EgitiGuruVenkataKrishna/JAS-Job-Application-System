"""JAS Telegram Digest — Daily statistics summary.

Provides the ``DigestGenerator`` class whose ``send_daily_digest`` method
queries the database for today's pipeline statistics and delivers a
formatted summary to the configured Telegram chat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder / optional imports
# ---------------------------------------------------------------------------
try:
    from src.db.client import DatabaseClient  # type: ignore[import-untyped]
except ImportError:
    DatabaseClient = None  # type: ignore[assignment,misc]
    logger.warning("src.db.client not available — digest will use stub data.")


class DigestGenerator:
    """Compiles and sends a daily pipeline digest via Telegram.

    Usage::

        generator = DigestGenerator()
        await generator.send_daily_digest(bot)
    """

    # Rough estimate: average time to manually find + apply to one job (min).
    _MANUAL_MINUTES_PER_JOB: int = 12

    def __init__(self, db: Any = None) -> None:
        self._settings = get_settings()
        self._db: Any = db

    # ------------------------------------------------------------------
    # Lazy DB access
    # ------------------------------------------------------------------

    def _get_db(self) -> Any:
        """Return a ``DatabaseClient`` singleton or ``None``."""
        if self._db is None and DatabaseClient is not None:
            self._db = DatabaseClient()
        return self._db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_daily_digest(self, bot: Bot) -> None:
        """Query today's stats from the database and send the digest.

        The message is sent to ``settings.telegram_chat_id``.

        Parameters
        ----------
        bot:
            The aiogram ``Bot`` instance used to send the message.
        """
        chat_id = self._settings.telegram_chat_id
        stats = await self._fetch_stats()
        text = self._format_digest(stats)

        try:
            await bot.send_message(chat_id=chat_id, text=text)
            logger.info("Daily digest sent to chat %s", chat_id)
        except Exception:
            logger.exception("Failed to send daily digest to chat %s", chat_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_stats(self) -> dict[str, int]:
        """Retrieve today's pipeline statistics from the database.

        Returns a dict with keys:
        ``scanned``, ``math_passed``, ``ai_passed``,
        ``applied_auto``, ``applied_manual``, ``skipped``.
        """
        db = self._get_db()
        if db is not None:
            try:
                return await db.get_daily_stats()
            except Exception:
                logger.exception("Failed to fetch daily stats from DB")

        # Fallback stub when the DB is unavailable.
        return {
            "scanned": 0,
            "math_passed": 0,
            "ai_passed": 0,
            "applied_auto": 0,
            "applied_manual": 0,
            "skipped": 0,
        }

    def _format_digest(self, stats: dict[str, int]) -> str:
        """Build the digest message body from raw statistics.

        Parameters
        ----------
        stats:
            A dict as returned by ``_fetch_stats``.

        Returns
        -------
        str
            HTML-formatted digest message ready for Telegram.
        """
        scanned = stats.get("scanned", 0)
        math_passed = stats.get("math_passed", 0)
        ai_passed = stats.get("ai_passed", 0)
        applied_auto = stats.get("applied_auto", 0)
        applied_manual = stats.get("applied_manual", 0)
        skipped = stats.get("skipped", 0)

        total_actioned = applied_auto + applied_manual + skipped
        hours_saved = round((total_actioned * self._MANUAL_MINUTES_PER_JOB) / 60, 1)

        digest_hour = self._settings.digest_hour
        now = datetime.now(tz=timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        text = (
            f"📊 <b>Daily Digest</b> ({digest_hour}:00) — {date_str}\n\n"
            f"Jobs Scanned: <b>{scanned}</b> | "
            f"Passed Math Gate: <b>{math_passed}</b> | "
            f"Passed AI Gate: <b>{ai_passed}</b>\n"
            f"Applied: <b>{applied_auto}</b> (auto) | "
            f"<b>{applied_manual}</b> (manual) | "
            f"Skipped: <b>{skipped}</b>\n\n"
            f"You saved approx <b>{hours_saved}</b> hours of manual hunting today."
        )
        return text
