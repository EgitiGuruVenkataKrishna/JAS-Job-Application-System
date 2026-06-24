"""Embedding Engine — Google text-embedding-004 via google-genai SDK.

Produces 768-dimensional embeddings and exposes cosine-similarity math.
Zero local ML dependencies; only numpy for vector arithmetic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from google import genai
from google.genai import types

from src.config import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_MODEL_ID = "gemini-embedding-2"
_EMBEDDING_DIM = 768


class EmbeddingEngine:
    """Generates text embeddings via Google text-embedding-004 API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        logger.info("EmbeddingEngine initialised (model=%s, dim=%d)", _MODEL_ID, _EMBEDDING_DIM)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_embedding(self, text: str) -> list[float]:
        """Return a 768-dim embedding vector for *text*.

        Retries up to ``_MAX_RETRIES`` times on transient API errors.

        Args:
            text: The input text to embed.

        Returns:
            A list of floats representing the embedding vector.

        Raises:
            RuntimeError: If all retry attempts are exhausted.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                # Truncate text to ~8000 chars to avoid token limit errors
                safe_text = text[:8000]
                response = await self._client.aio.models.embed_content(
                    model=_MODEL_ID,
                    contents=safe_text,
                    config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM)
                )
                embedding: list[float] = response.embeddings[0].values
                if len(embedding) != _EMBEDDING_DIM:
                    logger.warning(
                        "Expected %d-dim embedding, got %d",
                        _EMBEDDING_DIM,
                        len(embedding),
                    )
                return embedding

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Embedding API error (attempt %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )

        raise RuntimeError(
            f"Embedding API failed after {_MAX_RETRIES} attempts"
        ) from last_exc

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two vectors using numpy.

        Args:
            vec_a: First embedding vector.
            vec_b: Second embedding vector.

        Returns:
            Cosine similarity in the range [-1, 1].
        """
        a = np.asarray(vec_a, dtype=np.float64)
        b = np.asarray(vec_b, dtype=np.float64)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0.0 or norm_b == 0.0:
            logger.warning("Zero-norm vector encountered in cosine_similarity")
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))
