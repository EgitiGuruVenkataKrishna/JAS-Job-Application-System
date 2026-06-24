"""Gmail IMAP reader — intercepts job-alert emails and extracts job URLs.

Connects to Gmail via ``imaplib.IMAP4_SSL``, searches for UNSEEN emails
from known job-alert senders, parses the HTML body with BeautifulSoup to
extract job URLs, and marks processed emails as READ.

Resilience: IMAP connection is wrapped in a retry loop (3 attempts with
exponential backoff: 2 s → 4 s → 8 s).  On total failure the module
dispatches a Telegram alert via ``src.telegram.alerts``.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from typing import Sequence

from bs4 import BeautifulSoup

from src.config import get_settings
from src.telegram.alerts import send_system_alert

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawJob:
    """Lightweight container for a newly-discovered job posting."""

    url: str
    platform: str
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Senders whose UNSEEN emails we process.
_ALERT_SENDERS: list[str] = [
    "alerts@linkedin.com",
    "noreply@wellfound.com",
]

# Exponential-backoff schedule (seconds).
_RETRY_DELAYS: tuple[int, ...] = (2, 4, 8)

# URL pattern that likely points to a real job posting.
_JOB_URL_RE = re.compile(
    r"https?://[^\s\"'>]+",
    re.IGNORECASE,
)


class InboxInterceptor:
    """Reads Gmail IMAP inbox for job-alert emails and yields ``RawJob`` items."""

    def __init__(self) -> None:
        settings = get_settings()
        self._user: str = settings.gmail_user
        self._password: str = settings.gmail_app_password

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_new_jobs(self) -> list[RawJob]:
        """Fetch all UNSEEN job-alert emails and return extracted ``RawJob`` items.

        Retries up to 3 times with exponential backoff on IMAP failure.
        Sends a Telegram alert if all retries are exhausted.
        """
        last_exc: Exception | None = None

        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                # imaplib is synchronous — run in the default executor.
                return await asyncio.get_running_loop().run_in_executor(
                    None, self._fetch_sync
                )
            except (imaplib.IMAP4.error, OSError, ConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "IMAP attempt %d/%d failed: %s — retrying in %ds …",
                    attempt,
                    len(_RETRY_DELAYS),
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        # All retries exhausted — alert via Telegram.
        error_msg = (
            f"Gmail IMAP connection failed after {len(_RETRY_DELAYS)} retries.\n"
            f"Last error: {last_exc}"
        )
        logger.error(error_msg)
        await send_system_alert(error_msg, severity="CRITICAL")
        return []

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run in executor)
    # ------------------------------------------------------------------

    def _fetch_sync(self) -> list[RawJob]:
        """Synchronous IMAP fetch logic."""
        jobs: list[RawJob] = []
        conn = imaplib.IMAP4_SSL("imap.gmail.com")

        try:
            conn.login(self._user, self._password)
            conn.select("INBOX")

            for sender in _ALERT_SENDERS:
                msg_ids = self._search_unseen(conn, sender)
                for msg_id in msg_ids:
                    raw_email = self._fetch_message(conn, msg_id)
                    if raw_email is None:
                        continue

                    parsed = email.message_from_bytes(raw_email)
                    urls = self._extract_urls(parsed)
                    platform = self._platform_from_sender(sender)

                    for url in urls:
                        jobs.append(RawJob(url=url, platform=platform))

                    # Mark email as READ.
                    conn.store(msg_id, "+FLAGS", "\\Seen")

            logger.info("Inbox scan complete — discovered %d raw job URL(s).", len(jobs))
        finally:
            try:
                conn.close()
            except imaplib.IMAP4.error:
                pass
            conn.logout()

        return jobs

    @staticmethod
    def _search_unseen(conn: imaplib.IMAP4_SSL, sender: str) -> list[bytes]:
        """Search for UNSEEN messages from *sender*."""
        status, data = conn.search(None, "UNSEEN", f'FROM "{sender}"')
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    @staticmethod
    def _fetch_message(conn: imaplib.IMAP4_SSL, msg_id: bytes) -> bytes | None:
        """Fetch the full RFC822 message for *msg_id*."""
        status, data = conn.fetch(msg_id, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            return None
        return data[0][1]  # type: ignore[index]

    @staticmethod
    def _extract_urls(msg: Message) -> list[str]:
        """Walk MIME parts, parse HTML bodies, and return unique job URLs."""
        urls: list[str] = []
        seen: set[str] = set()

        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type != "text/html":
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue

            charset = part.get_content_charset() or "utf-8"
            html = payload.decode(charset, errors="replace")
            soup = BeautifulSoup(html, "html.parser")

            # Extract href attributes from anchor tags.
            for anchor in soup.find_all("a", href=True):
                href: str = anchor["href"]
                if _is_job_url(href) and href not in seen:
                    seen.add(href)
                    urls.append(href)

        return urls

    @staticmethod
    def _platform_from_sender(sender: str) -> str:
        """Derive a platform label from the sender email address."""
        if "linkedin" in sender:
            return "linkedin"
        if "wellfound" in sender:
            return "wellfound"
        return "unknown"


def _is_job_url(url: str) -> bool:
    """Heuristic filter — keep URLs that look like actual job postings."""
    skip_patterns = (
        "unsubscribe",
        "mailto:",
        "help.linkedin",
        "privacy",
        "terms",
        "#",
    )
    return not any(p in url.lower() for p in skip_patterns)
