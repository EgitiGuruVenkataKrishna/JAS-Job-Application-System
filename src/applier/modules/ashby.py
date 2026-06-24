"""Ashby ATS module — auto-fill for jobs.ashbyhq.com application forms."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from src.applier.dynamic_answers import DynamicAnswerEngine

logger = logging.getLogger(__name__)

# Ashby-specific selectors
_SELECTORS = {
    "form_container": 'form[data-testid="application-form"], form.ashby-application-form, form',
    "first_name": 'input[name="first_name"], input[name="_systemfield_name"], input[placeholder*="First"]',
    "last_name": 'input[name="last_name"], input[placeholder*="Last"]',
    "email": 'input[name="email"], input[type="email"], input[name="_systemfield_email"]',
    "phone": 'input[name="phone"], input[type="tel"], input[name="_systemfield_phone"]',
    "linkedin": 'input[name="linkedin"], input[name="linkedInUrl"], input[placeholder*="LinkedIn"]',
    "resume_input": 'input[type="file"][name*="resume"], input[type="file"]',
    "submit_button": 'button[type="submit"], button:has-text("Submit"), button:has-text("Apply")',
}

_FIELD_TIMEOUT_MS = 3_000
_SUBMIT_WAIT_MS = 10_000


class AshbyModule:
    """Auto-fills and submits Ashby (jobs.ashbyhq.com) application forms.

    Ashby forms often use React-based inputs with ``data-testid`` attributes
    and ``_systemfield_`` prefixed names for built-in fields.
    """

    def __init__(self) -> None:
        self._answer_engine = DynamicAnswerEngine()

    async def fill_and_submit(
        self,
        page: Page,
        user_profile: dict,
        resume_path: str,
        cover_letter_path: str | None = None,
    ) -> bool:
        """Fill and submit an Ashby application form.

        Args:
            page: Playwright page at the Ashby application URL.
            user_profile: Candidate profile dict.
            resume_path: Absolute path to the resume file.
            cover_letter_path: Optional cover letter path.

        Returns:
            ``True`` on successful submission.
        """
        try:
            await page.wait_for_selector(
                _SELECTORS["form_container"], timeout=5_000
            )

            await self._fill_basic_fields(page, user_profile)
            await self._upload_resume(page, resume_path)

            if cover_letter_path and Path(cover_letter_path).exists():
                await self._upload_cover_letter(page, cover_letter_path)

            await self._handle_custom_questions(page, user_profile)

            return await self._submit(page)

        except Exception:
            logger.error("Ashby form fill failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fill_basic_fields(self, page: Page, profile: dict) -> None:
        """Fill standard Ashby personal-info fields."""
        field_mapping = {
            _SELECTORS["first_name"]: profile.get("first_name", ""),
            _SELECTORS["last_name"]: profile.get("last_name", ""),
            _SELECTORS["email"]: profile.get("email", ""),
            _SELECTORS["phone"]: profile.get("phone", ""),
            _SELECTORS["linkedin"]: profile.get("linkedin_url", ""),
        }
        for selector_group, value in field_mapping.items():
            if not value:
                continue
            for sel in selector_group.split(", "):
                try:
                    await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                    await page.fill(sel, value)
                    logger.debug("Filled Ashby field: %s", sel)
                    break
                except PlaywrightTimeout:
                    continue

    async def _upload_resume(self, page: Page, resume_path: str) -> None:
        """Upload resume via the file input."""
        if not Path(resume_path).exists():
            logger.error("Resume file not found: %s", resume_path)
            return
        for sel in _SELECTORS["resume_input"].split(", "):
            try:
                handle = await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                if handle:
                    await handle.set_input_files(resume_path)
                    logger.info("Resume uploaded via: %s", sel)
                    return
            except PlaywrightTimeout:
                continue
        logger.warning("No file input found on Ashby form")

    async def _upload_cover_letter(self, page: Page, cover_letter_path: str) -> None:
        """Attempt to upload a cover letter to a secondary file input."""
        try:
            file_inputs = await page.query_selector_all('input[type="file"]')
            if len(file_inputs) >= 2:
                await file_inputs[1].set_input_files(cover_letter_path)
                logger.info("Cover letter uploaded to second file input")
            else:
                logger.debug("No secondary file input for cover letter on Ashby form")
        except Exception:
            logger.debug("Cover letter upload failed on Ashby form", exc_info=True)

    async def _handle_custom_questions(self, page: Page, profile: dict) -> None:
        """Answer custom Ashby questions via the dynamic answer engine."""
        custom_fields = await page.query_selector_all(
            'textarea, input[type="text"]:not([name*="name"]):not([name*="email"])'
            ':not([name*="phone"]):not([name*="linkedin"])'
        )
        for field in custom_fields:
            try:
                current = await field.input_value()
                if current.strip():
                    continue

                question = await self._get_question_text(page, field)
                if not question:
                    continue

                answer = await self._answer_engine.answer(
                    question=question,
                    jd_text="",
                    resume_json=profile,
                )
                await field.fill(answer)
                logger.info("Ashby custom answer for '%s': '%s'", question[:50], answer[:50])
            except Exception:
                logger.debug("Error filling Ashby custom field", exc_info=True)

    @staticmethod
    async def _get_question_text(page: Page, field) -> str:
        """Extract question label for an Ashby field."""
        field_id = await field.get_attribute("id")
        if field_id:
            label = await page.query_selector(f'label[for="{field_id}"]')
            if label:
                return (await label.inner_text()).strip()

        aria = await field.get_attribute("aria-label")
        if aria:
            return aria.strip()

        # Walk up to parent container
        parent = await field.evaluate_handle(
            "el => el.closest('[data-testid], .ashby-field, .form-field')"
        )
        if parent:
            el = parent.as_element()
            if el:
                label = await el.query_selector("label, .field-label")
                if label:
                    return (await label.inner_text()).strip()

        placeholder = await field.get_attribute("placeholder")
        return (placeholder or "").strip()

    async def _submit(self, page: Page) -> bool:
        """Click submit and verify success."""
        for sel in _SELECTORS["submit_button"].split(", "):
            try:
                btn = await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked Ashby submit: %s", sel)
                    break
            except PlaywrightTimeout:
                continue
        else:
            logger.error("No Ashby submit button found")
            return False

        try:
            await page.wait_for_selector(
                'text=/thank you|application submitted|we.*received your/i',
                timeout=_SUBMIT_WAIT_MS,
            )
            logger.info("✅ Ashby application submitted successfully")
            return True
        except PlaywrightTimeout:
            if "thank" in page.url.lower() or "success" in page.url.lower():
                return True
            logger.warning("Ashby success indicator not found")
            return False
