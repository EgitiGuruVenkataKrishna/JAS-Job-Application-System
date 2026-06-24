"""Math Gate — cosine-similarity filter for JD ↔ resume matching.

Compares the JD embedding against a pre-computed resume embedding.
Passes only if the similarity score meets or exceeds the configurable
threshold (default 0.77).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import get_settings
from src.filtering.embedding_engine import EmbeddingEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MathGateResult:
    """Outcome of a Math Gate evaluation."""

    passed: bool
    score: float
    jd_embedding: list[float]


class MathGate:
    """First-pass filter: cosine similarity between JD and resume embeddings."""

    def __init__(
        self,
        embedding_engine: EmbeddingEngine,
        threshold: float | None = None,
    ) -> None:
        """Initialise with an embedding engine and optional threshold.

        Args:
            embedding_engine: Engine used to embed the JD text.
            threshold: Minimum cosine similarity to pass.
                       Falls back to ``settings.cosine_threshold`` (0.77).
        """
        self._engine = embedding_engine
        self._threshold = threshold if threshold is not None else get_settings().cosine_threshold
        logger.info("MathGate initialised (threshold=%.4f)", self._threshold)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        jd_text: str,
        resume_embedding: list[float],
    ) -> MathGateResult:
        """Score a JD against a resume embedding.

        Args:
            jd_text: Raw job-description text.
            resume_embedding: Pre-computed 768-dim resume embedding.

        Returns:
            A :class:`MathGateResult` with pass/fail, score, and JD embedding.
        """
        jd_embedding = await self._engine.get_embedding(jd_text)

        score = EmbeddingEngine.cosine_similarity(jd_embedding, resume_embedding)
        passed = score >= self._threshold

        logger.info(
            "MathGate: score=%.4f threshold=%.4f passed=%s",
            score,
            self._threshold,
            passed,
        )

        return MathGateResult(
            passed=passed,
            score=score,
            jd_embedding=jd_embedding,
        )
