"""Behavioral tests for entity MCP tools.

Covers: entity_create, entity_get, entity_update, entity_resolve, entity_merge,
entity_neighbors, _repoint_episode_entities — testing through the public tool interface.

Also covers the MemoryModule MCP tool closure wiring (section "MCP tool — chronicler
pool wiring") to assert that memory_entity_merge passes chronicler_pool through to
entity_merge so that episode_entities rows are re-pointed on merge (bu-cojsp).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers import entity_rebind as _rebind
from butlers.modules.memory.tools.entities import (
    _SCORE_EXACT_DEMOTED,
    _SCORE_EXACT_NAME,
    _parse_rowcount,
    _repoint_episode_entities,
    _retract_facts_on_conn,
    entity_create,
    entity_find_by_canonical,
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
# entity_find_by_canonical
# ---------------------------------------------------------------------------


class TestEntityFindByCanonical:
    async def test_returns_serialized_live_entity(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=_entity_row())

        result = await entity_find_by_canonical(pool, "Alice", "person")

        assert result is not None
        assert result["id"] == ENTITY_STR
        assert result["canonical_name"] == "Alice"
        assert isinstance(result["created_at"], str)
        sql = pool.fetchrow.await_args.args[0]
        assert "LOWER(canonical_name) = LOWER($1)" in sql
        assert "(metadata->>'merged_into') IS NULL" in sql

    async def test_returns_none_when_not_found(self, pool: AsyncMock) -> None:
        pool.fetchrow = AsyncMock(return_value=None)

        result = await entity_find_by_canonical(pool, "Alice", "person")

        assert result is None


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
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": None, "identifier": None},  # both missing
            {"identifier": ""},  # empty identifier
            {"identifier": "   "},  # whitespace identifier
            {"name": ""},  # empty name (positional below)
        ],
    )
    async def test_empty_or_missing_input_raises_before_query(
        self, pool: AsyncMock, kwargs: dict
    ) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            if "name" in kwargs and kwargs["name"] == "" and "identifier" not in kwargs:
                await entity_resolve(pool, "")
            else:
                await entity_resolve(pool, **kwargs)
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

    async def test_fact_count_in_return_shape(self, pool: AsyncMock) -> None:
        row = _entity_mock_row(
            uuid.UUID(str(uuid.uuid4())), "Dave", match_type="exact", fact_count=3
        )
        pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(pool, "Dave")
        assert "fact_count" in results[0]
        assert isinstance(results[0]["fact_count"], int)


class TestEntityResolveSchema:
    """Verify MCP tool schema is tightened so strict-validation runtimes can
    reject null/empty identifier inputs before they reach the server."""

    async def _get_resolve_tool(self):
        from unittest.mock import MagicMock

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


class TestEntityMergeDispatch:
    """The legacy memory callable delegates to Relationship's sole authority."""

    async def test_delegates_and_preserves_return_shape(self) -> None:
        merge_result = MagicMock(
            kept_entity_id=TARGET_UUID,
            tombstoned_entity_id=SOURCE_UUID,
            aliases_added=2,
            rebind_id=uuid.uuid4(),
            failed_schemas=("finance",),
            receipts=(
                {
                    "target_schema": "finance",
                    "status": "failed",
                    "references_rebound": 0,
                    "error_class": "RuntimeError",
                },
            ),
        )
        authority = AsyncMock(return_value=merge_result)

        with patch(
            "butlers.tools.relationship.entity_merge.merge_entity_pair",
            new=authority,
        ):
            result = await entity_merge(
                MagicMock(name="relationship_pool"),
                SOURCE_ID,
                TARGET_ID,
                extra_pools=[MagicMock()],
                chronicler_pool=MagicMock(),
            )

        authority.assert_awaited_once()
        assert result["target_entity_id"] == TARGET_ID
        assert result["source_entity_id"] == SOURCE_ID
        assert result["aliases_added"] == 2
        assert result["failed_schemas"] == ["finance"]
        assert result["rebind_status"] == "failed"
        assert set(result) == {
            "target_entity_id",
            "source_entity_id",
            "facts_repointed",
            "facts_superseded",
            "edge_facts_repointed",
            "edge_facts_superseded",
            "aliases_added",
            "rebind_id",
            "failed_schemas",
            "rebind_status",
            "receipts",
        }


# ---------------------------------------------------------------------------
# Subscriber-local episode helper
# ---------------------------------------------------------------------------

