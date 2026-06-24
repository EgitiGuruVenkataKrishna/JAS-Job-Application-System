"""ATS type detection — identifies the Applicant Tracking System from page URL and DOM."""

from __future__ import annotations

import enum
import logging
import re

from playwright.async_api import Page

logger = logging.getLogger(__name__)


class AtsType(enum.Enum):
    """Supported Applicant Tracking System types."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    UNKNOWN = "unknown"


# URL pattern → AtsType mapping (checked in order)
_URL_PATTERNS: list[tuple[re.Pattern[str], AtsType]] = [
    (re.compile(r"boards\.greenhouse\.io|job-boards\.greenhouse\.io", re.IGNORECASE), AtsType.GREENHOUSE),
    (re.compile(r"jobs\.lever\.co", re.IGNORECASE), AtsType.LEVER),
    (re.compile(r"jobs\.ashbyhq\.com", re.IGNORECASE), AtsType.ASHBY),
    (re.compile(r"myworkdayjobs\.com|workday\.com", re.IGNORECASE), AtsType.WORKDAY),
]

# DOM meta-tag content → AtsType mapping (fallback when URL doesn't match)
_META_CONTENT_PATTERNS: list[tuple[str, AtsType]] = [
    ("Greenhouse", AtsType.GREENHOUSE),
    ("Lever", AtsType.LEVER),
    ("Ashby", AtsType.ASHBY),
    ("Workday", AtsType.WORKDAY),
]

# ATS types that have working auto-fill modules
_SUPPORTED_ATS: frozenset[AtsType] = frozenset({
    AtsType.GREENHOUSE,
    AtsType.LEVER,
    AtsType.ASHBY,
})


class AtsDetector:
    """Detects the ATS powering a given job application page."""

    async def detect(self, page: Page) -> AtsType:
        """Identify the ATS type from the current page URL and DOM meta tags.

        Detection order:
            1. URL pattern matching (fast, reliable).
            2. ``<meta>`` tag content inspection (fallback).
            3. Default → ``AtsType.UNKNOWN``.

        Args:
            page: Playwright ``Page`` already navigated to the job posting.

        Returns:
            The detected :class:`AtsType`.
        """
        url = page.url
        logger.debug("Detecting ATS for URL: %s", url)

        # --- 1. URL-based detection ---
        for pattern, ats_type in _URL_PATTERNS:
            if pattern.search(url):
                logger.info("ATS detected via URL pattern: %s", ats_type.value)
                return ats_type

        # --- 2. DOM meta-tag inspection ---
        try:
            meta_elements = await page.query_selector_all("meta[name], meta[property], meta[content]")
            for meta in meta_elements:
                content = await meta.get_attribute("content") or ""
                for keyword, ats_type in _META_CONTENT_PATTERNS:
                    if keyword.lower() in content.lower():
                        logger.info("ATS detected via meta tag ('%s'): %s", keyword, ats_type.value)
                        return ats_type
        except Exception:
            logger.warning("Failed to inspect meta tags for ATS detection", exc_info=True)

        logger.info("ATS type could not be determined — returning UNKNOWN")
        return AtsType.UNKNOWN

    @staticmethod
    def is_supported(ats_type: AtsType) -> bool:
        """Return ``True`` if we have a working auto-fill module for *ats_type*.

        Workday and Unknown ATS types are **not** supported and will be
        routed through the fallback handler instead.
        """
        return ats_type in _SUPPORTED_ATS
