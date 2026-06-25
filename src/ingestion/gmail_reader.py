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
    title: str
    company: str
    location: str
    jd_text: str
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Senders whose UNSEEN emails we process.
_ALERT_SENDERS: list[str] = [
    "alerts@linkedin.com",
    "noreply@wellfound.com",
    "noreply@indeed.com",
    "alerts@indeed.com",
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
                    platform = self._platform_from_sender(sender)
                    extracted_jobs = self._extract_jobs(parsed, platform)
                    jobs.extend(extracted_jobs)

                    # Mark email as READ.
                    conn.store(msg_id, "+FLAGS", "\\Seen")

            logger.info("Inbox scan complete — discovered %d raw job(s).", len(jobs))
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
    def _extract_jobs(msg: Message, platform: str) -> list[RawJob]:
        """Walk MIME parts, parse HTML bodies, and return structured RawJob items."""
        jobs: list[RawJob] = []
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

            for anchor in soup.find_all("a", href=True):
                href: str = anchor["href"]
                if _is_job_url(href) and href not in seen:
                    seen.add(href)
                    
                    # 1. Parse Title
                    title = anchor.get_text(strip=True)
                    if not title or len(title) < 5 or title.lower() in ("apply", "view", "view job", "apply now", "learn more"):
                        parent = anchor.parent
                        if parent:
                            title = parent.get_text(strip=True)
                    
                    # 2. Parse Company
                    company = "Unknown Company"
                    parent = anchor.parent
                    if parent:
                        parent_text = parent.get_text(separator=" | ", strip=True)
                        import re
                        match = re.search(r"\bat\s+([^|]+)", parent_text, re.I)
                        if match:
                            company = match.group(1).strip()
                        else:
                            siblings = list(parent.next_siblings)
                            for sib in siblings:
                                sib_text = sib.get_text(strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                                if sib_text and len(sib_text) > 1:
                                    company = sib_text
                                    break
                                    
                    # 3. Parse Location
                    location = "Remote"
                    if parent:
                        parent_text = parent.get_text(separator=" | ", strip=True)
                        import re
                        match = re.search(r"\b(in|at)\s+([^|]+)", parent_text, re.I)
                        if match and match.group(2).strip().lower() not in company.lower():
                            location = match.group(2).strip()

                    # 4. Parse JD Snippet (brief description)
                    jd_text = ""
                    curr = anchor
                    for _ in range(3):
                        if curr.parent:
                            curr = curr.parent
                    
                    if curr:
                        full_block_text = curr.get_text(separator="\n", strip=True)
                        lines = [line.strip() for line in full_block_text.split("\n") if line.strip()]
                        jd_lines = []
                        for line in lines:
                            if line.lower() not in (title.lower(), company.lower(), location.lower()) and len(line) > 15:
                                jd_lines.append(line)
                        if jd_lines:
                            jd_text = "\n".join(jd_lines[:3])

                    if not jd_text:
                        jd_text = f"Tech Internship at {company}. Check description online."

                    jobs.append(RawJob(
                        url=href,
                        platform=platform,
                        title=title,
                        company=company,
                        location=location,
                        jd_text=jd_text,
                    ))

        return jobs

    @staticmethod
    def _platform_from_sender(sender: str) -> str:
        """Derive a platform label from the sender email address."""
        sender_lower = sender.lower()
        if "linkedin" in sender_lower:
            return "linkedin"
        if "indeed" in sender_lower:
            return "indeed"
        if "wellfound" in sender_lower:
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