EPISODE_UUID_1 = uuid.UUID("eeeeeeee-1111-1111-1111-eeeeeeeeeeee")
EPISODE_UUID_2 = uuid.UUID("ffffffff-2222-2222-2222-ffffffffffff")


def _ep_row(episode_id: uuid.UUID, role: str = "participant") -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"episode_id": episode_id, "role": role}[k]
    return row


def _make_chronicler_pool(
    *,
    src_ep_rows: list,
    tgt_ep_rows: list,
) -> tuple[MagicMock, AsyncMock]:
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=[src_ep_rows, tgt_ep_rows])
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


class TestRePointEpisodeEntities:
    """Unit tests for _repoint_episode_entities helper (6.1, 6.3, 6.4)."""

    # NOTE: unshared->UPDATE and shared->DELETE dedup are both exercised together by
    # test_mixed_episodes_update_and_delete below; the dedicated single-branch
    # variants (with SQL-position arg asserts) were folded into it.

    async def test_dedup_promotes_higher_precedence_role(self) -> None:
        """When src has 'owner' and tgt has 'participant', tgt row role is promoted to 'owner'."""
        ch_pool, ch_conn = _make_chronicler_pool(
            src_ep_rows=[_ep_row(EPISODE_UUID_1, "owner")],
            tgt_ep_rows=[_ep_row(EPISODE_UUID_1, "participant")],
        )
        await _repoint_episode_entities(ch_pool, SOURCE_UUID, TARGET_UUID)

        # The role-promotion UPDATE should appear before the DELETE.
        # Now batched: one UPDATE per distinct promoted role, not per episode.
        role_updates = [
            c
            for c in ch_conn.execute.call_args_list
            if "UPDATE chronicler.episode_entities SET role" in c[0][0]
        ]
        assert len(role_updates) == 1, "Expected a batched role-promotion UPDATE"
        args = role_updates[0][0]
        assert args[1] == "owner"  # promoted role (higher precedence wins)
        assert args[2] == TARGET_UUID  # WHERE entity_id = $2
        assert EPISODE_UUID_1 in args[3]  # episode_id in ANY($3)

    async def test_dedup_no_role_update_when_target_already_higher(self) -> None:
        """When tgt already has 'owner' and src has 'participant', no role UPDATE is issued."""
        ch_pool, ch_conn = _make_chronicler_pool(
            src_ep_rows=[_ep_row(EPISODE_UUID_1, "participant")],
            tgt_ep_rows=[_ep_row(EPISODE_UUID_1, "owner")],
        )
        await _repoint_episode_entities(ch_pool, SOURCE_UUID, TARGET_UUID)

        role_updates = [
            c
            for c in ch_conn.execute.call_args_list
            if "UPDATE chronicler.episode_entities SET role" in c[0][0]
        ]
        assert role_updates == [], "No role UPDATE needed when target already has higher precedence"

    async def test_mixed_episodes_update_and_delete(self) -> None:
        """Source has two episodes; one shared with target (delete), one unique (update)."""
        ch_pool, ch_conn = _make_chronicler_pool(
            src_ep_rows=[_ep_row(EPISODE_UUID_1, "organizer"), _ep_row(EPISODE_UUID_2)],
            tgt_ep_rows=[_ep_row(EPISODE_UUID_1, "participant")],  # EPISODE_UUID_1 shared
        )
        await _repoint_episode_entities(ch_pool, SOURCE_UUID, TARGET_UUID)

        update_calls = [
            c
            for c in ch_conn.execute.call_args_list
            if "UPDATE chronicler.episode_entities SET entity_id" in c[0][0]
        ]
        delete_calls = [
            c
            for c in ch_conn.execute.call_args_list
            if "DELETE FROM chronicler.episode_entities" in c[0][0]
        ]
        assert len(update_calls) == 1  # EPISODE_UUID_2 re-pointed
        assert len(delete_calls) == 1  # EPISODE_UUID_1 deduped

    async def test_no_source_rows_is_noop(self) -> None:
        """When source has no episode_entities rows, no writes are issued.

        The derived episodes.entity_id column was dropped (bu-cfsgy), so with no
        source join-table rows the helper early-returns without any UPDATE/DELETE.
        """
        ch_pool, ch_conn = _make_chronicler_pool(src_ep_rows=[], tgt_ep_rows=[])
        await _repoint_episode_entities(ch_pool, SOURCE_UUID, TARGET_UUID)

        ep_entity_updates = [
            c
            for c in ch_conn.execute.call_args_list
            if "UPDATE chronicler.episode_entities SET entity_id" in c[0][0]
        ]
        ep_deletes = [
            c
            for c in ch_conn.execute.call_args_list
            if "DELETE FROM chronicler.episode_entities" in c[0][0]
        ]
        derived_updates = [
            c
            for c in ch_conn.execute.call_args_list
            if "UPDATE chronicler.episodes SET entity_id" in c[0][0]
        ]
        assert ep_entity_updates == []
        assert ep_deletes == []
        assert derived_updates == []  # derived column was dropped — no UPDATE


