"""JAS Telegram Bot — Core bot and webhook setup.

Creates the aiogram Bot and Dispatcher instances and wires a
FastAPI-compatible webhook route so the bot runs inside the same
ASGI process as the rest of the JAS pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from src.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Webhook path — kept as a constant so callers can reference it.
# ---------------------------------------------------------------------------
WEBHOOK_PATH = "/telegram/webhook"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def setup_bot() -> tuple[Bot, Dispatcher]:
    """Instantiate and return a configured ``(Bot, Dispatcher)`` pair.

    The Bot is created with the token from environment settings and HTML
    parse-mode enabled by default.  The Dispatcher is plain — handlers
    are registered separately via ``handlers.register_handlers(dp)``.
    """
    settings = get_settings()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    logger.info("Aiogram Bot and Dispatcher created (parse_mode=HTML).")
    return bot, dp


# ---------------------------------------------------------------------------
# Webhook integration with FastAPI
# ---------------------------------------------------------------------------

def setup_webhook(app: "FastAPI", bot: Bot, dp: Dispatcher) -> None:
    """Register Telegram webhook lifecycle hooks on a FastAPI application.

    * On **startup** the webhook URL is set via the Telegram API and
      aiogram's ``SimpleRequestHandler`` is mounted at ``WEBHOOK_PATH``.
    * On **shutdown** the webhook is deleted and the bot session is closed.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    bot:
        An aiogram ``Bot`` already configured with a valid token.
    dp:
        An aiogram ``Dispatcher`` with handlers registered.
    """
    settings = get_settings()

    # Build the full external webhook URL.  Expects the host to be set as an
    # env var or derived from the deployment environment.  Falls back to a
    # localhost placeholder during development.
    webhook_host = getattr(settings, "webhook_host", "https://localhost:8000")
    webhook_url = f"{webhook_host}{WEBHOOK_PATH}"

    # ------------------------------------------------------------------
    # Startup hook
    # ------------------------------------------------------------------
    @app.on_event("startup")
    async def _on_startup() -> None:  # noqa: WPS430
        """Set the Telegram webhook and mount the handler route."""
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook set → %s", webhook_url)

        # Mount the aiogram SimpleRequestHandler — it speaks ASGI through
        # Starlette's routing layer which FastAPI extends.
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=WEBHOOK_PATH)  # type: ignore[arg-type]
        logger.info("SimpleRequestHandler registered at %s", WEBHOOK_PATH)

    # ------------------------------------------------------------------
    # Shutdown hook
    # ------------------------------------------------------------------
    @app.on_event("shutdown")
    async def _on_shutdown() -> None:  # noqa: WPS430
        """Remove the webhook and close the bot session."""
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.session.close()
        logger.info("Telegram webhook removed and bot session closed.")
