"""Tests for scripts/backfill_point_event_entity_id.py — owner resolution.

Covers:
1. resolve_owner_entity_id returns UUID when public.entities has an owner row.
2. resolve_owner_entity_id returns UUID when the id is stored as a string.
3. resolve_owner_entity_id returns None when public.entities does not exist.
4. resolve_owner_entity_id returns None when the roles column is absent.
5. resolve_owner_entity_id returns None when no row has role 'owner'.
6. resolve_owner_entity_id returns None when the owner row's id IS NULL.
7. resolve_owner_entity_id returns None on PostgresError (graceful-None contract).
8. resolve_owner_entity_id returns None on unexpected id type.

Issue: bu-wukmy
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the script under test
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_point_event_entity_id.py"
)
_MODULE_NAME = "backfill_point_event_entity_id"


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
resolve_owner_entity_id = _mod.resolve_owner_entity_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCtx:
    """Minimal async context manager that yields obj."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_conn(
    *,
    entities_table_exists: bool = True,
    roles_column_exists: bool = True,
    owner_row: object = None,
) -> AsyncMock:
    """Return a mock asyncpg connection with configurable fetchval/fetchrow returns."""
    conn = AsyncMock()

    # fetchval is called twice: table-exists check, then column-exists check.
    conn.fetchval = AsyncMock(side_effect=[entities_table_exists, roles_column_exists])

    rec = None
    if owner_row is not None:
        rec = MagicMock(spec=asyncpg.Record)
        rec.__getitem__ = MagicMock(side_effect=lambda k: owner_row if k == "id" else None)
    conn.fetchrow = AsyncMock(return_value=rec)

    return conn


def _make_pool(conn: AsyncMock) -> AsyncMock:
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_uuid_when_owner_entity_found() -> None:
    """Returns the UUID when public.entities has a row with role 'owner'."""
    owner_id = uuid4()
    conn = _make_conn(owner_row=owner_id)
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result == owner_id


@pytest.mark.asyncio
async def test_returns_uuid_when_owner_id_stored_as_string() -> None:
    """Accepts owner id stored as a string and returns a UUID."""
    owner_id = uuid4()
    conn = _make_conn(owner_row=str(owner_id))
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result == owner_id


@pytest.mark.asyncio
async def test_returns_none_when_entities_table_missing() -> None:
    """Returns None gracefully when public.entities does not exist yet."""
    conn = _make_conn(entities_table_exists=False)
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_none_when_roles_column_absent() -> None:
    """Returns None gracefully when public.entities.roles column is missing."""
    conn = _make_conn(roles_column_exists=False)
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_none_when_no_owner_row() -> None:
    """Returns None when no entity has role 'owner'."""
    conn = _make_conn(owner_row=None)
    # fetchrow returns None (no row)
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_owner_id_is_null() -> None:
    """Returns None when the owner entity row exists but id IS NULL."""
    # Simulate a row where id is None — shouldn't happen in practice but
    # the graceful-None contract must hold.
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=[True, True])
    rec = MagicMock(spec=asyncpg.Record)
    rec.__getitem__ = MagicMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=rec)
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_postgres_error() -> None:
    """Returns None (and logs DEBUG) when the DB query raises PostgresError."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=asyncpg.PostgresError())
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_unexpected_id_type() -> None:
    """Returns None when the owner entity id is an unexpected type (e.g. int)."""
    conn = _make_conn(owner_row=42)  # type: ignore[arg-type]
    pool = _make_pool(conn)

    result = await resolve_owner_entity_id(pool)

    assert result is None
