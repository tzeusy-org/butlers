"""Unit tests for effective_confidence in the Memory butler search module."""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = Path(__file__).resolve().parent.parent / "search.py"


def _load_search_module():
    """Load search.py from disk."""
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["search"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
effective_confidence = _mod.effective_confidence

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# effective_confidence tests
# ---------------------------------------------------------------------------


class TestEffectiveConfidence:
    """Tests for the effective_confidence decay function."""

    def test_zero_decay_rate_returns_confidence_unchanged(self):
        """Permanent memories (decay_rate=0) keep their full confidence."""
        ts = datetime.now(UTC) - timedelta(days=365)
        assert effective_confidence(0.9, 0.0, ts) == 0.9

    def test_none_last_confirmed_returns_zero(self):
        """If last_confirmed_at is None, effective confidence is 0."""
        assert effective_confidence(0.9, 0.01, None) == 0.0

    def test_recent_confirmation_returns_near_confidence(self):
        """A confirmation seconds ago should barely decay."""
        ts = datetime.now(UTC) - timedelta(seconds=10)
        result = effective_confidence(0.8, 0.01, ts)
        assert result == pytest.approx(0.8, abs=0.001)

    def test_old_confirmation_returns_low_value(self):
        """After many days, confidence should be significantly reduced."""
        ts = datetime.now(UTC) - timedelta(days=100)
        decay_rate = 0.01
        result = effective_confidence(1.0, decay_rate, ts)
        expected = math.exp(-decay_rate * 100)
        assert result == pytest.approx(expected, rel=0.01)

    def test_very_old_confirmation_returns_near_zero(self):
        """After a very long time, confidence approaches zero."""
        ts = datetime.now(UTC) - timedelta(days=1000)
        result = effective_confidence(1.0, 0.01, ts)
        assert result < 0.001

    def test_higher_decay_rate_decays_faster(self):
        """A higher decay_rate should produce lower confidence at the same age."""
        ts = datetime.now(UTC) - timedelta(days=30)
        slow = effective_confidence(1.0, 0.01, ts)
        fast = effective_confidence(1.0, 0.1, ts)
        assert fast < slow

    def test_zero_confidence_stays_zero(self):
        """Zero base confidence stays zero regardless of decay."""
        ts = datetime.now(UTC) - timedelta(days=1)
        assert effective_confidence(0.0, 0.01, ts) == 0.0

    def test_confidence_one_decays_to_exp(self):
        """With confidence=1.0, result should be exp(-rate * days)."""
        days = 50
        rate = 0.02
        ts = datetime.now(UTC) - timedelta(days=days)
        result = effective_confidence(1.0, rate, ts)
        expected = math.exp(-rate * days)
        assert result == pytest.approx(expected, rel=0.01)

    def test_exponential_decay_half_life(self):
        """Verify the half-life relationship: t_half = ln(2) / decay_rate."""
        decay_rate = 0.1
        half_life_days = math.log(2) / decay_rate
        ts = datetime.now(UTC) - timedelta(days=half_life_days)
        result = effective_confidence(1.0, decay_rate, ts)
        assert result == pytest.approx(0.5, rel=0.01)

    def test_future_last_confirmed_at_returns_full_confidence(self):
        """A future timestamp (edge case) should not increase confidence.

        With negative elapsed time clamped to 0 days, the result equals
        the base confidence.
        """
        ts = datetime.now(UTC) + timedelta(hours=1)
        result = effective_confidence(0.7, 0.05, ts)
        assert result == pytest.approx(0.7, abs=0.001)
