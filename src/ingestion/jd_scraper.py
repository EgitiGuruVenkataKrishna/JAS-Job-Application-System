"""Job-description scraper — fetches and parses job postings from multiple ATS platforms.

Supports platform-specific parsing for LinkedIn, Greenhouse, Lever, and
falls back to generic whole-body text extraction for unknown sites.  ATS type
is inferred from URL patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class ScrapedJob:
    """Structured representation of a scraped job posting."""

    title: str
    company: str
    location: str
    jd_text: str
    ats_type: str
    url: str


# ------------------------------------------------------------------
# ATS detection
# ------------------------------------------------------------------

_ATS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"boards\.greenhouse\.io", re.I), "greenhouse"),
    (re.compile(r"jobs\.lever\.co", re.I), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com", re.I), "ashby"),
    (re.compile(r"myworkdayjobs\.com|wd\d+\.myworkday\.com", re.I), "workday"),
    (re.compile(r"linkedin\.com", re.I), "linkedin"),
]


def _detect_ats(url: str) -> str:
    """Return ATS identifier based on URL patterns."""
    for pattern, ats in _ATS_PATTERNS:
        if pattern.search(url):
            return ats
    return "unknown"


# ------------------------------------------------------------------
# Platform-specific parsers
# ------------------------------------------------------------------


def _parse_linkedin(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """Extract fields from a LinkedIn job page."""
    title = _text(soup.select_one("h1, .top-card-layout__title, .topcard__title"))
    company = _text(
        soup.select_one(
            ".topcard__org-name-link, .top-card-layout__second-subline a, "
            ".topcard__flavor a"
        )
    )
    location = _text(
        soup.select_one(
            ".topcard__flavor--bullet, .top-card-layout__second-subline span, "
            ".topcard__flavor:nth-of-type(2)"
        )
    )
    jd = _text(
        soup.select_one(
            ".description__text, .show-more-less-html__markup, "
            ".decorated-job-posting__details"
        )
    )
    return title, company, location, jd


def _parse_greenhouse(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """Extract fields from a Greenhouse board page."""
    title = _text(soup.select_one("h1.app-title, .job__title h1, h1"))
    company = _text(soup.select_one(".company-name, .logo span, h2"))
    location = _text(soup.select_one(".location, .body--metadata span"))
    content_div = soup.select_one("#content, .job__description, .content")
    jd = _text(content_div) if content_div else ""
    return title, company, location, jd


def _parse_lever(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """Extract fields from a Lever jobs page."""
    title = _text(soup.select_one("h2, .posting-headline h2"))
    company = _text(soup.select_one(".main-header-logo img", attr="alt"))
    location = _text(
        soup.select_one(
            ".posting-categories .sort-by-time, .location, "
            ".posting-categories .workplaceTypes"
        )
    )
    posting = soup.select_one(".posting-page, .content")
    jd = _text(posting) if posting else ""
    return title, company, location, jd


def _parse_generic(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """Fallback: extract whatever text exists in the document body."""
    title = _text(soup.select_one("h1")) or _text(soup.select_one("title"))
    body = soup.find("body")
    jd = _text(body) if body else ""
    return title, "", "", jd


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _text(element: Tag | None, *, attr: str | None = None) -> str:
    """Safely extract text from a BS4 element (or an attribute value)."""
    if element is None:
        return ""
    if attr:
        return str(element.get(attr, "")).strip()
    return element.get_text(separator="\n", strip=True)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


class JdScraper:
    """Asynchronous scraper with platform-aware parsers."""

    async def scrape(self, url: str) -> ScrapedJob | None:
        """Fetch *url* and parse the job description.

        Returns ``None`` on any HTTP or parsing error so the caller can
        skip gracefully.
        """
        try:
            html = await self._fetch(url)
            if not html:
                return None

            ats = _detect_ats(url)
            soup = BeautifulSoup(html, "html.parser")

            title, company, location, jd_text = self._dispatch(ats, soup)

            if not jd_text:
                logger.warning("Empty JD text after parsing %s — falling back to generic.", url)
                title, company, location, jd_text = _parse_generic(soup)

            return ScrapedJob(
                title=title or "Unknown",
                company=company or "Unknown",
                location=location or "Unknown",
                jd_text=jd_text,
                ats_type=ats,
                url=url,
            )
        except Exception:
            logger.exception("Failed to scrape %s", url)
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch(url: str) -> str | None:
        """Download the page HTML with a browser-like User-Agent."""
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP %s for %s", exc.response.status_code, url)
        except httpx.RequestError as exc:
            logger.error("Request error for %s: %s", url, exc)
        return None

    @staticmethod
    def _dispatch(
        ats: str, soup: BeautifulSoup
    ) -> tuple[str, str, str, str]:
        """Route to the correct platform parser."""
        parsers = {
            "linkedin": _parse_linkedin,
            "greenhouse": _parse_greenhouse,
            "lever": _parse_lever,
        }
        parser = parsers.get(ats, _parse_generic)
        return parser(soup)
