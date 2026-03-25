"""Tests for the set_preference and get_preferences MCP tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.preferences import (
    PREFERENCE_IMPORTANCE_DEFAULT,
    PREFERENCE_PERMANENCE_DEFAULT,
    PREFERENCE_PREDICATE_PREFIX,
    PREFERENCE_RETENTION_CLASS,
    _derive_scope,
    _resolve_owner,
    get_preferences,
    set_preference,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants for tests
# ---------------------------------------------------------------------------

OWNER_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OWNER_UUID_STR = str(OWNER_UUID)
OWNER_NAME = "Alice"
FACT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
FACT_UUID_STR = str(FACT_UUID)
SUPERSEDED_UUID = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
SUPERSEDED_UUID_STR = str(SUPERSEDED_UUID)
NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    return AsyncMock()


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    """Return a MagicMock EmbeddingEngine."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests — _derive_scope
# ---------------------------------------------------------------------------


class TestDeriveScope:
    """Tests for _derive_scope() helper."""

    def test_travel_domain_returns_travel(self) -> None:
        assert _derive_scope("preferences:travel_flight_seat") == "travel"

    def test_health_domain_returns_health(self) -> None:
        assert _derive_scope("preferences:health_dietary_restriction") == "health"

    def test_finance_domain_returns_finance(self) -> None:
        assert _derive_scope("preferences:finance_currency") == "finance"

    def test_relationship_domain_returns_relationship(self) -> None:
        assert _derive_scope("preferences:relationship_communication_style") == "relationship"

    def test_home_domain_returns_home(self) -> None:
        assert _derive_scope("preferences:home_temperature_unit") == "home"

    def test_general_domain_returns_global(self) -> None:
        assert _derive_scope("preferences:general_language") == "global"

    def test_general_communication_style_returns_global(self) -> None:
        assert _derive_scope("preferences:general_communication_style") == "global"


# ---------------------------------------------------------------------------
# Tests — _resolve_owner
# ---------------------------------------------------------------------------


class TestResolveOwner:
    """Tests for _resolve_owner() helper."""

    async def test_resolves_from_contacts_join(self, mock_pool: AsyncMock) -> None:
        """_resolve_owner uses contacts JOIN when entity_id exists."""
        mock_pool.fetchrow = AsyncMock(
            side_effect=[{"id": OWNER_UUID, "canonical_name": OWNER_NAME}, None]
        )
        result_id, result_name = await _resolve_owner(mock_pool)
        assert result_id == OWNER_UUID
        assert result_name == OWNER_NAME

    async def test_falls_back_to_entities_roles(self, mock_pool: AsyncMock) -> None:
        """Falls back to shared.entities when contacts query returns None."""
        mock_pool.fetchrow = AsyncMock(
            side_effect=[None, {"id": OWNER_UUID, "canonical_name": OWNER_NAME}]
        )
        result_id, result_name = await _resolve_owner(mock_pool)
        assert result_id == OWNER_UUID
        assert result_name == OWNER_NAME

    async def test_raises_when_no_owner_found(self, mock_pool: AsyncMock) -> None:
        """Raises ValueError when neither query returns a row."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Owner entity could not be resolved"):
            await _resolve_owner(mock_pool)

    async def test_error_message_includes_recovery_hint(self, mock_pool: AsyncMock) -> None:
        """Error message mentions butler startup and owner contact creation."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await _resolve_owner(mock_pool)
        assert "butler" in str(exc_info.value).lower() or "startup" in str(exc_info.value).lower()

    async def test_primary_path_uses_entities_roles_not_contacts_roles(
        self, mock_pool: AsyncMock
    ) -> None:
        """Primary path filters on e.roles (shared.entities), not c.roles (dropped in core_016)."""
        mock_pool.fetchrow = AsyncMock(
            return_value={"id": OWNER_UUID, "canonical_name": OWNER_NAME}
        )
        await _resolve_owner(mock_pool)
        first_call_sql = mock_pool.fetchrow.call_args_list[0].args[0]
        # Must not reference c.roles (dropped column)
        assert "c.roles" not in first_call_sql
        # Must filter via e.roles (correct column after core_016)
        assert "e.roles" in first_call_sql or "ANY(e.roles)" in first_call_sql


# ---------------------------------------------------------------------------
# Tests — set_preference
# ---------------------------------------------------------------------------


