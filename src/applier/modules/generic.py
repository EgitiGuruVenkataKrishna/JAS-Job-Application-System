"""Generic ATS module — heuristic-based form filling for unrecognised application pages."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# Label text → profile key mapping (case-insensitive matching)
_LABEL_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfirst\s*name\b", re.IGNORECASE), "first_name"),
    (re.compile(r"\blast\s*name\b|surname", re.IGNORECASE), "last_name"),
    (re.compile(r"\bfull\s*name\b|\byour\s*name\b|^name$", re.IGNORECASE), "_full_name"),
    (re.compile(r"\be-?mail\b", re.IGNORECASE), "email"),
    (re.compile(r"\bphone\b|\bmobile\b|\btelephone\b", re.IGNORECASE), "phone"),
    (re.compile(r"\blinkedin\b", re.IGNORECASE), "linkedin_url"),
    (re.compile(r"\bwebsite\b|\bportfolio\b", re.IGNORECASE), "website_url"),
    (re.compile(r"\bcurrent\s*company\b|\bemployer\b", re.IGNORECASE), "current_company"),
    (re.compile(r"\bcity\b|\blocation\b", re.IGNORECASE), "location"),
]

_FIELD_TIMEOUT_MS = 2_000
_SUBMIT_WAIT_MS = 8_000


class GenericModule:
    """Best-effort heuristic form filler for unknown / generic ATS pages.

    Scans all ``<label>`` elements on the page, matches their text against
    known field patterns, locates the associated input, and fills it from
    the user profile.  File inputs are used for resume upload.

    This module **may fail** on complex multi-step or JavaScript-heavy forms.
    """

    async def fill_and_submit(
        self,
        page: Page,
        user_profile: dict,
        resume_path: str,
        cover_letter_path: str | None = None,
    ) -> bool:
        """Attempt to fill and submit a generic application form.

        Args:
            page: Playwright page at the application URL.
            user_profile: Candidate profile dict.
            resume_path: Absolute path to the resume file.
            cover_letter_path: Optional cover letter path.

        Returns:
            ``True`` if the form appeared to submit successfully.
        """
        try:
            await self._fill_fields_by_labels(page, user_profile)
            await self._upload_files(page, resume_path, cover_letter_path)
            return await self._submit(page)
        except Exception:
            logger.error("Generic form fill failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fill_fields_by_labels(self, page: Page, profile: dict) -> None:
        """Scan labels and fill associated inputs from the profile."""
        labels = await page.query_selector_all("label")
        filled_count = 0

        for label_el in labels:
            try:
                label_text = (await label_el.inner_text()).strip()
                if not label_text:
                    continue

                profile_key = self._match_label(label_text)
                if profile_key is None:
                    continue

                # Resolve value
                if profile_key == "_full_name":
                    value = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                else:
                    value = profile.get(profile_key, "")
                if not value:
                    continue

                # Find the associated input
                input_el = await self._find_input_for_label(page, label_el)
                if input_el is None:
                    continue

                # Only fill text-like inputs
                input_type = (await input_el.get_attribute("type") or "text").lower()
                if input_type in ("text", "email", "tel", "url", "search", ""):
                    await input_el.fill(value)
                    filled_count += 1
                    logger.debug("Generic: filled '%s' → %s", label_text, profile_key)
            except Exception:
                logger.debug("Generic: error processing label", exc_info=True)

        logger.info("Generic module filled %d fields via label scanning", filled_count)

    @staticmethod
    def _match_label(label_text: str) -> str | None:
        """Return the profile key matching *label_text*, or ``None``."""
        for pattern, key in _LABEL_MAP:
            if pattern.search(label_text):
                return key
        return None

    @staticmethod
    async def _find_input_for_label(page: Page, label_el) -> object | None:
        """Find the input element associated with a ``<label>``."""
        # 1. label[for] → #id
        for_attr = await label_el.get_attribute("for")
        if for_attr:
            input_el = await page.query_selector(f"#{for_attr}")
            if input_el:
                return input_el

        # 2. Nested input inside the label
        nested = await label_el.query_selector("input, textarea, select")
        if nested:
            return nested

        # 3. Next sibling input
        sibling = await label_el.evaluate_handle(
            "el => el.nextElementSibling && "
            "(el.nextElementSibling.tagName === 'INPUT' || el.nextElementSibling.tagName === 'TEXTAREA') "
            "? el.nextElementSibling : null"
        )
        element = sibling.as_element()
        if element:
            tag = await element.evaluate("el => el.tagName")
            if tag in ("INPUT", "TEXTAREA"):
                return element

        return None

    async def _upload_files(
        self,
        page: Page,
        resume_path: str,
        cover_letter_path: str | None,
    ) -> None:
        """Upload resume (and optionally cover letter) to file inputs."""
        file_inputs = await page.query_selector_all('input[type="file"]')
        if not file_inputs:
            logger.debug("Generic: no file inputs found")
            return

        # First file input → resume
        if Path(resume_path).exists():
            try:
                await file_inputs[0].set_input_files(resume_path)
                logger.info("Generic: resume uploaded")
            except Exception:
                logger.warning("Generic: resume upload failed", exc_info=True)

        # Second file input → cover letter
        if (
            cover_letter_path
            and Path(cover_letter_path).exists()
            and len(file_inputs) >= 2
        ):
            try:
                await file_inputs[1].set_input_files(cover_letter_path)
                logger.info("Generic: cover letter uploaded")
            except Exception:
                logger.debug("Generic: cover letter upload failed", exc_info=True)

    async def _submit(self, page: Page) -> bool:
        """Find and click the most likely submit button."""
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
            'button:has-text("Send")',
            'a:has-text("Submit Application")',
        ]
        for sel in submit_selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info("Generic: clicked submit via %s", sel)
                    break
            except PlaywrightTimeout:
                continue
        else:
            logger.error("Generic: no submit button found")
            return False

        # Wait for success indication
        try:
            await page.wait_for_selector(
                'text=/thank you|application submitted|successfully|received/i',
                timeout=_SUBMIT_WAIT_MS,
            )
            logger.info("✅ Generic application submitted successfully")
            return True
        except PlaywrightTimeout:
            if "thank" in page.url.lower() or "success" in page.url.lower():
                return True
            logger.warning("Generic: success indicator not detected")
            return False
