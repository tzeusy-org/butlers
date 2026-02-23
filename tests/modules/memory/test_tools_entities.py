"""Tests for entity MCP tools — entity_create, entity_get, entity_update."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory.tools.entities import entity_create, entity_get, entity_update

pytestmark = pytest.mark.unit

SAMPLE_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SAMPLE_UUID_STR = str(SAMPLE_UUID)
TENANT_ID = "tenant-abc"
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    return AsyncMock()


def _make_entity_row(
    entity_id: uuid.UUID = SAMPLE_UUID,
    tenant_id: str = TENANT_ID,
    canonical_name: str = "Alice Smith",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build a sample entity row dict."""
    return {
        "id": entity_id,
        "tenant_id": tenant_id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "metadata": metadata or {},
        "created_at": NOW,
        "updated_at": NOW,
    }


# ---------------------------------------------------------------------------
# entity_create tests
# ---------------------------------------------------------------------------


class TestEntityCreate:
    """Tests for entity_create() tool wrapper."""

    async def test_inserts_entity_and_returns_uuid(self, mock_pool: AsyncMock) -> None:
        """entity_create calls fetchval with INSERT and returns entity_id string."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)

        result = await entity_create(
            mock_pool,
            "Alice Smith",
            "person",
            tenant_id=TENANT_ID,
        )

        assert result == {"entity_id": SAMPLE_UUID_STR}
        mock_pool.fetchval.assert_awaited_once()
        sql, *params = mock_pool.fetchval.call_args[0]
        assert "INSERT INTO entities" in sql
        assert TENANT_ID in params
        assert "Alice Smith" in params
        assert "person" in params

    async def test_returns_entity_id_as_string(self, mock_pool: AsyncMock) -> None:
        """entity_id in result must be a string, not a UUID object."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        result = await entity_create(mock_pool, "Acme Corp", "organization", tenant_id=TENANT_ID)
        assert isinstance(result["entity_id"], str)
        assert result["entity_id"] == SAMPLE_UUID_STR

    async def test_passes_aliases_to_db(self, mock_pool: AsyncMock) -> None:
        """Aliases are forwarded to the INSERT statement."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        await entity_create(
            mock_pool,
            "Alice Smith",
            "person",
            tenant_id=TENANT_ID,
            aliases=["Ali", "A. Smith"],
        )
        _, tenant, name, etype, aliases_arg, _ = mock_pool.fetchval.call_args[0]
        assert aliases_arg == ["Ali", "A. Smith"]

    async def test_defaults_aliases_to_empty_list(self, mock_pool: AsyncMock) -> None:
        """Omitting aliases passes an empty list to DB."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        await entity_create(mock_pool, "Bob", "person", tenant_id=TENANT_ID)
        _, tenant, name, etype, aliases_arg, _ = mock_pool.fetchval.call_args[0]
        assert aliases_arg == []

    async def test_passes_metadata_as_json(self, mock_pool: AsyncMock) -> None:
        """Metadata dict is serialized to JSON for the DB."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        meta = {"role": "engineer", "active": True}
        await entity_create(
            mock_pool,
            "Carol",
            "person",
            tenant_id=TENANT_ID,
            metadata=meta,
        )
        _, tenant, name, etype, aliases_arg, metadata_arg = mock_pool.fetchval.call_args[0]
        assert json.loads(metadata_arg) == meta

    async def test_defaults_metadata_to_empty_json_object(self, mock_pool: AsyncMock) -> None:
        """Omitting metadata passes '{}' JSON to DB."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        await entity_create(mock_pool, "Dave", "person", tenant_id=TENANT_ID)
        _, tenant, name, etype, aliases_arg, metadata_arg = mock_pool.fetchval.call_args[0]
        assert json.loads(metadata_arg) == {}

    async def test_raises_value_error_on_unique_constraint_violation(
        self, mock_pool: AsyncMock
    ) -> None:
        """Unique constraint violation is re-raised as ValueError."""
        mock_pool.fetchval = AsyncMock(
            side_effect=Exception(
                'duplicate key value violates unique constraint "uq_entities_tenant_canonical_type"'
            )
        )
        with pytest.raises(ValueError, match="already exists"):
            await entity_create(mock_pool, "Alice", "person", tenant_id=TENANT_ID)

    async def test_raises_value_error_on_generic_unique_violation(
        self, mock_pool: AsyncMock
    ) -> None:
        """Any unique constraint violation is detected and re-raised as ValueError."""
        mock_pool.fetchval = AsyncMock(
            side_effect=Exception("ERROR: duplicate key value violates unique constraint")
        )
        with pytest.raises(ValueError, match="already exists"):
            await entity_create(mock_pool, "Alice", "person", tenant_id=TENANT_ID)

    async def test_reraises_other_db_errors(self, mock_pool: AsyncMock) -> None:
        """Non-constraint DB errors propagate unchanged."""
        mock_pool.fetchval = AsyncMock(side_effect=RuntimeError("connection refused"))
        with pytest.raises(RuntimeError, match="connection refused"):
            await entity_create(mock_pool, "Eve", "person", tenant_id=TENANT_ID)

    async def test_invalid_entity_type_raises_value_error(self, mock_pool: AsyncMock) -> None:
        """Invalid entity_type raises ValueError without hitting the DB."""
        with pytest.raises(ValueError, match="Invalid entity_type"):
            await entity_create(mock_pool, "Ghost", "ghost", tenant_id=TENANT_ID)
        mock_pool.fetchval.assert_not_awaited()

    @pytest.mark.parametrize("entity_type", ["person", "organization", "place", "other"])
    async def test_all_valid_entity_types_accepted(
        self, mock_pool: AsyncMock, entity_type: str
    ) -> None:
        """All valid entity types (person, organization, place, other) are accepted."""
        mock_pool.fetchval = AsyncMock(return_value=SAMPLE_UUID)
        result = await entity_create(mock_pool, "Test Entity", entity_type, tenant_id=TENANT_ID)
        assert "entity_id" in result