class TestSetPreference:
    """Tests for set_preference() tool wrapper."""

    @pytest.fixture(autouse=True)
    def _patch_embedding(self, mock_embedding_engine: MagicMock):
        with patch(
            "butlers.modules.memory.tools.preferences.get_embedding_engine",
            return_value=mock_embedding_engine,
        ):
            yield

    @pytest.fixture()
    def mock_resolve_owner(self):
        with patch(
            "butlers.modules.memory.tools.preferences._resolve_owner",
            new_callable=AsyncMock,
            return_value=(OWNER_UUID, OWNER_NAME),
        ) as m:
            yield m

    @pytest.fixture()
    def mock_store_fact(self):
        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID, "supersedes_id": None},
        ) as m:
            yield m

    async def test_basic_preference_storage(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """set_preference stores a fact and returns a well-formed response dict."""
        result = await set_preference(mock_pool, "preferences:travel_flight_seat", "window")
        assert result["id"] == FACT_UUID_STR
        assert result["predicate"] == "preferences:travel_flight_seat"
        assert result["scope"] == "travel"
        assert result["owner_entity_id"] == OWNER_UUID_STR
        assert result["action"] == "created"
        assert result["superseded_id"] is None

    async def test_scope_derived_from_predicate(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """Scope is derived from domain segment of predicate."""
        result = await set_preference(
            mock_pool, "preferences:health_dietary_restriction", "no shellfish"
        )
        assert result["scope"] == "health"

    async def test_general_predicate_uses_global_scope(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """preferences:general_* predicates use 'global' scope."""
        result = await set_preference(mock_pool, "preferences:general_language", "English")
        assert result["scope"] == "global"

    async def test_delegates_to_store_fact_with_defaults(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """store_fact is called with preference defaults."""
        await set_preference(mock_pool, "preferences:travel_flight_seat", "window")
        mock_store_fact.assert_awaited_once()
        _call = mock_store_fact.call_args
        assert _call.kwargs.get("importance") == PREFERENCE_IMPORTANCE_DEFAULT
        assert _call.kwargs.get("permanence") == PREFERENCE_PERMANENCE_DEFAULT
        assert _call.kwargs.get("retention_class") == PREFERENCE_RETENTION_CLASS
        assert _call.kwargs.get("entity_id") == OWNER_UUID
        assert _call.kwargs.get("scope") == "travel"

    async def test_subject_is_owner_canonical_name(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """The subject field is the owner's canonical_name."""
        await set_preference(mock_pool, "preferences:travel_flight_seat", "window")
        _call = mock_store_fact.call_args
        positional = _call.args
        # store_fact(pool, subject, predicate, content, embedding_engine, ...)
        assert positional[1] == OWNER_NAME

    async def test_permanence_override(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """Passing permanence='permanent' overrides the default."""
        await set_preference(
            mock_pool, "preferences:travel_flight_seat", "window", permanence="permanent"
        )
        _call = mock_store_fact.call_args
        assert _call.kwargs.get("permanence") == "permanent"

    async def test_importance_override(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """Passing importance=9.5 overrides the default."""
        await set_preference(mock_pool, "preferences:travel_flight_seat", "window", importance=9.5)
        _call = mock_store_fact.call_args
        assert _call.kwargs.get("importance") == 9.5

    async def test_metadata_forwarded_to_store_fact(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """Optional metadata is forwarded to store_fact."""
        meta = {"source": "user_explicit", "confidence_note": "stated directly"}
        await set_preference(mock_pool, "preferences:general_language", "English", metadata=meta)
        _call = mock_store_fact.call_args
        assert _call.kwargs.get("metadata") == meta

    async def test_supersession_indicated_in_response(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """When store_fact returns supersedes_id, action='updated' and superseded_id is set."""
        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID, "supersedes_id": SUPERSEDED_UUID},
        ):
            result = await set_preference(mock_pool, "preferences:travel_flight_seat", "aisle")
        assert result["action"] == "updated"
        assert result["superseded_id"] == SUPERSEDED_UUID_STR

    async def test_predicate_validation_raises_on_invalid_prefix(
        self, mock_pool: AsyncMock
    ) -> None:
        """Predicates not starting with 'preferences:' raise ValueError."""
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(mock_pool, "travel_flight_seat", "window")

    async def test_predicate_validation_raises_on_arbitrary_predicate(
        self, mock_pool: AsyncMock
    ) -> None:
        """A generic predicate raises ValueError with format hint."""
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(mock_pool, "favorite_color", "blue")

    async def test_predicate_validation_raises_on_empty_after_prefix(
        self, mock_pool: AsyncMock
    ) -> None:
        """'preferences:' with nothing after it raises ValueError (no domain_name segment)."""
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(mock_pool, "preferences:", "value")

    async def test_predicate_validation_raises_on_missing_underscore(
        self, mock_pool: AsyncMock
    ) -> None:
        """'preferences:nodomain' with no underscore raises ValueError."""
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(mock_pool, "preferences:nodomain", "value")

    async def test_predicate_validation_raises_on_leading_underscore(
        self, mock_pool: AsyncMock
    ) -> None:
        """'preferences:_name' with leading underscore (empty domain) raises ValueError."""
        with pytest.raises(ValueError, match="preferences:"):
            await set_preference(mock_pool, "preferences:_name", "value")

    async def test_error_message_includes_format_hint(self, mock_pool: AsyncMock) -> None:
        """ValueError message includes the correct format hint."""
        with pytest.raises(ValueError) as exc_info:
            await set_preference(mock_pool, "bad_predicate", "value")
        msg = str(exc_info.value)
        assert PREFERENCE_PREDICATE_PREFIX in msg
        assert "domain" in msg.lower() or "format" in msg.lower()

    async def test_owner_resolution_failure_raises(self, mock_pool: AsyncMock) -> None:
        """ValueError from _resolve_owner propagates from set_preference."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Owner entity could not be resolved"):
            await set_preference(mock_pool, "preferences:general_language", "English")

    async def test_returns_dict_with_expected_keys(
        self,
        mock_pool: AsyncMock,
        mock_resolve_owner: AsyncMock,
        mock_store_fact: AsyncMock,
    ) -> None:
        """Response dict always has all required keys."""
        result = await set_preference(mock_pool, "preferences:home_temperature_unit", "celsius")
        assert set(result.keys()) >= {
            "id",
            "superseded_id",
            "action",
            "predicate",
            "scope",
            "owner_entity_id",
        }


# ---------------------------------------------------------------------------
# Tests — get_preferences
# ---------------------------------------------------------------------------


class TestGetPreferences:
    """Tests for get_preferences() tool wrapper."""

    @pytest.fixture()
    def mock_resolve_owner(self):
        with patch(
            "butlers.modules.memory.tools.preferences._resolve_owner",
            new_callable=AsyncMock,
            return_value=(OWNER_UUID, OWNER_NAME),
        ) as m:
            yield m

    def _make_row(
        self,
        predicate: str = "preferences:travel_flight_seat",
        content: str = "window",
        scope: str = "travel",
        importance: float = 8.0,
        permanence: str = "stable",
        confidence: float = 1.0,
        decay_rate: float = 0.002,
        last_confirmed_at: datetime | None = None,
    ) -> dict:
        return {
            "predicate": predicate,
            "value": content,
            "scope": scope,
            "importance": importance,
            "permanence": permanence,
            "updated_at": NOW,
            "confidence": confidence,
            "decay_rate": decay_rate,
            "last_confirmed_at": last_confirmed_at or NOW,
        }

    async def test_returns_empty_list_when_no_rows(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """get_preferences returns empty list when no active preference facts exist."""
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await get_preferences(mock_pool)
        assert result == []

    async def test_returns_empty_list_when_owner_not_found(self, mock_pool: AsyncMock) -> None:
        """get_preferences returns empty list (not error) when owner not found."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await get_preferences(mock_pool)
        assert result == []

    async def test_passes_owner_entity_id_to_query(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """fetch is called with owner_entity_id as first parameter."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_preferences(mock_pool)
        mock_pool.fetch.assert_awaited_once()
        _call = mock_pool.fetch.call_args
        assert OWNER_UUID in _call.args

    async def test_uses_default_preferences_pattern(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """Default predicate_pattern is 'preferences:%'."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_preferences(mock_pool)
        _call = mock_pool.fetch.call_args
        assert "preferences:%" in _call.args

    async def test_scope_filter_added_when_provided(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """scope filter is passed to the query when provided."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_preferences(mock_pool, scope="travel")
        _call = mock_pool.fetch.call_args
        assert "travel" in _call.args

    async def test_custom_predicate_pattern_used(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """Custom predicate_pattern is used instead of default."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_preferences(mock_pool, predicate_pattern="preferences:health_%")
        _call = mock_pool.fetch.call_args
        assert "preferences:health_%" in _call.args

    async def test_result_shape_has_expected_keys(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """Each result dict has the required keys."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row()])
        results = await get_preferences(mock_pool)
        assert len(results) == 1
        row = results[0]
        assert set(row.keys()) == {
            "predicate",
            "value",
            "scope",
            "importance",
            "permanence",
            "updated_at",
            "effective_confidence",
        }

    async def test_value_field_from_content(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """The 'value' field in results is the stored content."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row(content="aisle")])
        results = await get_preferences(mock_pool)
        assert results[0]["value"] == "aisle"

    async def test_updated_at_is_isoformat(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """updated_at is an ISO-8601 string."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row()])
        results = await get_preferences(mock_pool)
        assert isinstance(results[0]["updated_at"], str)
        # Should be parseable as ISO-8601
        datetime.fromisoformat(results[0]["updated_at"])

    async def test_effective_confidence_no_decay_when_rate_zero(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """effective_confidence equals confidence when decay_rate=0."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row(confidence=0.9, decay_rate=0.0)])
        results = await get_preferences(mock_pool)
        assert results[0]["effective_confidence"] == 0.9

    async def test_effective_confidence_decays_over_time(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """effective_confidence is less than initial confidence after decay."""
        old_confirmed = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        mock_pool.fetch = AsyncMock(
            return_value=[
                self._make_row(confidence=1.0, decay_rate=0.002, last_confirmed_at=old_confirmed)
            ]
        )
        results = await get_preferences(mock_pool)
        assert results[0]["effective_confidence"] < 1.0

    async def test_multiple_results_ordered_by_predicate(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """Results should be returned in predicate order (SQL enforces this)."""
        rows = [
            self._make_row(predicate="preferences:travel_flight_seat"),
            self._make_row(predicate="preferences:health_dietary_restriction", scope="health"),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)
        results = await get_preferences(mock_pool)
        assert len(results) == 2
        # Order matches what the DB returns (ordered ASC in SQL)
        assert results[0]["predicate"] == "preferences:travel_flight_seat"

    async def test_importance_is_float(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """importance field is a Python float."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row(importance=8.0)])
        results = await get_preferences(mock_pool)
        assert isinstance(results[0]["importance"], float)

    async def test_sql_query_filters_by_predicate_pattern(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """The SQL query uses LIKE on predicate."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_preferences(mock_pool)
        # Check the SQL sent to pool.fetch contains the predicate LIKE clause.
        sql_arg = mock_pool.fetch.call_args.args[0]
        assert "LIKE" in sql_arg
        assert "preferences" in sql_arg or "predicate" in sql_arg

    async def test_zero_confidence_preserved(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """confidence=0.0 must not be coerced to 1.0 via falsy check."""
        mock_pool.fetch = AsyncMock(return_value=[self._make_row(confidence=0.0, decay_rate=0.0)])
        results = await get_preferences(mock_pool)
        assert results[0]["effective_confidence"] == 0.0

    async def test_effective_confidence_uses_exponential_decay_not_compound(
        self, mock_pool: AsyncMock, mock_resolve_owner: AsyncMock
    ) -> None:
        """effective_confidence uses exp(-rate * days), not (1 - rate) ** days.

        At rate=0.5, 10 days:
            exp(-0.5 * 10) = exp(-5) ≈ 0.0067
            (1 - 0.5)**10  = 0.5**10 ≈ 0.000977
        These are far enough apart to verify the correct formula.
        """
        from datetime import timedelta

        # Use a fixed 10-day-old anchor (relative to "now" at test run time).
        ten_days_ago = datetime.now(UTC) - timedelta(days=10)
        row = self._make_row(confidence=1.0, decay_rate=0.5, last_confirmed_at=ten_days_ago)
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await get_preferences(mock_pool)
        eff = results[0]["effective_confidence"]

        # The actual days elapsed may differ slightly from 10 (sub-second precision).
        # We just need to verify the value is NOT consistent with compound decay.
        compound_at_10 = round((1.0 - 0.5) ** 10, 4)  # ≈ 0.001
        # exp formula produces much higher value (~0.0067) — well above compound.
        assert eff > compound_at_10 * 3, (
            f"Expected exp(-rate*days) >> (1-rate)**days, got eff={eff}, compound≈{compound_at_10}"
        )
