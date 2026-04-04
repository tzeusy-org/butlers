"""Behavioral tests for preference MCP tools (set_preference, get_preferences).

Tests exercise behavior through the public tool interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.preferences import (
    _derive_scope,
    _resolve_owner,
    get_preferences,
    set_preference,
)

pytestmark = pytest.mark.unit

OWNER_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OWNER_STR = str(OWNER_UUID)
OWNER_NAME = "Alice"
FACT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
FACT_STR = str(FACT_UUID)
NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def mock_engine() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# _derive_scope
# ---------------------------------------------------------------------------


class TestDeriveScope:
    def test_domain_scope_mapping(self) -> None:
        assert _derive_scope("preferences:travel_flight_seat") == "travel"
        assert _derive_scope("preferences:general_language") == "global"


# ---------------------------------------------------------------------------
# _resolve_owner
# ---------------------------------------------------------------------------


class TestResolveOwner:
    async def test_resolves_from_contacts(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(
            side_effect=[{"id": OWNER_UUID, "canonical_name": OWNER_NAME}, None]
        )
        eid, name = await _resolve_owner(pool)
        assert eid == OWNER_UUID and name == OWNER_NAME

    async def test_raises_when_no_owner_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Owner entity could not be resolved"):
            await _resolve_owner(pool)


# ---------------------------------------------------------------------------
# set_preference
# ---------------------------------------------------------------------------


@pytest.fixture()
def patch_embedding(mock_engine: MagicMock):
    with patch(
        "butlers.modules.memory.tools.preferences.get_embedding_engine",
        return_value=mock_engine,
    ):
        yield


@pytest.fixture()
def mock_owner():
    with patch(
        "butlers.modules.memory.tools.preferences._resolve_owner",
        new_callable=AsyncMock,
        return_value=(OWNER_UUID, OWNER_NAME),
    ) as m:
        yield m


@pytest.fixture()
def mock_store(tmp_path):
    from butlers.modules.memory.tools import _helpers

    with patch.object(
        _helpers._storage,
        "store_fact",
        new_callable=AsyncMock,
        return_value={"id": FACT_UUID, "supersedes_id": None},
    ) as m:
        yield m


class TestSetPreference:
    async def test_basic_storage_returns_expected_shape(
        self, pool, patch_embedding, mock_owner, mock_store
    ) -> None:
        result = await set_preference(pool, "preferences:travel_flight_seat", "window")
        assert result["id"] == FACT_STR
        assert result["scope"] == "travel"
        assert result["action"] == "created"
        assert result["superseded_id"] is None

    async def test_supersession_indicated_as_updated(
        self, pool, patch_embedding, mock_owner
    ) -> None:
        from butlers.modules.memory.tools import _helpers

        sup_id = uuid.uuid4()
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID, "supersedes_id": sup_id},
        ):
            result = await set_preference(pool, "preferences:travel_flight_seat", "aisle")
        assert result["action"] == "updated"
        assert result["superseded_id"] == str(sup_id)

    @pytest.mark.parametrize("bad_predicate", ["travel_flight_seat", "preferences:nodomain"])
    async def test_invalid_predicate_raises(self, pool: AsyncMock, bad_predicate: str) -> None:
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(pool, bad_predicate, "value")


# ---------------------------------------------------------------------------
# get_preferences
# ---------------------------------------------------------------------------


class TestGetPreferences:
    def _row(
        self,
        predicate: str = "preferences:travel_flight_seat",
        content: str = "window",
        scope: str = "travel",
        confidence: float = 1.0,
        decay_rate: float = 0.0,
    ) -> dict:
        return {
            "predicate": predicate,
            "value": content,
            "scope": scope,
            "importance": 8.0,
            "permanence": "stable",
            "updated_at": NOW,
            "confidence": confidence,
            "decay_rate": decay_rate,
            "last_confirmed_at": NOW,
        }

    async def test_returns_empty_when_no_rows(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value={"id": OWNER_UUID, "canonical_name": OWNER_NAME})
        pool.fetch = AsyncMock(return_value=[])
        assert await get_preferences(pool) == []

    async def test_result_shape(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value={"id": OWNER_UUID, "canonical_name": OWNER_NAME})
        pool.fetch = AsyncMock(return_value=[self._row()])
        results = await get_preferences(pool)
        assert len(results) == 1
        assert set(results[0].keys()) == {
            "predicate",
            "value",
            "scope",
            "importance",
            "permanence",
            "updated_at",
            "effective_confidence",
        }

    async def test_effective_confidence_decays(self, pool: AsyncMock) -> None:
        old = datetime.now(UTC) - timedelta(days=100)
        row = self._row(confidence=1.0, decay_rate=0.008)
        row["last_confirmed_at"] = old
        pool.fetchrow = AsyncMock(return_value={"id": OWNER_UUID, "canonical_name": OWNER_NAME})
        pool.fetch = AsyncMock(return_value=[row])
        results = await get_preferences(pool)
        assert results[0]["effective_confidence"] < 1.0

    async def test_no_decay_when_rate_zero(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value={"id": OWNER_UUID, "canonical_name": OWNER_NAME})
        pool.fetch = AsyncMock(return_value=[self._row(confidence=0.9, decay_rate=0.0)])
        results = await get_preferences(pool)
        assert results[0]["effective_confidence"] == 0.9
