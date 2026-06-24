"""Cover letter generator — uses Google Gemini to produce tailored cover letters."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from google import genai

from src.config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_INSTRUCTION = (
    "You are an expert career coach and professional writer. "
    "Produce concise, compelling cover letters that highlight directly "
    "relevant experience and avoid generic filler."
)

_PROMPT_TEMPLATE = (
    "Write a concise, professional cover letter for {title} at {company}. "
    "Reference specific details from the JD. "
    "Highlight relevant experience from the resume. "
    "Keep it under 300 words. Do not be generic.\n\n"
    "--- JOB DESCRIPTION ---\n{jd_text}\n\n"
    "--- RESUME ---\n{resume_text}\n"
)


class CoverLetterGenerator:
    """Generates tailored cover letters via Google Gemini (gemini-2.5-flash).

    Only invoked when ``llm_score >= 90`` — that gate is enforced by the
    caller, not within this class.
    """

    def __init__(self) -> None:
        """Create a Gemini client using the configured API key."""
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._cover_letters_dir: Path = settings.cover_letters_dir
        logger.info(
            "CoverLetterGenerator initialised — output: %s",
            self._cover_letters_dir,
        )

    # ── public API ─────────────────────────────────────────────────────────

    async def generate(
        self,
        jd_text: str,
        resume_json: dict,
        company: str,
        title: str,
    ) -> Path:
        """Generate a cover letter and persist it to disk.

        Args:
            jd_text: Full text of the job description.
            resume_json: Structured resume data (dict).
            company: Target company name.
            title: Target job title.

        Returns:
            Path to the saved cover letter text file.

        Raises:
            RuntimeError: If the Gemini API call fails.
        """
        resume_text = self._flatten_resume(resume_json)

        prompt = _PROMPT_TEMPLATE.format(
            title=title,
            company=company,
            jd_text=jd_text,
            resume_text=resume_text,
        )

        logger.info(
            "Generating cover letter for %s at %s via Gemini …", title, company
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    temperature=0.7,
                    max_output_tokens=1024,
                ),
            )
            cover_letter_text: str = response.text
        except Exception as exc:
            logger.error(
                "Gemini API call failed for %s at %s: %s",
                title,
                company,
                exc,
            )
            raise RuntimeError(
                f"Cover letter generation failed for {title} at {company}"
            ) from exc

        if not cover_letter_text or not cover_letter_text.strip():
            raise RuntimeError(
                "Gemini returned an empty cover letter — cannot proceed."
            )

        # Persist to disk
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_company = re.sub(r"[^\w\-]", "_", company)
        dest = self._cover_letters_dir / f"{safe_company}_{timestamp}.txt"
        dest.write_text(cover_letter_text.strip(), encoding="utf-8")

        logger.info("Cover letter saved → %s", dest)
        return dest

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_resume(resume_json: dict) -> str:
        """Convert structured resume JSON into readable plain text for the prompt."""
        parts: list[str] = []

        # Contact
        if name := resume_json.get("name"):
            parts.append(f"Name: {name}")
        if email := resume_json.get("email"):
            parts.append(f"Email: {email}")

        # Education
        for edu in resume_json.get("education", []):
            parts.append(
                f"Education: {edu.get('degree', '')} — "
                f"{edu.get('institution', '')} ({edu.get('dates', '')})"
            )

        # Experience
        for job in resume_json.get("experience", []):
            header = f"{job.get('title', '')} at {job.get('company', '')} ({job.get('dates', '')})"
            bullets = "\n".join(
                f"  • {b}" for b in job.get("bullets", [])
            )
            parts.append(f"Experience: {header}\n{bullets}")

        # Projects
        for proj in resume_json.get("projects", []):
            parts.append(
                f"Project: {proj.get('name', '')} "
                f"[{proj.get('tech_stack', '')}] — {proj.get('description', '')}"
            )

        # Skills
        skills = resume_json.get("skills", {})
        if skills:
            skill_lines = ", ".join(
                f"{cat}: {', '.join(items)}"
                for cat, items in skills.items()
                if isinstance(items, list)
            )
            parts.append(f"Skills: {skill_lines}")

        return "\n".join(parts)
