"""Tests for permanence validation in the Memory butler storage module."""

from __future__ import annotations

import importlib.util
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# Mock sentence_transformers before importing storage.
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
validate_permanence = _mod.validate_permanence
store_fact = _mod.store_fact
_PERMANENCE_DECAY = _mod._PERMANENCE_DECAY

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper for mocking asyncpg pool/conn
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_engine():
    """Return a mock EmbeddingEngine that produces a deterministic vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture()
def mock_pool():
    """Return (pool, conn) mocks wired up like asyncpg."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    return pool, conn


# ---------------------------------------------------------------------------
# Tests — validate_permanence
# ---------------------------------------------------------------------------


class TestValidatePermanence:
    """validate_permanence() returns correct decay rates and rejects invalids."""

    @pytest.mark.parametrize(
        ("permanence", "expected_decay"),
        [
            ("permanent", 0.0),
            ("stable", 0.002),
            ("standard", 0.008),
            ("volatile", 0.03),
            ("ephemeral", 0.1),
        ],
    )
    def test_valid_permanence_returns_decay_rate(self, permanence, expected_decay):
        assert validate_permanence(permanence) == expected_decay

    def test_all_known_permanence_levels_covered(self):
        """Ensure our parametrize list matches the actual dict keys."""
        expected_keys = {"permanent", "stable", "standard", "volatile", "ephemeral"}
        assert set(_PERMANENCE_DECAY.keys()) == expected_keys

    @pytest.mark.parametrize(
        "bad_value",
        [
            "invented",
            "PERMANENT",
            "Stable",
            "",
            "none",
            "temporary",
        ],
    )
    def test_invalid_permanence_raises_value_error(self, bad_value):
        with pytest.raises(ValueError, match="Invalid permanence"):
            validate_permanence(bad_value)

    def test_error_message_lists_valid_values(self):
        with pytest.raises(ValueError, match=r"\['ephemeral', 'permanent'"):
            validate_permanence("bogus")


# ---------------------------------------------------------------------------
# Tests — store_fact rejects invalid permanence
# ---------------------------------------------------------------------------


class TestStoreFactPermanenceValidation:
    """store_fact() raises ValueError for invalid permanence values."""

    async def test_store_fact_rejects_invalid_permanence(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        with pytest.raises(ValueError, match="Invalid permanence"):
            await store_fact(pool, "user", "data", "value", embedding_engine, permanence="invented")

    async def test_store_fact_accepts_valid_permanence(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        # Should not raise for any valid permanence
        for perm in _PERMANENCE_DECAY:
            result = await store_fact(
                pool, "user", "data", "value", embedding_engine, permanence=perm
            )
            assert result is not None