# ---------------------------------------------------------------------------
# entity_get tests
# ---------------------------------------------------------------------------


class TestEntityGet:
    """Tests for entity_get() tool wrapper."""

    async def test_returns_entity_record(self, mock_pool: AsyncMock) -> None:
        """entity_get returns a serialized dict of the entity."""
        row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)

        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)

        assert result is not None
        assert result["id"] == SAMPLE_UUID_STR
        assert result["canonical_name"] == "Alice Smith"
        assert result["entity_type"] == "person"

    async def test_returns_none_when_not_found(self, mock_pool: AsyncMock) -> None:
        """entity_get returns None when the entity does not exist."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        assert result is None

    async def test_tenant_isolation_in_query(self, mock_pool: AsyncMock) -> None:
        """Query includes tenant_id for isolation."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        sql, eid_arg, tid_arg = mock_pool.fetchrow.call_args[0]
        assert "tenant_id" in sql
        assert tid_arg == TENANT_ID

    async def test_converts_string_id_to_uuid(self, mock_pool: AsyncMock) -> None:
        """entity_id string is converted to UUID before querying."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        sql, eid_arg, tid_arg = mock_pool.fetchrow.call_args[0]
        assert eid_arg == SAMPLE_UUID
        assert isinstance(eid_arg, uuid.UUID)

    async def test_serializes_uuid_fields_to_strings(self, mock_pool: AsyncMock) -> None:
        """UUID fields in the result are serialized to strings."""
        row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        assert isinstance(result["id"], str)

    async def test_serializes_datetime_fields_to_isoformat(self, mock_pool: AsyncMock) -> None:
        """Datetime fields are serialized to ISO format strings."""
        row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        assert result["created_at"] == NOW.isoformat()
        assert result["updated_at"] == NOW.isoformat()

    async def test_returns_aliases_and_metadata(self, mock_pool: AsyncMock) -> None:
        """entity_get includes aliases and metadata in the result."""
        row = _make_entity_row(
            aliases=["Ali", "A. Smith"],
            metadata={"role": "engineer"},
        )
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        assert result["aliases"] == ["Ali", "A. Smith"]
        assert result["metadata"] == {"role": "engineer"}

    async def test_result_has_all_expected_keys(self, mock_pool: AsyncMock) -> None:
        """Result dict includes all entity table columns."""
        row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(return_value=row)
        result = await entity_get(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        expected_keys = {
            "id",
            "tenant_id",
            "canonical_name",
            "entity_type",
            "aliases",
            "metadata",
            "created_at",
            "updated_at",
        }
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# entity_update tests
# ---------------------------------------------------------------------------


class TestEntityUpdate:
    """Tests for entity_update() tool wrapper."""

    async def test_returns_none_when_not_found(self, mock_pool: AsyncMock) -> None:
        """entity_update returns None if the entity doesn't exist for this tenant."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await entity_update(
            mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID, canonical_name="New Name"
        )
        assert result is None

    async def test_updates_canonical_name(self, mock_pool: AsyncMock) -> None:
        """canonical_name is included in the UPDATE when provided."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row(canonical_name="New Name")

        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(
            mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID, canonical_name="New Name"
        )

        assert result is not None
        assert result["canonical_name"] == "New Name"
        # Confirm UPDATE SQL was issued (second fetchrow call)
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "UPDATE entities" in second_call_sql
        assert "canonical_name" in second_call_sql

    async def test_updates_aliases_with_replace_all(self, mock_pool: AsyncMock) -> None:
        """Alias updates use replace-all semantics."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row(aliases=["NewAlias"])
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(
            mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID, aliases=["NewAlias"]
        )

        assert result is not None
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "aliases" in second_call_sql

    async def test_merges_metadata(self, mock_pool: AsyncMock) -> None:
        """Metadata update merges new keys with existing metadata."""
        existing_meta = {"role": "engineer", "level": 3}
        current_row = {"id": SAMPLE_UUID, "metadata": existing_meta}
        merged_meta = {"role": "engineer", "level": 3, "team": "backend"}
        updated_row = _make_entity_row(metadata=merged_meta)
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(
            mock_pool,
            SAMPLE_UUID_STR,
            tenant_id=TENANT_ID,
            metadata={"team": "backend"},
        )

        assert result is not None
        # The UPDATE call should include merged metadata JSON
        second_call_args = mock_pool.fetchrow.call_args_list[1][0]
        # Find the metadata param (a JSON string containing the merged data)
        json_params = [p for p in second_call_args[1:] if isinstance(p, str) and "backend" in p]
        assert len(json_params) == 1
        assert json.loads(json_params[0]) == merged_meta

    async def test_new_metadata_key_wins_on_conflict(self, mock_pool: AsyncMock) -> None:
        """When metadata keys conflict, the new value overwrites the existing one."""
        existing_meta = {"role": "engineer"}
        current_row = {"id": SAMPLE_UUID, "metadata": existing_meta}
        updated_row = _make_entity_row(metadata={"role": "manager"})
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        await entity_update(
            mock_pool,
            SAMPLE_UUID_STR,
            tenant_id=TENANT_ID,
            metadata={"role": "manager"},
        )

        second_call_args = mock_pool.fetchrow.call_args_list[1][0]
        json_params = [p for p in second_call_args[1:] if isinstance(p, str) and "manager" in p]
        assert len(json_params) == 1
        assert json.loads(json_params[0])["role"] == "manager"

    async def test_no_fields_provided_still_updates_updated_at(self, mock_pool: AsyncMock) -> None:
        """Even with no fields provided, the updated_at timestamp is refreshed."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)

        assert result is not None
        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "updated_at = now()" in second_call_sql

    async def test_serializes_uuid_fields(self, mock_pool: AsyncMock) -> None:
        """UUID fields in update result are serialized to strings."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)

        assert isinstance(result["id"], str)
        assert result["id"] == SAMPLE_UUID_STR

    async def test_serializes_datetime_fields(self, mock_pool: AsyncMock) -> None:
        """Datetime fields in update result are serialized to ISO format."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        result = await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)

        assert result["created_at"] == NOW.isoformat()
        assert result["updated_at"] == NOW.isoformat()

    async def test_tenant_isolation_in_existence_check(self, mock_pool: AsyncMock) -> None:
        """The existence check query includes tenant_id for isolation."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        first_call_sql = mock_pool.fetchrow.call_args_list[0][0][0]
        assert "tenant_id" in first_call_sql

    async def test_tenant_isolation_in_update_query(self, mock_pool: AsyncMock) -> None:
        """The UPDATE query includes tenant_id for isolation."""
        current_row = {"id": SAMPLE_UUID, "metadata": {}}
        updated_row = _make_entity_row()
        mock_pool.fetchrow = AsyncMock(side_effect=[current_row, updated_row])

        await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID, canonical_name="X")

        second_call_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "tenant_id" in second_call_sql

    async def test_converts_string_id_to_uuid(self, mock_pool: AsyncMock) -> None:
        """entity_id string is converted to UUID before querying."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        await entity_update(mock_pool, SAMPLE_UUID_STR, tenant_id=TENANT_ID)
        first_call_args = mock_pool.fetchrow.call_args_list[0][0]
        # Second positional arg is the entity_id UUID
        eid_arg = first_call_args[1]
        assert eid_arg == SAMPLE_UUID
        assert isinstance(eid_arg, uuid.UUID)