# Cross-schema episode mutation is no longer part of entity_merge. The
# subscriber-local helper remains covered above and is invoked by entity_rebind.


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


# MCP tool — relationship authority dispatch
# ---------------------------------------------------------------------------


def _register_memory_entity_merge_tool(mod, *, entity_merge_result):
    """Register memory tools with a controllable entity_merge and capture the closure."""

    mcp = MagicMock()
    registered: dict[str, object] = {}

    def capture_tool():
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp.tool.side_effect = capture_tool

    fake_entities = MagicMock()
    fake_entities.entity_merge = AsyncMock(return_value=entity_merge_result)
    # The closure does `from butlers.modules.memory.tools import entities as _entities`,
    # which resolves `entities` as an attribute on the parent package object — wire
    # our fake there so `_entities.entity_merge` is the controllable AsyncMock.
    fake_tools_pkg = MagicMock()
    fake_tools_pkg.entities = fake_entities

    async def _run():
        with patch.dict(
            "sys.modules",
            {
                "butlers.modules.memory.tools": fake_tools_pkg,
                "butlers.modules.memory.tools.writing": MagicMock(),
                "butlers.modules.memory.tools.reading": MagicMock(),
                "butlers.modules.memory.tools.feedback": MagicMock(),
                "butlers.modules.memory.tools.management": MagicMock(),
                "butlers.modules.memory.tools.context": MagicMock(),
                "butlers.modules.memory.tools.preferences": MagicMock(),
                "butlers.modules.memory.tools.consolidation": MagicMock(),
                "butlers.modules.memory.consolidation": MagicMock(),
                "butlers.modules.memory.reembedding": MagicMock(),
                "butlers.modules.memory.tools.entities": fake_entities,
            },
        ):
            await mod.register_tools(mcp=mcp, config=None, db=mod._db, butler_name="memory")
        return registered, fake_entities

    return _run


class TestMemoryEntityMergeMCPDispatch:
    """The MCP surface dispatches through the relationship-scoped pool."""

    async def test_mcp_tool_passes_relationship_pool_to_authority(self) -> None:
        from butlers.modules.memory import MemoryModule

        mod = MemoryModule()
        fake_db = MagicMock()
        fake_db.pool = MagicMock(name="memory_pool")
        mod._db = fake_db
        relationship_pool = MagicMock(name="relationship_pool")
        expected = {"target_entity_id": TARGET_ID}
        run = _register_memory_entity_merge_tool(mod, entity_merge_result=expected)

        with patch.object(
            mod,
            "_get_or_create_relationship_pool",
            new=AsyncMock(return_value=relationship_pool),
        ):
            registered, fake_entities = await run()
            result = await registered["memory_entity_merge"](
                source_entity_id=SOURCE_ID,
                target_entity_id=TARGET_ID,
            )

        assert result == expected
        fake_entities.entity_merge.assert_awaited_once_with(
            relationship_pool,
            SOURCE_ID,
            TARGET_ID,
        )

    async def test_mcp_tool_fails_closed_without_relationship_authority(self) -> None:
        from butlers.modules.memory import MemoryModule

        mod = MemoryModule()
        mod._db = MagicMock()
        run = _register_memory_entity_merge_tool(mod, entity_merge_result=None)
        with patch.object(
            mod,
            "_get_or_create_relationship_pool",
            new=AsyncMock(return_value=None),
        ):
            registered, fake_entities = await run()
            with pytest.raises(RuntimeError, match="authority is unavailable"):
                await registered["memory_entity_merge"](
                    source_entity_id=SOURCE_ID,
                    target_entity_id=TARGET_ID,
                )

        fake_entities.entity_merge.assert_not_awaited()


