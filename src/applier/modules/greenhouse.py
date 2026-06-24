"""Greenhouse ATS module — Priority 1, most detailed auto-fill implementation."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from src.applier.dynamic_answers import DynamicAnswerEngine

logger = logging.getLogger(__name__)

# Standard Greenhouse form field selectors
_SELECTORS = {
    "first_name": "#first_name",
    "last_name": "#last_name",
    "email": "#email",
    "phone": "#phone",
    "linkedin": '[autocomplete="url"]',
    "resume_input": 'input[type="file"][id*="resume"], input[type="file"][data-field*="resume"], form input[type="file"]',
    "cover_letter_input": 'input[type="file"][id*="cover"], input[type="file"][data-field*="cover"]',
    "submit_button": 'button[type="submit"], #submit_app, input[type="submit"]',
}

# Timeout constants (milliseconds)
_FIELD_TIMEOUT_MS = 3_000
_SUBMIT_WAIT_MS = 10_000


class GreenhouseModule:
    """Auto-fills and submits Greenhouse job application forms.

    This is the most detailed ATS module (Priority 1). It handles:
        - Standard personal-info fields (name, email, phone, LinkedIn)
        - Resume and cover-letter file uploads
        - Common dropdown questions (sponsorship, work authorisation)
        - Dynamic answers for arbitrary custom text fields via Gemini
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
        """Fill out a Greenhouse application form and submit it.

        Args:
            page: Playwright page navigated to the Greenhouse application.
            user_profile: Dict with keys ``first_name``, ``last_name``,
                ``email``, ``phone``, ``linkedin_url``, etc.
            resume_path: Absolute path to the resume file.
            cover_letter_path: Optional absolute path to the cover letter.

        Returns:
            ``True`` if the application was submitted successfully.
        """
        try:
            # --- 1. Fill standard personal fields ---
            await self._fill_basic_fields(page, user_profile)

            # --- 2. Upload resume ---
            await self._upload_resume(page, resume_path)

            # --- 3. Upload cover letter (optional) ---
            if cover_letter_path and Path(cover_letter_path).exists():
                await self._upload_cover_letter(page, cover_letter_path)

            # --- 4. Fill LinkedIn URL ---
            await self._fill_linkedin(page, user_profile)

            # --- 5. Handle common dropdowns ---
            await self._handle_dropdowns(page)

            # --- 6. Handle custom text fields via dynamic answers ---
            await self._handle_custom_questions(page, user_profile)

            # --- 7. Submit the form ---
            return await self._submit(page)

        except Exception:
            logger.error("Greenhouse form fill failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fill_basic_fields(self, page: Page, profile: dict) -> None:
        """Fill first name, last name, email, and phone."""
        field_map = {
            _SELECTORS["first_name"]: profile.get("first_name", ""),
            _SELECTORS["last_name"]: profile.get("last_name", ""),
            _SELECTORS["email"]: profile.get("email", ""),
            _SELECTORS["phone"]: profile.get("phone", ""),
        }
        for selector, value in field_map.items():
            if not value:
                continue
            try:
                await page.wait_for_selector(selector, timeout=_FIELD_TIMEOUT_MS)
                await page.fill(selector, value)
                logger.debug("Filled field %s", selector)
            except PlaywrightTimeout:
                logger.debug("Field %s not found — skipping", selector)

    async def _upload_resume(self, page: Page, resume_path: str) -> None:
        """Upload the resume to the first matching file input."""
        selectors = _SELECTORS["resume_input"].split(", ")
        for selector in selectors:
            try:
                handle = await page.wait_for_selector(selector, timeout=_FIELD_TIMEOUT_MS)
                if handle:
                    await handle.set_input_files(resume_path)
                    logger.info("Resume uploaded via selector: %s", selector)
                    return
            except PlaywrightTimeout:
                continue
        logger.warning("No resume upload field found on Greenhouse form")

    async def _upload_cover_letter(self, page: Page, cover_letter_path: str) -> None:
        """Upload the cover letter to the cover-letter file input."""
        try:
            handle = await page.wait_for_selector(
                _SELECTORS["cover_letter_input"], timeout=_FIELD_TIMEOUT_MS
            )
            if handle:
                await handle.set_input_files(cover_letter_path)
                logger.info("Cover letter uploaded")
        except PlaywrightTimeout:
            # Try the second file input as fallback
            try:
                file_inputs = await page.query_selector_all('input[type="file"]')
                if len(file_inputs) >= 2:
                    await file_inputs[1].set_input_files(cover_letter_path)
                    logger.info("Cover letter uploaded via second file input")
                else:
                    logger.debug("No cover-letter upload field found")
            except Exception:
                logger.debug("Cover-letter upload fallback failed", exc_info=True)

    async def _fill_linkedin(self, page: Page, profile: dict) -> None:
        """Fill the LinkedIn URL field if present."""
        linkedin_url = profile.get("linkedin_url", "")
        if not linkedin_url:
            return
        try:
            await page.wait_for_selector(_SELECTORS["linkedin"], timeout=_FIELD_TIMEOUT_MS)
            await page.fill(_SELECTORS["linkedin"], linkedin_url)
            logger.debug("LinkedIn URL filled")
        except PlaywrightTimeout:
            logger.debug("LinkedIn field not found")

    async def _handle_dropdowns(self, page: Page) -> None:
        """Handle common Greenhouse dropdowns (sponsorship, work auth)."""
        selects = await page.query_selector_all("select")
        for select_el in selects:
            try:
                # Get the associated label text
                select_id = await select_el.get_attribute("id") or ""
                label_text = ""
                if select_id:
                    label_el = await page.query_selector(f'label[for="{select_id}"]')
                    if label_el:
                        label_text = (await label_el.inner_text()).lower()

                # Also check the select's name attribute
                name_attr = (await select_el.get_attribute("name") or "").lower()
                combined = f"{label_text} {name_attr} {select_id.lower()}"

                if "sponsor" in combined:
                    await self._select_option(select_el, "No")
                    logger.debug("Selected 'No' for sponsorship dropdown")
                elif "authorized" in combined or "authoris" in combined or "authorization" in combined:
                    await self._select_option(select_el, "Yes")
                    logger.debug("Selected 'Yes' for work authorisation dropdown")
            except Exception:
                logger.debug("Error handling dropdown", exc_info=True)

    @staticmethod
    async def _select_option(select_el, target_text: str) -> None:
        """Select an option by visible text (case-insensitive partial match)."""
        options = await select_el.query_selector_all("option")
        for option in options:
            text = (await option.inner_text()).strip()
            if text.lower() == target_text.lower():
                value = await option.get_attribute("value")
                if value is not None:
                    await select_el.select_option(value=value)
                    return
        # Fallback: partial match
        for option in options:
            text = (await option.inner_text()).strip()
            if target_text.lower() in text.lower():
                value = await option.get_attribute("value")
                if value is not None:
                    await select_el.select_option(value=value)
                    return

    async def _handle_custom_questions(self, page: Page, profile: dict) -> None:
        """Find custom text fields and generate dynamic answers."""
        # Greenhouse custom questions are typically in fieldsets or divs with labels
        text_fields = await page.query_selector_all(
            'textarea:not([id="first_name"]):not([id="last_name"]):not([id="email"]):not([id="phone"]), '
            'input[type="text"][data-custom="true"], '
            'div.field textarea'
        )
        for field in text_fields:
            try:
                current_value = await field.input_value()
                if current_value.strip():
                    continue  # Already has a value

                # Find the question text from the nearest label
                question = await self._get_question_for_field(page, field)
                if not question:
                    continue

                # Gather JD text from the page for context
                jd_text = await self._extract_jd_text(page)

                answer = await self._answer_engine.answer(
                    question=question,
                    jd_text=jd_text,
                    resume_json=profile,
                )
                await field.fill(answer)
                logger.info("Dynamic answer for '%s': '%s'", question[:50], answer[:50])
            except Exception:
                logger.debug("Error filling custom question field", exc_info=True)

    @staticmethod
    async def _get_question_for_field(page: Page, field) -> str:
        """Attempt to extract the question text for a form field."""
        # Try: label[for=id]
        field_id = await field.get_attribute("id")
        if field_id:
            label = await page.query_selector(f'label[for="{field_id}"]')
            if label:
                return (await label.inner_text()).strip()

        # Try: aria-label or placeholder
        aria = await field.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = await field.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        # Try: previous sibling label
        parent = await field.evaluate_handle("el => el.closest('.field, .form-group, fieldset')")
        if parent:
            label = await parent.as_element().query_selector("label, legend, .field-label")
            if label:
                return (await label.inner_text()).strip()

        return ""

    @staticmethod
    async def _extract_jd_text(page: Page) -> str:
        """Extract a reasonable chunk of job description text from the page."""
        try:
            jd_el = await page.query_selector(
                "#content, .job-description, .job__description, "
                '[data-automation="jobDescription"], article'
            )
            if jd_el:
                text = await jd_el.inner_text()
                return text[:3000]
        except Exception:
            pass
        return ""

    async def _submit(self, page: Page) -> bool:
        """Click the submit button and wait for a success indicator."""
        selectors = _SELECTORS["submit_button"].split(", ")
        clicked = False
        for selector in selectors:
            try:
                btn = await page.wait_for_selector(selector, timeout=_FIELD_TIMEOUT_MS)
                if btn and await btn.is_visible():
                    await btn.click()
                    clicked = True
                    logger.info("Clicked submit button: %s", selector)
                    break
            except PlaywrightTimeout:
                continue

        if not clicked:
            logger.error("No submit button found on Greenhouse form")
            return False

        # Wait for success indicator
        try:
            await page.wait_for_selector(
                'text=/thank you|application submitted|application received/i',
                timeout=_SUBMIT_WAIT_MS,
            )
            logger.info("✅ Greenhouse application submitted successfully")
            return True
        except PlaywrightTimeout:
            logger.warning("Success indicator not found after submit — may have succeeded")
            # Check if URL changed (some forms redirect)
            if "thank" in page.url.lower() or "success" in page.url.lower():
                logger.info("✅ URL indicates successful submission")
                return True
            return False
