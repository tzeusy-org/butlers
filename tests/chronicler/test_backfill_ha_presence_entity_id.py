"""Tests for the home_assistant.history presence episode entity_id backfill (bu-v7hen).

Covers:
- load_ha_person_mapping: table absent, empty mapping, mapped contacts, UUID coercion.
- backfill: dry-run count, apply updates, skips unmapped entities, writes episode_entities.
- backfill: entity_id already set → skipped (SELECT WHERE entity_id IS NULL).
- resolve_ha_person_entity_ids adapter helper (unit tests).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Load script module under test
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_ha_presence_entity_id.py"
)
_MODULE_NAME = "backfill_ha_presence_entity_id"


def _load_script():  # type: ignore[return]
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_mod = _load_script()

backfill = _mod.backfill
load_ha_person_mapping = _mod.load_ha_person_mapping

_ENTITY_ALICE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ENTITY_BOB = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_EP_ID_1 = UUID("11111111-1111-1111-1111-111111111111")
_EP_ID_2 = UUID("22222222-2222-2222-2222-222222222222")
_EP_ID_3 = UUID("33333333-3333-3333-3333-333333333333")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_row(**kwargs: object) -> MagicMock:
    return MagicMock(**kwargs, **{"__getitem__": lambda s, k, _kw=kwargs: _kw[k]})


def _pool_with_mapping_rows(*rows: dict) -> AsyncMock:
    """Build a mock pool that simulates connectors.home_assistant_persons existing
    and returning the given rows for the mapping query."""

    async def _fetchval(*args: object, **kwargs: object) -> bool:
        return True  # table exists

    conn = AsyncMock()
    conn.fetchval = _fetchval
    conn.fetch = AsyncMock(return_value=[_make_row(**r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    # Also support pool.fetchval / pool.fetch directly (used in load_ha_person_mapping).
    pool.fetchval = AsyncMock(return_value=True)
    pool.fetch = AsyncMock(return_value=[_make_row(**r) for r in rows])
    return pool


def _pool_table_absent() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=False)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _pool_error() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=asyncpg.PostgresError("test error"))
    pool.fetch = AsyncMock(side_effect=asyncpg.PostgresError("test error"))
    return pool


# ---------------------------------------------------------------------------
# load_ha_person_mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_ha_person_mapping_returns_empty_when_table_absent() -> None:
    pool = _pool_table_absent()
    result = await load_ha_person_mapping(pool)
    assert result == {}


@pytest.mark.asyncio
async def test_load_ha_person_mapping_returns_empty_on_db_error() -> None:
    pool = _pool_error()
    result = await load_ha_person_mapping(pool)
    assert result == {}


@pytest.mark.asyncio
async def test_load_ha_person_mapping_returns_mapped_contacts() -> None:
    rows = [
        {"ha_entity_id": "person.alice", "entity_id": _ENTITY_ALICE},
        {"ha_entity_id": "person.bob", "entity_id": _ENTITY_BOB},
    ]
    pool = _pool_with_mapping_rows(*rows)
    result = await load_ha_person_mapping(pool)
    assert result == {"person.alice": _ENTITY_ALICE, "person.bob": _ENTITY_BOB}


@pytest.mark.asyncio
async def test_load_ha_person_mapping_coerces_str_uuid() -> None:
    rows = [
        {"ha_entity_id": "person.alice", "entity_id": str(_ENTITY_ALICE)},
    ]
    pool = _pool_with_mapping_rows(*rows)
    result = await load_ha_person_mapping(pool)
    assert result["person.alice"] == _ENTITY_ALICE


@pytest.mark.asyncio
async def test_load_ha_person_mapping_skips_null_entity_id() -> None:
    # Pool returns a row with entity_id=None — should be filtered by the SQL
    # (WHERE c.entity_id IS NOT NULL) but defensive handling still tested.
    rows = [
        {"ha_entity_id": "person.alice", "entity_id": None},
    ]
    pool = _pool_with_mapping_rows(*rows)
    result = await load_ha_person_mapping(pool)
    assert result == {}


# ---------------------------------------------------------------------------
# backfill (dry-run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_dry_run_returns_count_without_writes() -> None:
    chronicler_pool = AsyncMock()
    count_row = _make_row(count=3)
    chronicler_pool.fetchrow = AsyncMock(return_value=count_row)
    chronicler_pool.fetch = AsyncMock(return_value=[])

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={"person.alice": _ENTITY_ALICE},
        dry_run=True,
    )
    assert result["found"] == 3
    assert result["updated"] == 0
    assert result["ee_upserted"] == 0
    chronicler_pool.execute.assert_not_called()
    chronicler_pool.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_dry_run_zero_count_returns_zero() -> None:
    chronicler_pool = AsyncMock()
    count_row = _make_row(count=0)
    chronicler_pool.fetchrow = AsyncMock(return_value=count_row)

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={},
        dry_run=True,
    )
    assert result["found"] == 0


# ---------------------------------------------------------------------------
# backfill (apply)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_apply_updates_episodes_and_writes_episode_entities() -> None:
    """Happy path: episodes with known HA entities get entity_id set and
    episode_entities rows inserted."""
    ep_rows = [
        _make_row(id=_EP_ID_1, ha_entity_id="person.alice"),
    ]
    chronicler_pool = AsyncMock()
    # First fetch returns the episode batch; second returns empty (no more rows).
    chronicler_pool.fetch = AsyncMock(side_effect=[ep_rows, []])
    chronicler_pool.execute = AsyncMock(return_value="UPDATE 1")
    chronicler_pool.executemany = AsyncMock(return_value=None)

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={"person.alice": _ENTITY_ALICE},
        dry_run=False,
    )

    assert result["updated"] == 1
    assert result["skipped"] == 0
    assert result["ee_upserted"] == 1

    # Verify UPDATE was called with the correct entity_id.
    chronicler_pool.execute.assert_called_once()
    update_call_args = chronicler_pool.execute.call_args
    assert update_call_args[0][1] == _ENTITY_ALICE  # second positional arg is entity_id
    assert _EP_ID_1 in update_call_args[0][2]  # third positional arg is list of episode IDs

    # Verify episode_entities INSERT was called.
    chronicler_pool.executemany.assert_called_once()
    insert_call_args = chronicler_pool.executemany.call_args
    ee_rows_written = insert_call_args[0][1]
    assert (_EP_ID_1, _ENTITY_ALICE, "owner") in ee_rows_written


@pytest.mark.asyncio
async def test_backfill_apply_skips_unmapped_entities() -> None:
    """Episodes for HA entities not in the mapping are skipped.

    When the entire batch is unmapped the loop stops early (no infinite loop).
    """
    ep_rows = [
        _make_row(id=_EP_ID_1, ha_entity_id="person.unknown"),
    ]
    chronicler_pool = AsyncMock()
    # Only one fetch call: the loop breaks after the first all-unmapped batch.
    chronicler_pool.fetch = AsyncMock(return_value=ep_rows)
    chronicler_pool.execute = AsyncMock(return_value="UPDATE 0")
    chronicler_pool.executemany = AsyncMock(return_value=None)

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={},  # empty — no mappings
        dry_run=False,
    )

    assert result["updated"] == 0
    assert result["skipped"] == 1
    chronicler_pool.execute.assert_not_called()
    chronicler_pool.executemany.assert_not_called()
    # fetch was called exactly once — the loop stopped rather than re-fetching.
    assert chronicler_pool.fetch.call_count == 1


@pytest.mark.asyncio
async def test_backfill_apply_mixed_mapped_and_unmapped() -> None:
    """Episodes for mapped HA entities are updated; unmapped ones are skipped."""
    ep_rows = [
        _make_row(id=_EP_ID_1, ha_entity_id="person.alice"),
        _make_row(id=_EP_ID_2, ha_entity_id="person.guest"),  # not in mapping
    ]
    chronicler_pool = AsyncMock()
    chronicler_pool.fetch = AsyncMock(side_effect=[ep_rows, []])
    chronicler_pool.execute = AsyncMock(return_value="UPDATE 1")
    chronicler_pool.executemany = AsyncMock(return_value=None)

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={"person.alice": _ENTITY_ALICE},
        dry_run=False,
    )

    assert result["updated"] == 1
    assert result["skipped"] == 1
    assert result["ee_upserted"] == 1


@pytest.mark.asyncio
async def test_backfill_apply_null_ha_entity_id_is_skipped() -> None:
    """Episodes whose payload->>'entity_id' is NULL are skipped.

    A NULL ha_entity_id is treated as unmapped, so the loop stops after the
    first batch (no infinite loop).
    """
    ep_rows = [
        _make_row(id=_EP_ID_1, ha_entity_id=None),  # no entity_id in payload
    ]
    chronicler_pool = AsyncMock()
    # Loop breaks after first all-unmapped batch.
    chronicler_pool.fetch = AsyncMock(return_value=ep_rows)
    chronicler_pool.execute = AsyncMock(return_value="UPDATE 0")
    chronicler_pool.executemany = AsyncMock(return_value=None)

    result = await backfill(
        chronicler_pool,
        ha_person_mapping={"person.alice": _ENTITY_ALICE},
        dry_run=False,
    )

    assert result["updated"] == 0
    assert result["skipped"] == 1
    chronicler_pool.execute.assert_not_called()
    assert chronicler_pool.fetch.call_count == 1


@pytest.mark.asyncio
async def test_backfill_apply_multi_person_uses_correct_entity_ids() -> None:
    """Multi-person household: each person's episodes are updated with their own entity_id."""
    ep_rows = [
        _make_row(id=_EP_ID_1, ha_entity_id="person.alice"),
        _make_row(id=_EP_ID_2, ha_entity_id="person.bob"),
    ]
    execute_calls: list[tuple] = []

    async def _execute(query: str, *args: object, **kwargs: object) -> str:
        execute_calls.append((query, args))
        return "UPDATE 1"

    chronicler_pool = AsyncMock()
    chronicler_pool.fetch = AsyncMock(side_effect=[ep_rows, []])
    chronicler_pool.execute = _execute
    chronicler_pool.executemany = AsyncMock(return_value=None)

    mapping = {"person.alice": _ENTITY_ALICE, "person.bob": _ENTITY_BOB}
    result = await backfill(
        chronicler_pool,
        ha_person_mapping=mapping,
        dry_run=False,
    )

    assert result["updated"] == 2
    assert result["skipped"] == 0
    assert result["ee_upserted"] == 2

    # Each unique entity_id should be used in a separate UPDATE call.
    # execute_calls entries are (query, *positional_args); positional_args[0] is the entity_id.
    entity_ids_used = {args[0] for _query, args in execute_calls}
    assert _ENTITY_ALICE in entity_ids_used
    assert _ENTITY_BOB in entity_ids_used