class TestRetractFactsOnConn:
    """bu-j820n.2: forget/tombstone analogue of ``_repoint_facts_on_conn``.

    On a forget there is no survivor — every active fact referencing the entity
    (subject-side ``entity_id`` for gifts/loans/interactions/notes/life-events,
    and object-side ``object_entity_id`` for edge-facts) must be retracted
    (``validity = 'retracted'``), not left active and dangling on the tombstone.
    """

    async def test_issues_subject_and_object_retraction_updates(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=["UPDATE 3", "UPDATE 1"])

        result = await _retract_facts_on_conn(conn, ENTITY_UUID)

        # Two UPDATEs: subject-side (entity_id) then object-side (object_entity_id).
        assert conn.execute.await_count == 2
        subject_sql = conn.execute.await_args_list[0].args[0]
        object_sql = conn.execute.await_args_list[1].args[0]

        assert "UPDATE facts" in subject_sql
        assert "validity = 'retracted'" in subject_sql
        assert "entity_id = $1" in subject_sql
        assert "object_entity_id" not in subject_sql

        assert "UPDATE facts" in object_sql
        assert "validity = 'retracted'" in object_sql
        assert "object_entity_id = $1" in object_sql

        # Only active/fading rows are retracted (idempotent on re-forget;
        # already-superseded/expired/retracted rows are left alone). Fading
        # rows must be included too (bu-5ud8p.1) or they'd be silently
        # stranded on the tombstoned entity.
        assert "validity IN ('active', 'fading')" in subject_sql
        assert "validity IN ('active', 'fading')" in object_sql

        # Both UPDATEs are bound to the entity being forgotten.
        assert conn.execute.await_args_list[0].args[1] == ENTITY_UUID
        assert conn.execute.await_args_list[1].args[1] == ENTITY_UUID

        # Command tags are parsed into counts.
        assert result == {"facts_retracted": 3, "edge_facts_retracted": 1}

    async def test_parse_rowcount_handles_non_numeric_tags(self):
        # Real asyncpg tag.
        assert _parse_rowcount("UPDATE 5") == 5
        # Mock / unexpected return values degrade to 0 rather than raising.
        assert _parse_rowcount(None) == 0
        assert _parse_rowcount("UPDATE") == 0
        assert _parse_rowcount(MagicMock()) == 0


class TestEntityRebindReceipts:
    @staticmethod
    def _pending_pool(rebind_id: uuid.UUID, target_schema: str) -> AsyncMock:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "rebind_id": rebind_id,
                "source_entity_id": SOURCE_UUID,
                "target_entity_id": TARGET_UUID,
                "target_schema": target_schema,
                "references_rebound": 0,
                "status": "pending",
                "error_class": None,
            }
        )
        transaction = AsyncMock()
        transaction.__aenter__ = AsyncMock(return_value=None)
        transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=transaction)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = AsyncMock()
        pool.acquire = MagicMock(return_value=_acquire())
        return pool

    async def test_undefined_tables_are_classified_without_claiming_success(self) -> None:
        receipt = _rebind.EntityRebindReceipt(
            rebind_id=uuid.uuid4(),
            target_schema="finance",
            references_rebound=0,
            status="skipped_no_table",
            error_class="UndefinedTableError",
        )
        pool = self._pending_pool(receipt.rebind_id, "finance")
        with (
            patch.object(
                _rebind,
                "_run_optional_table_step",
                new=AsyncMock(side_effect=[(0, False), (0, False), (0, False), (0, False)]),
            ),
            patch.object(_rebind, "_write_receipt", new=AsyncMock(return_value=receipt)) as write,
        ):
            result = await _rebind.rebind_entity_references(
                pool,
                rebind_id=receipt.rebind_id,
                source_entity_id=SOURCE_UUID,
                target_entity_id=TARGET_UUID,
                target_schema="finance",
            )

        assert result.status == "skipped_no_table"
        assert write.await_args.kwargs["status"] == "skipped_no_table"
        assert write.await_args.kwargs["error_class"] == "UndefinedTableError"

    async def test_real_repoint_error_writes_failed_receipt_with_error_class(self) -> None:
        receipt = _rebind.EntityRebindReceipt(
            rebind_id=uuid.uuid4(),
            target_schema="finance",
            references_rebound=0,
            status="failed",
            error_class="RuntimeError",
        )
        with (
            patch.object(
                _rebind,
                "_run_optional_table_step",
                new=AsyncMock(side_effect=RuntimeError("write failed")),
            ),
            patch.object(_rebind, "_write_receipt", new=AsyncMock(return_value=receipt)) as write,
        ):
            result = await _rebind.rebind_entity_references(
                self._pending_pool(receipt.rebind_id, "finance"),
                rebind_id=receipt.rebind_id,
                source_entity_id=SOURCE_UUID,
                target_entity_id=TARGET_UUID,
                target_schema="finance",
            )

        assert result.status == "failed"
        assert write.await_args.kwargs["error_class"] == "RuntimeError"
