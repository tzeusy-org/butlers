"""Tests for scripts/backfill_episode_participants.py (bu-xuqyo).

Covers:
- Idempotent re-run: running the script twice produces the same final state (no duplicate rows).
- Dry-run: reports counts but writes no rows.
- Owner row written from episodes.entity_id.
- Participant rows written from upstream calendar_event_entities.
- Role-precedence collapse: an attendee who is also the owner gets role='owner' (not duplicated).
- Graceful degradation when calendar tables are absent (owner-only episode_entities).
- Episodes with missing schema or origin_instance_ref in payload are skipped.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the script under test via importlib (mirrors tests/scripts/ convention)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_episode_participants.py"
)
_MODULE_NAME = "backfill_episode_participants"


def _load_script():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()

fetch_participant_entity_ids = _mod.fetch_participant_entity_ids
_build_entity_role_map = _mod._build_entity_role_map
upsert_episode_entities = _mod.upsert_episode_entities
backfill = _mod.backfill
_quote_ident = _mod._quote_ident

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_OWNER_ID = uuid4()
_PARTICIPANT_A = uuid4()
_PARTICIPANT_B = uuid4()
_SCHEMA = "test_butler"
_ORIGIN_REF = "evt:test:2026-05-01T12:00:00Z"
_EPISODE_ID = uuid4()


class _AsyncCtx:
    """Minimal async context manager for pool.acquire() and conn.transaction()."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        return None


def _make_pool_for_fetch(
    *,
    instances_exists: bool = True,
    entities_exists: bool = True,
    participant_rows: list[dict] | None = None,
    raise_error: bool = False,
) -> AsyncMock:
    """Build a mock asyncpg.Pool for fetch_participant_entity_ids tests.

    ``fetchval`` is called twice: first for instances table, then for entities table.
    ``fetch`` is called once for the participant query.
    """
    conn = AsyncMock()

    if raise_error:
        conn.fetchval = AsyncMock(side_effect=asyncpg.PostgresError("test error"))
        conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("test error"))
    else:
        fetchval_responses = [instances_exists, entities_exists]
        conn.fetchval = AsyncMock(side_effect=fetchval_responses)

        if participant_rows is None:
            participant_rows = []

        mock_rows = []
        for row_dict in participant_rows:
            mock_row = MagicMock()
            mock_row.__getitem__ = MagicMock(side_effect=lambda k, d=row_dict: d[k])
            mock_rows.append(mock_row)
        conn.fetch = AsyncMock(return_value=mock_rows)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _make_episode_row(
    *,
    episode_id: UUID | None = None,
    schema: str | None = _SCHEMA,
    origin_instance_ref: str | None = _ORIGIN_REF,
    entity_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": episode_id or uuid4(),
        "schema": schema,
        "origin_instance_ref": origin_instance_ref,
        "entity_id": entity_id,
    }


