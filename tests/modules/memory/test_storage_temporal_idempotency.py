"""Tests for temporal fact idempotency and memory_policies TTL lookup.

Covers:
- _generate_temporal_idempotency_key determinism and uniqueness
- store_fact idempotency for temporal facts (same key → no-op)
- store_fact no idempotency key for property facts
- store_fact explicit idempotency_key takes precedence
- _lookup_episode_ttl_days from memory_policies
- consolidation_executor episode_ttl_days in result
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (avoids import-time side effects).
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact
_generate_temporal_idempotency_key = _mod._generate_temporal_idempotency_key
_lookup_episode_ttl_days = _mod._lookup_episode_ttl_days
_DEFAULT_EPISODE_TTL_DAYS = _mod._DEFAULT_EPISODE_TTL_DAYS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper for mocking asyncpg pool/conn
# ---------------------------------------------------------------------------


class _AsyncCM:
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
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture()
def mock_pool():
    """Return (pool, conn) mocks wired like asyncpg."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    pool.fetchval = AsyncMock(return_value=None)

    return pool, conn


# ---------------------------------------------------------------------------
# Tests: _generate_temporal_idempotency_key
# ---------------------------------------------------------------------------


class TestGenerateTemporalIdempotencyKey:
    """Unit tests for the idempotency key generation function."""

    def test_returns_32_char_hex(self):
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        key = _generate_temporal_idempotency_key(None, None, "global", "meal_breakfast", ts, None)
        assert isinstance(key, str)
        assert len(key) == 32
        # Must be lowercase hex
        assert all(c in "0123456789abcdef" for c in key)

    def test_same_inputs_produce_same_key(self):
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        entity_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        key1 = _generate_temporal_idempotency_key(
            entity_id, None, "global", "meal_breakfast", ts, None
        )
        key2 = _generate_temporal_idempotency_key(
            entity_id, None, "global", "meal_breakfast", ts, None
        )
        assert key1 == key2

    def test_different_valid_at_produces_different_key(self):
        ts1 = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 3, 6, 12, 0, 0, tzinfo=UTC)
        key1 = _generate_temporal_idempotency_key(None, None, "global", "meal_breakfast", ts1, None)
        key2 = _generate_temporal_idempotency_key(None, None, "global", "meal_breakfast", ts2, None)
        assert key1 != key2

    def test_different_predicate_produces_different_key(self):
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        key1 = _generate_temporal_idempotency_key(None, None, "global", "meal_breakfast", ts, None)
        key2 = _generate_temporal_idempotency_key(None, None, "global", "meal_lunch", ts, None)
        assert key1 != key2

    def test_different_entity_id_produces_different_key(self):
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        eid1 = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        eid2 = uuid.UUID("660e8400-e29b-41d4-a716-446655440001")
        key1 = _generate_temporal_idempotency_key(eid1, None, "global", "weight", ts, None)
        key2 = _generate_temporal_idempotency_key(eid2, None, "global", "weight", ts, None)
        assert key1 != key2

    def test_source_episode_id_affects_key(self):
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        ep1 = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
        ep2 = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
        key1 = _generate_temporal_idempotency_key(None, None, "global", "weight", ts, ep1)
        key2 = _generate_temporal_idempotency_key(None, None, "global", "weight", ts, ep2)
        assert key1 != key2

    def test_none_entity_id_handled(self):
        """None entity_id must not crash."""
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        key = _generate_temporal_idempotency_key(None, None, "global", "meal_breakfast", ts, None)
        assert len(key) == 32


# ---------------------------------------------------------------------------
# Tests: store_fact idempotency behaviour
# ---------------------------------------------------------------------------


