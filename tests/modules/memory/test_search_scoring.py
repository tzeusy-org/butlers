"""Unit tests for composite scoring functions in the Memory butler search module."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"


def _load_search_module():
    """Load search.py from disk."""
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules so @dataclass can resolve __module__
    sys.modules["search"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
CompositeWeights = _mod.CompositeWeights
compute_recency_score = _mod.compute_recency_score
compute_composite_score = _mod.compute_composite_score
_DEFAULT_WEIGHTS = _mod._DEFAULT_WEIGHTS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# CompositeWeights tests
# ---------------------------------------------------------------------------


class TestCompositeWeights:
    """Tests for the CompositeWeights dataclass."""

    def test_default_weights_sum_to_one(self):
        w = CompositeWeights()
        total = w.relevance + w.importance + w.recency + w.confidence
        assert total == pytest.approx(1.0)

    def test_default_weight_values(self):
        w = CompositeWeights()
        assert w.relevance == 0.4
        assert w.importance == 0.3
        assert w.recency == 0.2
        assert w.confidence == 0.1

    def test_custom_weights(self):
        w = CompositeWeights(relevance=0.5, importance=0.2, recency=0.2, confidence=0.1)
        assert w.relevance == 0.5
        assert w.importance == 0.2


# ---------------------------------------------------------------------------
# compute_recency_score tests
# ---------------------------------------------------------------------------


class TestComputeRecencyScore:
    """Tests for compute_recency_score()."""

    def test_none_returns_zero(self):
        assert compute_recency_score(None) == 0.0

    def test_recent_reference_near_one(self):
        """A reference less than 1 hour ago should score very close to 1.0."""
        recent = datetime.now(UTC) - timedelta(minutes=30)
        score = compute_recency_score(recent)
        assert score > 0.99

    def test_old_reference_near_zero(self):
        """A reference > 30 days ago should score near 0.0 (with 7-day half-life)."""
        old = datetime.now(UTC) - timedelta(days=60)
        score = compute_recency_score(old)
        assert score < 0.01

    def test_decay_is_exponential(self):
        """Score at 2*half_life should be ~0.25 of score at t=0."""
        now = datetime.now(UTC)
        half_life = 7.0

        score_at_0 = compute_recency_score(now, half_life_days=half_life)
        score_at_1hl = compute_recency_score(
            now - timedelta(days=half_life), half_life_days=half_life
        )
        score_at_2hl = compute_recency_score(
            now - timedelta(days=2 * half_life), half_life_days=half_life
        )

        # At 1 half-life, score should be ~0.5
        assert score_at_1hl == pytest.approx(0.5, abs=0.02)
        # At 2 half-lives, score should be ~0.25
        assert score_at_2hl == pytest.approx(0.25, abs=0.02)
        # Monotonically decreasing
        assert score_at_0 > score_at_1hl > score_at_2hl

    def test_score_clamped_to_unit_interval(self):
        """Score should always be in [0.0, 1.0]."""
        # Just now
        score_now = compute_recency_score(datetime.now(UTC))
        assert 0.0 <= score_now <= 1.0

        # Very old
        score_old = compute_recency_score(datetime.now(UTC) - timedelta(days=365))
        assert 0.0 <= score_old <= 1.0

    def test_custom_half_life(self):
        """Custom half-life of 1 day should decay much faster."""
        ref = datetime.now(UTC) - timedelta(days=1)
        score = compute_recency_score(ref, half_life_days=1.0)
        assert score == pytest.approx(0.5, abs=0.02)


# ---------------------------------------------------------------------------
# compute_composite_score tests
# ---------------------------------------------------------------------------


class TestComputeCompositeScore:
    """Tests for compute_composite_score()."""

    def test_perfect_scores_give_one(self):
        """All max inputs with default weights should yield 1.0."""
        score = compute_composite_score(
            relevance=1.0,
            importance=10.0,
            recency=1.0,
            effective_confidence=1.0,
        )
        assert score == pytest.approx(1.0)

    def test_zero_scores_give_zero(self):
        """All zero inputs should yield 0.0."""
        score = compute_composite_score(
            relevance=0.0,
            importance=0.0,
            recency=0.0,
            effective_confidence=0.0,
        )
        assert score == pytest.approx(0.0)

    def test_custom_weights_used(self):
        """Custom weights should override defaults."""
        # Only relevance matters
        w = CompositeWeights(relevance=1.0, importance=0.0, recency=0.0, confidence=0.0)
        score = compute_composite_score(
            relevance=0.8,
            importance=10.0,
            recency=1.0,
            effective_confidence=1.0,
            weights=w,
        )
        assert score == pytest.approx(0.8)

    def test_importance_normalized_from_0_to_10(self):
        """Importance of 5 (out of 10) should contribute 0.5 * weight."""
        w = CompositeWeights(relevance=0.0, importance=1.0, recency=0.0, confidence=0.0)
        score = compute_composite_score(
            relevance=0.0,
            importance=5.0,
            recency=0.0,
            effective_confidence=0.0,
            weights=w,
        )
        assert score == pytest.approx(0.5)

    def test_importance_max_normalized_to_one(self):
        """Importance of 10 should normalize to 1.0."""
        w = CompositeWeights(relevance=0.0, importance=1.0, recency=0.0, confidence=0.0)
        score = compute_composite_score(
            relevance=0.0,
            importance=10.0,
            recency=0.0,
            effective_confidence=0.0,
            weights=w,
        )
        assert score == pytest.approx(1.0)

    def test_mixed_values(self):
        """Composite score with mixed realistic values."""
        score = compute_composite_score(
            relevance=0.85,
            importance=7.0,
            recency=0.6,
            effective_confidence=0.9,
        )
        # 0.4*0.85 + 0.3*(7/10) + 0.2*0.6 + 0.1*0.9
        expected = 0.4 * 0.85 + 0.3 * 0.7 + 0.2 * 0.6 + 0.1 * 0.9
        assert score == pytest.approx(expected)

    def test_default_weights_when_none(self):
        """Passing weights=None should use default weights."""
        score_default = compute_composite_score(
            relevance=0.5,
            importance=5.0,
            recency=0.5,
            effective_confidence=0.5,
            weights=None,
        )
        score_explicit = compute_composite_score(
            relevance=0.5,
            importance=5.0,
            recency=0.5,
            effective_confidence=0.5,
            weights=CompositeWeights(),
        )
        assert score_default == pytest.approx(score_explicit)
