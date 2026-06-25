"""Tests for Math Gate (Cosine Similarity Filtering)."""

import numpy as np
import pytest

from src.filtering.embedding_engine import EmbeddingEngine
from src.filtering.math_gate import MathGate


class MockEmbeddingEngine:
    async def get_embedding(self, text: str) -> list[float]:
        # Return dummy embedding based on length
        return [float(len(text))] * 768


@pytest.mark.asyncio
async def test_cosine_similarity():
    """Test pure numpy cosine similarity."""
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    vec_c = [0.0, 1.0, 0.0]

    score_ab = EmbeddingEngine.cosine_similarity(vec_a, vec_b)
    score_ac = EmbeddingEngine.cosine_similarity(vec_a, vec_c)

    assert np.isclose(score_ab, 1.0)
    assert np.isclose(score_ac, 0.0)


@pytest.mark.asyncio
async def test_math_gate():
    """Test MathGate logic."""
    engine = MockEmbeddingEngine()
    gate = MathGate(embedding_engine=engine, threshold=0.77)

    # Same vector should pass
    resume_emb = [1.0] * 768
    result = await gate.evaluate("dummy jd text", resume_emb)

    # Due to mock returning [13]*768, cosine similarity between [1]*768 and [13]*768 is 1.0
    assert result.passed is True
    assert np.isclose(result.score, 1.0)