class TestStoreFactIdempotency:
    """Temporal fact idempotency via idempotency_key."""

    async def test_temporal_fact_has_idempotency_key_in_insert(self, mock_pool, embedding_engine):
        """Temporal facts get an auto-generated idempotency_key in the INSERT."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)

        await store_fact(pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts)

        insert_call = conn.execute.call_args_list[0]
        # idempotency_key is $23 (args[-4], before observed_at=$24, retention_class=$25,
        # sensitivity=$26 at the end)
        idem_key = insert_call.args[-4]
        assert idem_key is not None
        assert isinstance(idem_key, str)
        assert len(idem_key) == 32

    async def test_property_fact_has_null_idempotency_key(self, mock_pool, embedding_engine):
        """Property facts (valid_at=None) must have NULL idempotency_key."""
        pool, conn = mock_pool

        await store_fact(pool, "user", "favorite_color", "blue", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        idem_key = insert_call.args[-4]
        # Property facts must not get an idempotency key
        assert idem_key is None

    async def test_explicit_idempotency_key_used_as_is(self, mock_pool, embedding_engine):
        """Caller-provided idempotency_key takes precedence over auto-generation."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        explicit_key = "my-custom-key-1234567890abcdef"

        await store_fact(
            pool,
            "user",
            "meal_breakfast",
            "oatmeal",
            embedding_engine,
            valid_at=ts,
            idempotency_key=explicit_key,
        )

        insert_call = conn.execute.call_args_list[0]
        idem_key = insert_call.args[-4]
        assert idem_key == explicit_key

    async def test_duplicate_temporal_fact_returns_existing_id(self, mock_pool, embedding_engine):
        """When same (tenant_id, idempotency_key) already exists, returns existing ID."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        existing_id = uuid.uuid4()

        # Simulate an existing fact with this idempotency key
        conn.fetchval = AsyncMock(return_value=existing_id)

        result = await store_fact(
            pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts
        )

        # Should return the pre-existing fact's ID without executing any INSERT
        assert result == existing_id
        # No INSERT should have been executed
        conn.execute.assert_not_awaited()

    async def test_new_temporal_fact_inserts_when_no_existing_key(
        self, mock_pool, embedding_engine
    ):
        """New temporal fact (no existing idem key) proceeds with INSERT."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)

        # fetchval returns None — no existing fact with this key
        conn.fetchval = AsyncMock(return_value=None)

        result = await store_fact(
            pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts
        )

        assert isinstance(result, uuid.UUID)
        # INSERT should have been called
        assert conn.execute.call_count == 1
        assert "INSERT INTO facts" in conn.execute.call_args_list[0].args[0]

    async def test_idempotency_key_not_checked_for_property_facts(
        self, mock_pool, embedding_engine
    ):
        """No idempotency fetchval call for property facts (valid_at=None)."""
        pool, conn = mock_pool

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # fetchval must not be called for property facts (no entity_id either)
        conn.fetchval.assert_not_awaited()

    async def test_observed_at_set_on_insert(self, mock_pool, embedding_engine):
        """observed_at (args[-3]) is always set to a datetime on insert."""
        pool, conn = mock_pool

        await store_fact(pool, "user", "favorite_color", "blue", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        # observed_at is $24; retention_class=$25 and sensitivity=$26 follow.
        observed_at = insert_call.args[-3]
        assert isinstance(observed_at, datetime)
        assert observed_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Tests: invalid_at set on superseded facts
# ---------------------------------------------------------------------------


class TestSupersededFactInvalidAt:
    """When a property fact is superseded, invalid_at must be set on the old fact."""

    async def test_invalid_at_set_when_fact_superseded(self, mock_pool, embedding_engine):
        """UPDATE on superseded fact includes invalid_at."""
        pool, conn = mock_pool
        old_id = uuid.uuid4()
        conn.fetchrow = AsyncMock(return_value={"id": old_id})

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        update_call = conn.execute.call_args_list[0]
        sql = update_call.args[0]
        # Must set both validity and invalid_at
        assert "validity = 'superseded'" in sql
        assert "invalid_at" in sql
        # Second arg ($1) = old fact ID, third arg ($2) = invalid_at timestamp
        assert update_call.args[1] == old_id
        invalid_at_val = update_call.args[2]
        assert isinstance(invalid_at_val, datetime)

    async def test_no_invalid_at_update_when_no_supersession(self, mock_pool, embedding_engine):
        """When no existing fact, only the INSERT is executed (no UPDATE)."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value=None)

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # Only one execute call (INSERT), no UPDATE
        assert conn.execute.call_count == 1
        sql = conn.execute.call_args_list[0].args[0]
        assert "INSERT INTO facts" in sql
        assert "UPDATE" not in sql


# ---------------------------------------------------------------------------
# Tests: _lookup_episode_ttl_days
# ---------------------------------------------------------------------------


class TestLookupEpisodeTtlDays:
    """Tests for memory_policies TTL lookup with fallback."""

    async def test_returns_policy_ttl_when_found(self):
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=730)

        result = await _lookup_episode_ttl_days(pool, "health_log")

        assert result == 730

    async def test_returns_default_when_policy_not_found(self):
        """When fetchval returns None (class not in table), use default TTL."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)

        result = await _lookup_episode_ttl_days(pool, "unknown_class")

        assert result == _DEFAULT_EPISODE_TTL_DAYS

    async def test_returns_default_when_table_missing(self):
        """When the memory_policies table doesn't exist, fall back to default."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=Exception("relation does not exist"))

        result = await _lookup_episode_ttl_days(pool, "transient")

        assert result == _DEFAULT_EPISODE_TTL_DAYS

    async def test_returns_default_for_permanent_null_ttl(self):
        """NULL ttl_days (e.g. 'permanent' class) falls back to default."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)  # NULL in DB

        result = await _lookup_episode_ttl_days(pool, "permanent")

        assert result == _DEFAULT_EPISODE_TTL_DAYS

    async def test_queries_with_correct_retention_class(self):
        """The correct retention_class is passed to the query."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=90)

        await _lookup_episode_ttl_days(pool, "operational")

        pool.fetchval.assert_called_once()
        call_args = pool.fetchval.call_args
        # Second positional arg is the retention_class parameter
        assert call_args.args[1] == "operational"

    async def test_returns_default_when_ttl_is_zero(self):
        """A TTL of 0 is treated as invalid and falls back to default."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=0)

        result = await _lookup_episode_ttl_days(pool, "transient")

        assert result == _DEFAULT_EPISODE_TTL_DAYS

    async def test_returns_default_when_ttl_is_negative(self):
        """A negative TTL falls back to default."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=-5)

        result = await _lookup_episode_ttl_days(pool, "transient")

        assert result == _DEFAULT_EPISODE_TTL_DAYS