def _make_pool_for_upsert() -> tuple[AsyncMock, list]:
    """Build a mock pool for upsert_episode_entities tests.

    Returns (pool, captured_rows) where captured_rows is populated by
    conn.executemany with the rows passed to INSERT.
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    captured: list = []

    async def _capture_executemany(sql: str, rows: list) -> None:
        if "INSERT INTO" in sql:
            captured.extend(rows)

    conn.executemany = AsyncMock(side_effect=_capture_executemany)
    conn.execute = AsyncMock()

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, captured


# ---------------------------------------------------------------------------
# Tests for fetch_participant_entity_ids
# ---------------------------------------------------------------------------


async def test_fetch_participants_returns_entity_ids_when_tables_present() -> None:
    """Happy path: calendar tables exist and participants are resolved."""
    pool = _make_pool_for_fetch(
        instances_exists=True,
        entities_exists=True,
        participant_rows=[
            {"entity_id": _PARTICIPANT_A},
            {"entity_id": _PARTICIPANT_B},
        ],
    )
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result is not None
    assert set(result) == {_PARTICIPANT_A, _PARTICIPANT_B}


async def test_fetch_participants_returns_none_when_instances_table_absent() -> None:
    """calendar_event_instances table absent → returns None (graceful degrade)."""
    pool = _make_pool_for_fetch(instances_exists=False, entities_exists=True)
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result is None


async def test_fetch_participants_returns_empty_when_entities_table_absent() -> None:
    """calendar_event_entities table absent → returns [] (owner-only fallback)."""
    pool = _make_pool_for_fetch(instances_exists=True, entities_exists=False)
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result == []


async def test_fetch_participants_returns_empty_list_when_no_attendees() -> None:
    """Tables exist but no attendee rows found → returns []."""
    pool = _make_pool_for_fetch(instances_exists=True, entities_exists=True, participant_rows=[])
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result == []


async def test_fetch_participants_postgres_error_returns_none() -> None:
    """PostgresError on any query → returns None without raising."""
    pool = _make_pool_for_fetch(raise_error=True)
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result is None


async def test_fetch_participants_coerces_string_uuid() -> None:
    """entity_id returned as str is coerced to UUID."""
    pool = _make_pool_for_fetch(
        instances_exists=True,
        entities_exists=True,
        participant_rows=[{"entity_id": str(_PARTICIPANT_A)}],
    )
    result = await fetch_participant_entity_ids(
        pool, schema=_SCHEMA, origin_instance_ref=_ORIGIN_REF
    )
    assert result is not None
    assert len(result) == 1
    assert result[0] == _PARTICIPANT_A
    assert isinstance(result[0], UUID)


# ---------------------------------------------------------------------------
# Tests for _build_entity_role_map
# ---------------------------------------------------------------------------


def test_build_entity_role_map_owner_only() -> None:
    """Owner with no participants → single row with role='owner'."""
    result = _build_entity_role_map(owner_id=_OWNER_ID, participant_ids=[])
    assert result == {_OWNER_ID: "owner"}


def test_build_entity_role_map_participants_only() -> None:
    """No owner but participants → all rows with role='participant'."""
    result = _build_entity_role_map(owner_id=None, participant_ids=[_PARTICIPANT_A, _PARTICIPANT_B])
    assert result == {_PARTICIPANT_A: "participant", _PARTICIPANT_B: "participant"}


def test_build_entity_role_map_owner_and_participants() -> None:
    """Owner + participants → owner row + participant rows."""
    result = _build_entity_role_map(
        owner_id=_OWNER_ID, participant_ids=[_PARTICIPANT_A, _PARTICIPANT_B]
    )
    assert result[_OWNER_ID] == "owner"
    assert result[_PARTICIPANT_A] == "participant"
    assert result[_PARTICIPANT_B] == "participant"
    assert len(result) == 3


def test_build_entity_role_map_role_precedence_collapse_owner_beats_participant() -> None:
    """When the owner appears in participant_ids, role='owner' wins (role-precedence collapse)."""
    result = _build_entity_role_map(
        owner_id=_OWNER_ID,
        participant_ids=[_OWNER_ID, _PARTICIPANT_A],  # owner listed as participant too
    )
    # owner_id appears exactly once with role='owner'.
    assert result[_OWNER_ID] == "owner"
    assert result[_PARTICIPANT_A] == "participant"
    assert len(result) == 2


def test_build_entity_role_map_no_owner_no_participants() -> None:
    """No owner and no participants → empty map."""
    result = _build_entity_role_map(owner_id=None, participant_ids=[])
    assert result == {}


# ---------------------------------------------------------------------------
# Tests for upsert_episode_entities
# ---------------------------------------------------------------------------


async def test_upsert_episode_entities_dry_run_writes_nothing() -> None:
    """Dry-run: returns 0 and does not call execute or executemany."""
    pool, captured = _make_pool_for_upsert()
    entity_role = {_OWNER_ID: "owner", _PARTICIPANT_A: "participant"}
    n = await upsert_episode_entities(
        pool, episode_id=_EPISODE_ID, entity_role=entity_role, dry_run=True
    )
    assert n == 0
    # pool.acquire must not be called in dry-run mode.
    pool.acquire.assert_not_called()
    assert captured == []


async def test_upsert_episode_entities_writes_rows() -> None:
    """Apply mode: DELETE is called then executemany with the correct rows."""
    pool, captured = _make_pool_for_upsert()
    entity_role = {_OWNER_ID: "owner", _PARTICIPANT_A: "participant"}
    n = await upsert_episode_entities(
        pool, episode_id=_EPISODE_ID, entity_role=entity_role, dry_run=False
    )
    assert n == 2
    # Rows are (episode_id, entity_id, role) triples.
    roles_by_entity = {eid: role for _, eid, role in captured}
    assert roles_by_entity[_OWNER_ID] == "owner"
    assert roles_by_entity[_PARTICIPANT_A] == "participant"


async def test_upsert_episode_entities_delete_then_insert() -> None:
    """DELETE is called before executemany to replace stale attendees."""
    pool, _ = _make_pool_for_upsert()
    entity_role = {_OWNER_ID: "owner"}
    await upsert_episode_entities(
        pool, episode_id=_EPISODE_ID, entity_role=entity_role, dry_run=False
    )
    conn = pool.acquire.return_value._obj
    # DELETE must be called before executemany.
    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c.args[0])]
    assert delete_calls, "DELETE FROM chronicler.episode_entities was not called"
    # executemany must also have been called (for the INSERT).
    conn.executemany.assert_called_once()


async def test_upsert_episode_entities_empty_entity_role_no_insert() -> None:
    """When entity_role is empty, DELETE runs but executemany is not called."""
    pool, captured = _make_pool_for_upsert()
    n = await upsert_episode_entities(pool, episode_id=_EPISODE_ID, entity_role={}, dry_run=False)
    assert n == 0
    assert captured == []
    conn = pool.acquire.return_value._obj
    conn.executemany.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for backfill (integration-style with mocked pool)
# ---------------------------------------------------------------------------


def _make_backfill_pool(
    *,
    episodes: list[dict[str, Any]],
    participant_rows_by_ref: dict[str, list[dict]] | None = None,
    instances_exists: bool = True,
    entities_exists: bool = True,
) -> tuple[AsyncMock, list]:
    """Build a mock pool for backfill() tests.

    ``pool.fetch`` returns the episodes list (for collect_episodes).
    ``pool.acquire`` returns a conn whose fetchval / fetch simulate the calendar
    table existence checks and participant query.

    Returns (pool, captured_insert_rows).
    """
    if participant_rows_by_ref is None:
        participant_rows_by_ref = {}

    captured: list = []

    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))

    # fetchval: return True/False based on which table is being checked.
    def _fetchval_side_effect(*args: Any, **kw: Any) -> bool:
        sql_or_schema = str(args)
        if "calendar_event_instances" in sql_or_schema:
            return instances_exists
        return entities_exists

    conn.fetchval = AsyncMock(side_effect=_fetchval_side_effect)

    async def _fetch_dispatch(sql: str, *args: Any) -> list:
        if "calendar_event_instances" in sql or "calendar_event_entities" in sql:
            # participant query: args[0] is origin_instance_ref
            ref = args[0] if args else None
            rows_dicts = participant_rows_by_ref.get(ref, [])
            mock_rows = []
            for d in rows_dicts:
                mr = MagicMock()
                mr.__getitem__ = MagicMock(side_effect=lambda k, _d=d: _d[k])
                mock_rows.append(mr)
            return mock_rows
        return []

    conn.fetch = AsyncMock(side_effect=_fetch_dispatch)

    async def _capture_executemany(sql: str, rows: list) -> None:
        if "INSERT INTO" in sql:
            captured.extend(rows)

    conn.executemany = AsyncMock(side_effect=_capture_executemany)
    conn.execute = AsyncMock()

    pool = AsyncMock()

    # pool.fetch is used by collect_episodes — returns the episodes list.
    episode_mock_rows = []
    for ep in episodes:
        mr = MagicMock()
        mr.__getitem__ = MagicMock(side_effect=lambda k, _ep=ep: _ep[k])
        episode_mock_rows.append(mr)
    pool.fetch = AsyncMock(return_value=episode_mock_rows)

    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, captured


async def test_backfill_dry_run_writes_nothing() -> None:
    """Dry-run: summary.rows_written == 0 and no DB writes occur."""
    ep_id = uuid4()
    episodes = [
        _make_episode_row(episode_id=ep_id, entity_id=_OWNER_ID),
    ]
    pool, captured = _make_backfill_pool(
        episodes=episodes,
        participant_rows_by_ref={_ORIGIN_REF: [{"entity_id": _PARTICIPANT_A}]},
    )
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=True)
    assert summary["rows_written"] == 0
    assert summary["total"] == 1
    assert captured == []


async def test_backfill_owner_row_written_from_episodes_entity_id() -> None:
    """Owner row (role='owner') is written from episodes.entity_id even with no participants."""
    ep_id = uuid4()
    episodes = [_make_episode_row(episode_id=ep_id, entity_id=_OWNER_ID)]
    pool, captured = _make_backfill_pool(
        episodes=episodes,
        participant_rows_by_ref={_ORIGIN_REF: []},
    )
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    assert summary["rows_written"] == 1
    roles_by_entity = {eid: role for _, eid, role in captured}
    assert roles_by_entity[_OWNER_ID] == "owner"


async def test_backfill_participant_rows_written_from_calendar_event_entities() -> None:
    """Participant rows are derived from calendar_event_entities via the join."""
    ep_id = uuid4()
    episodes = [_make_episode_row(episode_id=ep_id, entity_id=_OWNER_ID)]
    pool, captured = _make_backfill_pool(
        episodes=episodes,
        participant_rows_by_ref={
            _ORIGIN_REF: [{"entity_id": _PARTICIPANT_A}, {"entity_id": _PARTICIPANT_B}]
        },
    )
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    assert summary["rows_written"] == 3  # owner + 2 participants
    roles_by_entity = {eid: role for _, eid, role in captured}
    assert roles_by_entity[_OWNER_ID] == "owner"
    assert roles_by_entity[_PARTICIPANT_A] == "participant"
    assert roles_by_entity[_PARTICIPANT_B] == "participant"


async def test_backfill_role_precedence_collapse_owner_beats_participant() -> None:
    """When the owner is also listed as a calendar attendee, role='owner' wins."""
    ep_id = uuid4()
    episodes = [_make_episode_row(episode_id=ep_id, entity_id=_OWNER_ID)]
    pool, captured = _make_backfill_pool(
        episodes=episodes,
        participant_rows_by_ref={
            _ORIGIN_REF: [
                {"entity_id": _OWNER_ID},  # owner also listed as attendee
                {"entity_id": _PARTICIPANT_A},
            ]
        },
    )
    await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    roles_by_entity = {eid: role for _, eid, role in captured}
    # owner_id appears exactly once with role='owner' (not duplicated).
    assert roles_by_entity[_OWNER_ID] == "owner"
    assert roles_by_entity[_PARTICIPANT_A] == "participant"
    assert len(roles_by_entity) == 2


async def test_backfill_idempotent_rerun_same_final_state() -> None:
    """Running the backfill twice produces the same final state.

    Concretely: the captured rows from the second run must be identical to those
    from the first run (DELETE-then-INSERT ensures idempotency at the SQL layer;
    this test verifies the Python layer drives identical inputs on each call).
    """
    ep_id = uuid4()
    episodes = [_make_episode_row(episode_id=ep_id, entity_id=_OWNER_ID)]
    participant_map = {_ORIGIN_REF: [{"entity_id": _PARTICIPANT_A}]}

    # First run.
    pool1, captured1 = _make_backfill_pool(
        episodes=episodes, participant_rows_by_ref=participant_map
    )
    await backfill(pool1, adapter="google_calendar.completed", dry_run=False)

    # Second run (fresh mock pool — same inputs).
    pool2, captured2 = _make_backfill_pool(
        episodes=episodes, participant_rows_by_ref=participant_map
    )
    await backfill(pool2, adapter="google_calendar.completed", dry_run=False)

    # Both runs must produce the same set of (entity_id, role) pairs.
    def _as_set(rows: list) -> set:
        return {(eid, role) for _, eid, role in rows}

    assert _as_set(captured1) == _as_set(captured2), (
        "Idempotent re-run must produce identical episode_entities rows on every run"
    )
    # Sanity: 2 rows (owner + participant).
    assert len(captured1) == 2


async def test_backfill_skips_episode_with_missing_schema() -> None:
    """Episode with no 'schema' in payload is skipped gracefully."""
    episodes = [_make_episode_row(schema=None, entity_id=_OWNER_ID)]
    pool, captured = _make_backfill_pool(episodes=episodes)
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    assert summary["skipped"] == 1
    assert summary["rows_written"] == 0
    assert captured == []


async def test_backfill_skips_episode_with_missing_origin_ref() -> None:
    """Episode with no 'origin_instance_ref' in payload is skipped gracefully."""
    episodes = [_make_episode_row(origin_instance_ref=None, entity_id=_OWNER_ID)]
    pool, captured = _make_backfill_pool(episodes=episodes)
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    assert summary["skipped"] == 1
    assert summary["rows_written"] == 0
    assert captured == []


async def test_backfill_no_episodes_returns_zero_counts() -> None:
    """No matching episodes → all counts are zero, no DB writes."""
    pool, captured = _make_backfill_pool(episodes=[])
    summary = await backfill(pool, adapter="google_calendar.completed", dry_run=False)
    assert summary["total"] == 0
    assert summary["rows_written"] == 0
    assert summary["skipped"] == 0
    assert captured == []


# ---------------------------------------------------------------------------
# Tests for _quote_ident
# ---------------------------------------------------------------------------


def test_quote_ident_wraps_in_double_quotes() -> None:
    """_quote_ident wraps a simple identifier in double quotes."""
    assert _quote_ident("test_butler") == '"test_butler"'


def test_quote_ident_rejects_unsafe_schema() -> None:
    """_quote_ident raises ValueError for identifiers with disallowed characters."""
    with pytest.raises(ValueError, match="Unsafe schema identifier"):
        _quote_ident("schema; DROP TABLE")
