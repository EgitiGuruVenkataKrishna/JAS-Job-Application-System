"""Lever ATS module — auto-fill for jobs.lever.co application forms."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from src.applier.dynamic_answers import DynamicAnswerEngine

logger = logging.getLogger(__name__)

# Lever-specific selectors
_SELECTORS = {
    "form_container": ".application-form, .postings-btn-wrapper + div, form",
    "full_name": 'input[name="name"], input[name="fullName"]',
    "email": 'input[name="email"], input[type="email"]',
    "phone": 'input[name="phone"], input[type="tel"]',
    "current_company": 'input[name="org"], input[name="currentCompany"]',
    "linkedin": (
        'input[name="urls[LinkedIn]"], input[name="linkedin"], '
        'input[placeholder*="LinkedIn"]'
    ),
    "website": 'input[name="urls[Portfolio]"], input[name="urls[Other]"]',
    "resume_input": 'input[type="file"][name="resume"], input[type="file"]',
    "submit_button": 'button[type="submit"], .postings-btn, button.application-submit',
}

_FIELD_TIMEOUT_MS = 3_000
_SUBMIT_WAIT_MS = 10_000


class LeverModule:
    """Auto-fills and submits Lever (jobs.lever.co) application forms.

    Lever forms typically live inside an ``.application-form`` container
    and use name-based inputs rather than id-based ones.
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
        """Fill and submit a Lever application form.

        Args:
            page: Playwright page at the Lever application URL.
            user_profile: Candidate profile dict.
            resume_path: Absolute path to the resume file.
            cover_letter_path: Unused for Lever (single file upload).

        Returns:
            ``True`` on successful submission.
        """
        try:
            # Wait for the form to be present
            await page.wait_for_selector(
                _SELECTORS["form_container"], timeout=5_000
            )

            # --- 1. Fill standard fields ---
            await self._fill_text_fields(page, user_profile)

            # --- 2. Upload resume ---
            await self._upload_resume(page, resume_path)

            # --- 3. Handle custom questions ---
            await self._handle_custom_questions(page, user_profile)

            # --- 4. Submit ---
            return await self._submit(page)

        except Exception:
            logger.error("Lever form fill failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fill_text_fields(self, page: Page, profile: dict) -> None:
        """Fill standard Lever text inputs."""
        full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        mapping = {
            _SELECTORS["full_name"]: full_name,
            _SELECTORS["email"]: profile.get("email", ""),
            _SELECTORS["phone"]: profile.get("phone", ""),
            _SELECTORS["current_company"]: profile.get("current_company", ""),
            _SELECTORS["linkedin"]: profile.get("linkedin_url", ""),
        }
        for selector, value in mapping.items():
            if not value:
                continue
            for sel in selector.split(", "):
                try:
                    await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                    await page.fill(sel, value)
                    logger.debug("Filled Lever field: %s", sel)
                    break
                except PlaywrightTimeout:
                    continue

    async def _upload_resume(self, page: Page, resume_path: str) -> None:
        """Upload resume to the Lever file input."""
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
        logger.warning("No file input found on Lever form")

    async def _handle_custom_questions(self, page: Page, profile: dict) -> None:
        """Answer additional questions using the dynamic answer engine."""
        # Lever uses .application-question divs with textarea/select/input
        custom_fields = await page.query_selector_all(
            ".application-question textarea, "
            ".application-question input[type='text'], "
            ".additional-fields textarea, "
            ".additional-fields input[type='text']"
        )
        for field in custom_fields:
            try:
                current = await field.input_value()
                if current.strip():
                    continue

                # Extract question text
                question = await self._get_question_text(page, field)
                if not question:
                    continue

                answer = await self._answer_engine.answer(
                    question=question,
                    jd_text="",  # Lever JD is usually on a separate page
                    resume_json=profile,
                )
                await field.fill(answer)
                logger.info("Lever custom answer for '%s': '%s'", question[:50], answer[:50])
            except Exception:
                logger.debug("Error filling Lever custom field", exc_info=True)

    @staticmethod
    async def _get_question_text(page: Page, field) -> str:
        """Extract question label text for a Lever form field."""
        field_id = await field.get_attribute("id")
        if field_id:
            label = await page.query_selector(f'label[for="{field_id}"]')
            if label:
                return (await label.inner_text()).strip()

        # Walk up to parent question container
        parent = await field.evaluate_handle(
            "el => el.closest('.application-question, .additional-fields')"
        )
        if parent:
            el = parent.as_element()
            if el:
                label = await el.query_selector("label, .application-label, legend")
                if label:
                    return (await label.inner_text()).strip()

        placeholder = await field.get_attribute("placeholder")
        return (placeholder or "").strip()

    async def _submit(self, page: Page) -> bool:
        """Click the Lever submit button and check for success."""
        for sel in _SELECTORS["submit_button"].split(", "):
            try:
                btn = await page.wait_for_selector(sel, timeout=_FIELD_TIMEOUT_MS)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info("Clicked Lever submit: %s", sel)
                    break
            except PlaywrightTimeout:
                continue
        else:
            logger.error("No Lever submit button found")
            return False

        # Check for success
        try:
            await page.wait_for_selector(
                'text=/thank you|application (has been )?submitted|we.*received/i',
                timeout=_SUBMIT_WAIT_MS,
            )
            logger.info("✅ Lever application submitted successfully")
            return True
        except PlaywrightTimeout:
            if "thank" in page.url.lower():
                return True
            logger.warning("Lever success indicator not found")
            return False
