"""Behavioral tests for entity MCP tools.

Covers: entity_create, entity_get, entity_update, entity_resolve, entity_merge,
entity_neighbors — testing through the public tool interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.tools.entities import (
    _SCORE_EXACT_DEMOTED,
    _SCORE_EXACT_NAME,
    entity_create,
    entity_get,
    entity_merge,
    entity_neighbors,
    entity_resolve,
    entity_update,
)

pytestmark = pytest.mark.unit

ENTITY_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
ENTITY_STR = str(ENTITY_UUID)
SOURCE_UUID = uuid.UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
TARGET_UUID = uuid.UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
SOURCE_ID = str(SOURCE_UUID)
TARGET_ID = str(TARGET_UUID)
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_row(
    entity_id: uuid.UUID = ENTITY_UUID,
    name: str = "Alice",
    entity_type: str = "person",
    aliases: list | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": entity_id,
        "canonical_name": name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "metadata": metadata or {},
        "created_at": NOW,
        "updated_at": NOW,
    }


def _entity_mock_row(
    entity_id: uuid.UUID,
    canonical_name: str = "Test",
    aliases: list | None = None,
    metadata: dict | None = None,
    match_type: str = "exact",
    fact_count: int = 0,
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda s, k: {
        "id": entity_id,
        "canonical_name": canonical_name,
        "entity_type": "person",
        "aliases": aliases or [],
        "metadata": metadata or {},
        "roles": [],
        "match_type": match_type,
        "fact_count": fact_count,
    }[k]
    return row


@pytest.fixture()
def pool() -> AsyncMock:
    return AsyncMock()


def _merge_pool(
    src_row,
    tgt_row,
    *,
    fact_rows=None,
    edge_rows=None,
    extra_fetchrow=None,
    cal_src_rows=None,
    cal_tgt_rows=None,
):
    """Build a mock pool for entity_merge with conn set up properly.

    conn.fetch side_effect order matches entity_merge call sequence:
      1. _repoint_facts_on_pool subject-side facts
      2. _repoint_facts_on_pool edge/object-side facts
      3. _repoint_calendar_event_entities source event_ids
      4. _repoint_calendar_event_entities already-linked target rows
    """
    pool = MagicMock()
    conn = AsyncMock()
    extra = extra_fetchrow or []
    conn.fetchrow = AsyncMock(side_effect=[src_row, tgt_row, *extra])
    conn.fetch = AsyncMock(
        side_effect=[
            (fact_rows or []),
            (edge_rows or []),
            (cal_src_rows or []),
            (cal_tgt_rows or []),
        ]
    )
    conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    return pool, conn


# ---------------------------------------------------------------------------
# entity_create
# ---------------------------------------------------------------------------


class TestEntityCreate:
    async def test_returns_entity_id_string(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=ENTITY_UUID)
        result = await entity_create(pool, "Alice", "person")
        assert result == {"entity_id": ENTITY_STR}

    async def test_invalid_type_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="Invalid entity_type"):
            await entity_create(pool, "Ghost", "ghost")
        pool.fetchval.assert_not_awaited()

    async def test_unique_constraint_raises_value_error(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(
            side_effect=[Exception("duplicate key value violates unique constraint"), None]
        )
        with pytest.raises(ValueError, match="already exists"):
            await entity_create(pool, "Alice", "person")

    async def test_tombstoned_entity_allows_retry(self, pool: AsyncMock) -> None:
        tombstone_id = uuid.uuid4()
        new_id = uuid.uuid4()
        pool.fetchval = AsyncMock(
            side_effect=[
                Exception("duplicate key value violates unique constraint"),
                tombstone_id,
                new_id,
            ]
        )
        result = await entity_create(pool, "Alice", "person")
        assert result == {"entity_id": str(new_id)}


# ---------------------------------------------------------------------------
# entity_get
# ---------------------------------------------------------------------------


class TestEntityGet:
    async def test_returns_serialized_entity(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=_entity_row())
        result = await entity_get(pool, ENTITY_STR)
        assert result["id"] == ENTITY_STR
        assert result["canonical_name"] == "Alice"
        assert isinstance(result["created_at"], str)

    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)
        assert await entity_get(pool, ENTITY_STR) is None


# ---------------------------------------------------------------------------
# entity_update
# ---------------------------------------------------------------------------


class TestEntityUpdate:
    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)
        assert await entity_update(pool, ENTITY_STR) is None

    async def test_updates_and_returns_serialized(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(
            side_effect=[
                {"id": ENTITY_UUID, "metadata": {}},
                _entity_row(name="New Name"),
            ]
        )
        result = await entity_update(pool, ENTITY_STR, canonical_name="New Name")
        assert result is not None
        assert result["canonical_name"] == "New Name"


# ---------------------------------------------------------------------------
# entity_resolve
# ---------------------------------------------------------------------------


class TestEntityResolve:
    async def test_both_name_and_identifier_none_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await entity_resolve(pool, name=None, identifier=None)
        pool.fetch.assert_not_called()

    async def test_empty_identifier_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await entity_resolve(pool, identifier="")
        pool.fetch.assert_not_called()

    async def test_whitespace_identifier_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await entity_resolve(pool, identifier="   ")
        pool.fetch.assert_not_called()

    async def test_empty_name_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await entity_resolve(pool, "")
        pool.fetch.assert_not_called()

    async def test_normal_identifier_still_works(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Mah Rock", match_type="exact")
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, identifier="Mah Rock")
        assert results
        assert results[0]["canonical_name"] == "Mah Rock"

    async def test_exact_match_returns_score_exact_name(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alice", match_type="exact")
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "Alice")
        assert results[0]["score"] == _SCORE_EXACT_NAME
        assert results[0]["name_match"] == "exact"

    async def test_results_sorted_by_score_desc(self, pool: AsyncMock) -> None:
        e1 = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alice", match_type="exact")
        e2 = _entity_mock_row(uuid.UUID(str(uuid.uuid4())), "Alicia", match_type="prefix")
        pool.fetch = AsyncMock(return_value=[e2, e1])
        results = await entity_resolve(pool, "Alice")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_identifier_and_name_raises(self, pool: AsyncMock) -> None:
        with pytest.raises(ValueError, match="not both"):
            await entity_resolve(pool, "Alice", identifier="Owner")

    async def test_single_exact_alias_match_returns_score_100(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Bob", match_type="exact", fact_count=0
        )
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "Bob")
        assert results[0]["score"] == 100
        assert results[0]["name_match"] == "exact"

    async def test_two_exact_candidates_unequal_fact_count(self, pool: AsyncMock) -> None:
        e1 = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Alice", match_type="exact", fact_count=5
        )
        e2 = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Alice Corp", match_type="exact", fact_count=20
        )
        pool.fetch = AsyncMock(return_value=[e1, e2])
        results = await entity_resolve(pool, "alice")
        by_name = {r["canonical_name"]: r for r in results}
        assert by_name["Alice Corp"]["score"] == _SCORE_EXACT_NAME
        assert by_name["Alice"]["score"] == _SCORE_EXACT_DEMOTED

    async def test_tied_fact_count_both_score_100(self, pool: AsyncMock) -> None:
        e1 = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Eve A", match_type="exact", fact_count=8
        )
        e2 = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Eve B", match_type="exact", fact_count=8
        )
        pool.fetch = AsyncMock(return_value=[e1, e2])
        results = await entity_resolve(pool, "eve")
        assert all(r["score"] == _SCORE_EXACT_NAME for r in results)

    async def test_zero_fact_count_single_candidate_score_100(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Zara", match_type="exact", fact_count=0
        )
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "zara")
        assert results[0]["score"] == _SCORE_EXACT_NAME

    async def test_fact_count_in_return_shape(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Dave", match_type="exact", fact_count=3
        )
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "Dave")
        assert "fact_count" in results[0]
        assert isinstance(results[0]["fact_count"], int)

    async def test_name_match_never_alias(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Charlie", match_type="exact", fact_count=0
        )
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "charlie")
        assert results[0]["name_match"] == "exact"
        assert results[0]["name_match"] != "alias"


class TestEntityResolveSchema:
    """Verify MCP tool schema is tightened so strict-validation runtimes can
    reject null/empty identifier inputs before they reach the server."""

    async def _get_resolve_tool(self):
        from unittest.mock import MagicMock, patch

        from butlers.modules.memory import MemoryModule

        mod = MemoryModule()
        fake_db = MagicMock()
        fake_db.pool = MagicMock(name="fake_pool")
        mcp = MagicMock()
        registered: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        parent_mock = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "butlers.modules.memory.tools": parent_mock,
                "butlers.modules.memory.tools.writing": MagicMock(),
                "butlers.modules.memory.tools.reading": MagicMock(),
                "butlers.modules.memory.tools.feedback": MagicMock(),
                "butlers.modules.memory.tools.management": MagicMock(),
                "butlers.modules.memory.tools.context": MagicMock(),
                "butlers.modules.memory.tools.entities": MagicMock(),
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=fake_db, butler_name="test-butler")
        return registered["memory_entity_resolve"]

    async def test_identifier_is_required_non_null_non_empty(self) -> None:
        import inspect
        import typing

        from pydantic.fields import FieldInfo

        tool = await self._get_resolve_tool()
        sig = inspect.signature(tool)
        assert "identifier" in sig.parameters
        ident = sig.parameters["identifier"]

        # identifier is a required positional (no default)
        assert ident.default is inspect.Parameter.empty, (
            "identifier must be required (no default) for strict schema validation"
        )

        # Resolve forward-ref annotations (file uses `from __future__ import annotations`).
        hints = typing.get_type_hints(tool, include_extras=True)
        ident_type = hints["identifier"]
        args = typing.get_args(ident_type)
        assert args, f"identifier must be Annotated[str, Field(...)]; got {ident_type!r}"
        assert args[0] is str, f"identifier must be typed `str` (non-nullable), got {args[0]}"

        # Field metadata enforces min_length=1
        field_info = next((a for a in args[1:] if isinstance(a, FieldInfo)), None)
        assert field_info is not None, "identifier must carry a pydantic Field"
        metadata_strs = [str(m) for m in field_info.metadata]
        assert any("min_length=1" in m for m in metadata_strs), (
            f"identifier Field must enforce min_length=1; got metadata={field_info.metadata}"
        )

    async def test_legacy_name_parameter_removed_from_tool_signature(self) -> None:
        """The MCP tool surface no longer exposes the ambiguous legacy `name` kwarg;
        only `identifier` is accepted, which prevents callers from passing both null."""
        import inspect

        tool = await self._get_resolve_tool()
        sig = inspect.signature(tool)
        assert "name" not in sig.parameters


# ---------------------------------------------------------------------------
# entity_merge
# ---------------------------------------------------------------------------


class TestEntityMerge:
    async def test_raises_when_source_equals_target(self) -> None:
        with pytest.raises(ValueError, match="must be different"):
            await entity_merge(AsyncMock(), SOURCE_ID, SOURCE_ID)

    async def test_raises_when_already_tombstoned(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, metadata={"merged_into": TARGET_ID})
        tgt = _entity_mock_row(TARGET_UUID)
        pool, _conn = _merge_pool(src, tgt)
        with pytest.raises(ValueError, match="already tombstoned"):
            await entity_merge(pool, SOURCE_ID, TARGET_ID)

    async def test_result_has_expected_keys(self) -> None:
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(src, tgt)
        result = await entity_merge(pool, SOURCE_ID, TARGET_ID)
        assert {
            "target_entity_id",
            "source_entity_id",
            "facts_repointed",
            "facts_superseded",
            "edge_facts_repointed",
            "edge_facts_superseded",
            "aliases_added",
        } == set(result.keys())

    async def test_source_tombstoned_with_merged_into(self) -> None:
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        tombstones = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET metadata" in c[0][0] and SOURCE_UUID in c[0]
        ]
        assert len(tombstones) == 1
        # Metadata is now bound directly as a Python dict; the registered JSONB
        # codec on the asyncpg pool handles serialization. See [bu-qki26].
        assert tombstones[0][0][1]["merged_into"] == TARGET_ID

    async def test_unidentified_flag_not_propagated(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, metadata={"unidentified": True, "src_key": "v"})
        tgt = _entity_mock_row(TARGET_UUID, metadata={})
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        updates = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        # Metadata bound as a dict (pre-codec encoding).
        merged = updates[0][0][2]
        assert "unidentified" not in merged
        assert merged.get("src_key") == "v"

    async def test_source_canonical_name_added_to_target_aliases(self) -> None:
        src = _entity_mock_row(SOURCE_UUID, canonical_name="tzeusii", aliases=[])
        tgt = _entity_mock_row(TARGET_UUID, canonical_name="Tze How Lee", aliases=[])
        pool, conn = _merge_pool(src, tgt)
        await entity_merge(pool, SOURCE_ID, TARGET_ID)
        updates = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE public.entities SET aliases" in c[0][0] and TARGET_UUID in c[0]
        ]
        merged_aliases = updates[0][0][1]
        assert "tzeusii" in merged_aliases


# ---------------------------------------------------------------------------
# entity_merge — calendar_event_entities
# ---------------------------------------------------------------------------

EVENT_UUID_1 = uuid.UUID("cccccccc-1111-1111-1111-cccccccccccc")
EVENT_UUID_2 = uuid.UUID("dddddddd-2222-2222-2222-dddddddddddd")


def _cal_row(event_id: uuid.UUID) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"event_id": event_id}[k]
    return row


class TestEntityMergeCalendarEntities:
    """entity_merge re-points calendar_event_entities from source to target."""

    async def test_repoints_unshared_event_to_target(self) -> None:
        """When only the source is linked to an event, update entity_id to target."""
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        # cal_src_rows: source has EVENT_UUID_1; cal_tgt_rows: target has no shared events
        pool, conn = _merge_pool(
            src,
            tgt,
            cal_src_rows=[_cal_row(EVENT_UUID_1)],
            cal_tgt_rows=[],  # target not linked to EVENT_UUID_1
        )

        await entity_merge(pool, SOURCE_ID, TARGET_ID)

        update_calls = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE calendar_event_entities SET entity_id" in c[0][0]
        ]
        assert len(update_calls) == 1
        call_args = update_calls[0][0]
        assert call_args[1] == tgt_uuid  # new entity_id
        assert call_args[2] == src_uuid  # old entity_id (WHERE entity_id = $2)
        assert EVENT_UUID_1 in call_args[3]  # event_id in ANY($3) list

    async def test_deletes_duplicate_event_association(self) -> None:
        """When target is already linked to the same event, delete the source row."""
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        # Both source and target are linked to EVENT_UUID_1 — duplicate
        pool, conn = _merge_pool(
            src,
            tgt,
            cal_src_rows=[_cal_row(EVENT_UUID_1)],
            cal_tgt_rows=[_cal_row(EVENT_UUID_1)],
        )

        await entity_merge(pool, SOURCE_ID, TARGET_ID)

        delete_calls = [
            c
            for c in conn.execute.call_args_list
            if "DELETE FROM calendar_event_entities" in c[0][0]
        ]
        assert len(delete_calls) == 1
        call_args = delete_calls[0][0]
        assert src_uuid in call_args  # WHERE entity_id = $1
        assert EVENT_UUID_1 in call_args[2]  # event_id in ANY($2) list

    async def test_mixed_events_update_and_delete(self) -> None:
        """Source has two events; one shared with target (delete), one unique (update)."""
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(
            src,
            tgt,
            cal_src_rows=[_cal_row(EVENT_UUID_1), _cal_row(EVENT_UUID_2)],
            cal_tgt_rows=[_cal_row(EVENT_UUID_1)],  # EVENT_UUID_1 is shared
        )

        await entity_merge(pool, SOURCE_ID, TARGET_ID)

        update_calls = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE calendar_event_entities SET entity_id" in c[0][0]
        ]
        delete_calls = [
            c
            for c in conn.execute.call_args_list
            if "DELETE FROM calendar_event_entities" in c[0][0]
        ]
        assert len(update_calls) == 1  # EVENT_UUID_2 re-pointed
        assert len(delete_calls) == 1  # EVENT_UUID_1 deleted

    async def test_no_calendar_events_no_execute_calls_for_cal(self) -> None:
        """When source has no calendar event associations, no calendar SQL is issued."""
        src = _entity_mock_row(SOURCE_UUID)
        tgt = _entity_mock_row(TARGET_UUID)
        pool, conn = _merge_pool(
            src,
            tgt,
            cal_src_rows=[],
            cal_tgt_rows=[],
        )

        await entity_merge(pool, SOURCE_ID, TARGET_ID)

        cal_execute_calls = [
            c for c in conn.execute.call_args_list if "calendar_event_entities" in c[0][0]
        ]
        assert cal_execute_calls == []


# Module-level aliases for clarity in test assertions
src_uuid = SOURCE_UUID
tgt_uuid = TARGET_UUID


# ---------------------------------------------------------------------------
# entity_neighbors
# ---------------------------------------------------------------------------


class TestEntityNeighbors:
    async def test_raises_for_nonexistent_entity(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="does not exist"):
            await entity_neighbors(pool, ENTITY_STR)

    async def test_result_shape(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=1)
        neighbor_id = uuid.uuid4()
        pool.fetch = AsyncMock(
            return_value=[
                {
                    "entity_id": neighbor_id,
                    "canonical_name": "Bob",
                    "entity_type": "person",
                    "predicate": "knows",
                    "dir": "outgoing",
                    "content": "",
                    "fact_id": uuid.uuid4(),
                    "depth": 1,
                    "path": [ENTITY_UUID, neighbor_id],
                }
            ]
        )
        result = await entity_neighbors(pool, ENTITY_STR)
        assert len(result) == 1
        assert {"entity", "predicate", "direction", "content", "depth", "fact_id", "path"} == set(
            result[0].keys()
        )
        assert isinstance(result[0]["entity"]["id"], str)
