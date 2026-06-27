"""AI Gate — Gemini 2.5 Flash structured evaluation of JD ↔ resume fit.

Performs deep eligibility assessment, match scoring, bullet-point
tailoring, GitHub project tailoring, and reasoning via a single Gemini call with JSON output.
"""

from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from src.config import get_settings

logger = logging.getLogger(__name__)

_MODEL_ID = "gemini-2.5-flash"
_MAX_RETRIES = 3

_SYSTEM_PROMPT = """\
You are an Expert Tech Recruiter AI. Your job is to evaluate whether a \
candidate's resume is a strong match for a given job description.

You will receive:
1. A **Job Description (JD)**.
2. A **Resume** in JSON format.
3. A list of available **GitHub Repositories** of the candidate.

Perform the following tasks:

1. **Eligibility Check** — The candidate is ONLY eligible for intern or \
junior-level roles. Reject if the JD requires security clearance, \
requires sponsorship that the candidate cannot provide, or is clearly \
senior/staff level.

2. **Match Score** — Score the match from 0 to 100 based on skill overlap, \
technology alignment, and experience relevance.

3. **Tailored Bullets** — If the candidate IS eligible, rewrite exactly 3 \
resume bullet points that mirror keywords and phrases from the JD. \
You must ONLY rephrase existing experience — absolutely NO fabrication \
of skills or accomplishments the candidate does not have.

4. **Tailored Projects** — If the candidate IS eligible, select the top 2-3 \
most relevant projects from the candidate's GitHub Repositories that best demonstrate \
alignment with the technologies and requirements of this specific JD. \
For each chosen project, write a JSON object containing its "name", "tech_stack", \
and 2-3 tailored "bullets" that highlight matching tech skills (strictly based on the project context, no fabrication).

5. **Reasoning** — Provide a single concise sentence explaining why the \
candidate is or is not a good fit.

Return your answer as a JSON object with these exact fields:
{
  "eligible": <bool>,
  "score": <int 0-100>,
  "tailored_bullets": [<string>, <string>, <string>],
  "reasoning": <string>,
  "rejection_reason": <string or null>,
  "tailored_projects": [
     {
       "name": <string>,
       "tech_stack": <string>,
       "bullets": [<string>, <string>]
     }
  ]
}

If the candidate is NOT eligible, set "tailored_bullets" and "tailored_projects" to an empty list \
and populate "rejection_reason". Otherwise set "rejection_reason" to null.
"""


@dataclass(frozen=True, slots=True)
class AiGateResult:
    """Outcome of an AI Gate evaluation."""

    eligible: bool
    score: int
    tailored_bullets: list[str] = field(default_factory=list)
    reasoning: str = ""
    rejection_reason: str | None = None
    tailored_projects: list[dict] = field(default_factory=list)


class AiGate:
    """Second-pass filter: LLM-powered eligibility and match assessment."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        logger.info("AiGate initialised (model=%s)", _MODEL_ID)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        jd_text: str,
        resume_json: dict,
    ) -> AiGateResult:
        """Evaluate a resume against a JD using Gemini 2.5 Flash.

        Args:
            jd_text: Raw job-description text.
            resume_json: Parsed resume as a dictionary.

        Returns:
            An :class:`AiGateResult` with eligibility, score, bullets, reasoning and projects.

        Raises:
            RuntimeError: If the Gemini API call or response parsing fails.
        """
        # Fetch GitHub repositories to inject into prompt
        from src.filtering.github_integration import GitHubIntegration
        github = GitHubIntegration()
        github_projects = await github.get_projects_for_prompt()

        user_prompt = (
            f"## Job Description\n\n{jd_text}\n\n"
            f"## Resume (JSON)\n\n```json\n{json.dumps(resume_json, indent=2)}\n```\n\n"
            f"## GitHub Repositories\n\n```json\n{json.dumps(github_projects, indent=2)}\n```"
        )

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=_MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text
                logger.debug("AiGate raw response: %s", raw_text)

                data = json.loads(raw_text)

                result = AiGateResult(
                    eligible=bool(data["eligible"]),
                    score=int(data["score"]),
                    tailored_bullets=list(data.get("tailored_bullets", [])),
                    reasoning=str(data.get("reasoning", "")),
                    rejection_reason=data.get("rejection_reason"),
                    tailored_projects=list(data.get("tailored_projects", [])),
                )

                logger.info(
                    "AiGate: eligible=%s score=%d reasoning=%s",
                    result.eligible,
                    result.score,
                    result.reasoning,
                )

                return result

            except json.JSONDecodeError as exc:
                logger.error("AiGate JSON parse error: %s | raw=%s", exc, raw_text)
                raise RuntimeError("Failed to parse Gemini JSON response") from exc

            except KeyError as exc:
                logger.error("AiGate missing field in response: %s | data=%s", exc, data)
                raise RuntimeError(f"Gemini response missing required field: {exc}") from exc

            except Exception as exc:  # noqa: BLE001
                if attempt == _MAX_RETRIES:
                    logger.error("AiGate unexpected error: %s", exc, exc_info=True)
                    raise RuntimeError("AiGate evaluation failed") from exc
                logger.warning("Gemini API error (attempt %d/%d): %s. Retrying...", attempt, _MAX_RETRIES, exc)
                await asyncio.sleep(2 ** attempt)

