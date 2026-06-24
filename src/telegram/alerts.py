"""Telegram alert dispatcher — sends system notifications via Telegram Bot API.

Uses httpx directly (no aiogram dependency) to POST formatted messages
to a Telegram chat. Supports INFO, WARNING, and CRITICAL severity levels.
"""

from __future__ import annotations

import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

_SEVERITY_ICONS: dict[str, str] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "CRITICAL": "🚨",
}

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_system_alert(message: str, severity: str = "WARNING") -> None:
    """Send a formatted alert to the configured Telegram chat.

    Parameters
    ----------
    message:
        Human-readable alert body.
    severity:
        One of ``INFO``, ``WARNING``, ``CRITICAL``.  Controls the icon
        prepended to the message.
    """
    settings = get_settings()
    icon = _SEVERITY_ICONS.get(severity.upper(), "⚠️")

    text = (
        f"{icon} *JAS System Alert — {severity.upper()}*\n\n"
        f"{message}"
    )

    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Telegram alert sent (severity=%s).", severity.upper())
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Telegram API returned %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error("Failed to reach Telegram API: %s", exc)
