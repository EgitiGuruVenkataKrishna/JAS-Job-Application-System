"""Dynamic answer generation — uses Google Gemini to answer unexpected application questions."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any

from google import genai

from src.config import get_settings

logger = logging.getLogger(__name__)

# Similarity threshold for fuzzy cache matching
_CACHE_SIMILARITY_THRESHOLD = 0.80

# Pre-cached answers for extremely common application questions
_DEFAULT_ANSWERS: dict[str, str] = {
    "are you legally authorized to work in the united states": "Yes",
    "will you now or in the future require sponsorship": "No",
    "do you now or will you in the future require immigration sponsorship": "No",
    "are you at least 18 years of age": "Yes",
    "are you willing to undergo a background check": "Yes",
    "how did you hear about this position": "Online job board",
    "do you have a valid driver's license": "Yes",
    "are you willing to relocate": "Yes",
    "what is your desired salary": "Open to discussion based on total compensation package",
    "when can you start": "Within two weeks of an offer",
}


class DynamicAnswerEngine:
    """Generates concise, contextual answers for custom application questions.

    Uses Google Gemini as the LLM backend and maintains an in-memory cache
    to avoid redundant API calls for similar questions across applications.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._cache: dict[str, str] = {**_DEFAULT_ANSWERS}
        logger.debug("DynamicAnswerEngine initialised with %d pre-cached answers", len(self._cache))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def answer(
        self,
        question: str,
        jd_text: str,
        resume_json: dict[str, Any],
    ) -> str:
        """Return a concise answer to an application *question*.

        Resolution order:
            1. Exact / fuzzy cache hit → return immediately.
            2. Call Gemini to generate a tailored answer.
            3. Cache the result and return.

        Args:
            question: The question text scraped from the form.
            jd_text: Full job description text for context.
            resume_json: Parsed résumé data (name, skills, experience, etc.).

        Returns:
            A short, genuine answer string (≤ 150 words).
        """
        normalised = question.strip().lower().rstrip("?").strip()

        # --- 1. Cache lookup (exact then fuzzy) ---
        cached = self._lookup_cache(normalised)
        if cached is not None:
            logger.debug("Cache hit for question: '%s'", question[:60])
            return cached

        # --- 2. Generate via Gemini ---
        logger.info("Generating dynamic answer for: '%s'", question[:80])
        answer_text = await self._generate(question, jd_text, resume_json)

        # --- 3. Cache and return ---
        self._cache[normalised] = answer_text
        return answer_text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookup_cache(self, normalised_question: str) -> str | None:
        """Exact match first, then fuzzy match above threshold."""
        if normalised_question in self._cache:
            return self._cache[normalised_question]

        best_ratio = 0.0
        best_answer: str | None = None
        for key, value in self._cache.items():
            ratio = SequenceMatcher(None, normalised_question, key).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_answer = value
        if best_ratio >= _CACHE_SIMILARITY_THRESHOLD and best_answer is not None:
            return best_answer
        return None

    async def _generate(
        self,
        question: str,
        jd_text: str,
        resume_json: dict[str, Any],
    ) -> str:
        """Call Gemini to produce a tailored answer."""
        prompt = (
            "You are helping a job applicant fill out an online application form.\n\n"
            f"## Job Description\n{jd_text[:3000]}\n\n"
            f"## Candidate Resume (JSON)\n{self._truncate_resume(resume_json)}\n\n"
            f"## Question\n{question}\n\n"
            "## Instructions\n"
            "Write a concise, genuine answer to the question above. "
            "Keep it under 150 words. Be specific to this candidate and role, not generic. "
            "If the question is a simple yes/no, answer with just 'Yes' or 'No'. "
            "Do NOT include any preamble — output only the answer text."
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            answer = (response.text or "").strip()
            if not answer:
                logger.warning("Gemini returned empty answer for: '%s'", question[:60])
                return "N/A"
            return answer
        except Exception:
            logger.error("Gemini API call failed for dynamic answer", exc_info=True)
            return "N/A"

    @staticmethod
    def _truncate_resume(resume_json: dict[str, Any], max_chars: int = 2000) -> str:
        """Serialise resume JSON and truncate to stay within prompt limits."""
        import json

        text = json.dumps(resume_json, indent=2, default=str)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text
