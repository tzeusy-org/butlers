"""Regression tests for core_122 audit_log metadata/result/error migration.

Covers:
- Migration revision chain metadata is correct (down_revision → core_121).
- Upgrade adds metadata (JSONB), result (TEXT), error (TEXT) to public.audit_log.
- audit.append() round-trips metadata/result/error into the new columns.
- Omitting the new fields still inserts a row (backward compatible → NULLs).
- Downgrade drops the three columns.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.api.routers.audit import append

pytestmark = pytest.mark.integration

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"
_CORE_092 = _VERSIONS / "core_092_audit_log.py"
_CORE_122 = _VERSIONS / "core_122_audit_log_metadata_result_error.py"


def _load_migration(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool: asyncpg.Pool, mod, direction: str) -> None:
    """Capture op.execute() SQL emitted by upgrade()/downgrade() and run it."""
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, direction)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def audit_log_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        yield pool


def test_migration_revision_chain() -> None:
    mod = _load_migration("core_122", _CORE_122)
    assert mod.revision == "core_122"
    assert mod.down_revision == "core_121"


async def _upgrade_to_122(pool: asyncpg.Pool) -> None:
    await _apply(pool, _load_migration("core_092", _CORE_092), "upgrade")
    await _apply(pool, _load_migration("core_122", _CORE_122), "upgrade")


@pytest.mark.asyncio(loop_scope="session")
async def test_columns_added(audit_log_pool: asyncpg.Pool) -> None:
    """Upgrade adds metadata/result/error with the expected types."""
    pool = audit_log_pool
    await _upgrade_to_122(pool)

    rows = await pool.fetch(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'audit_log' "
        "AND column_name IN ('metadata', 'result', 'error')"
    )
    by_name = {r["column_name"]: r for r in rows}
    assert set(by_name) == {"metadata", "result", "error"}
    assert by_name["metadata"]["data_type"] == "jsonb"
    assert by_name["result"]["data_type"] == "text"
    assert by_name["error"]["data_type"] == "text"
    # All three are nullable so legacy inserts are unaffected.
    assert all(r["is_nullable"] == "YES" for r in by_name.values())


@pytest.mark.asyncio(loop_scope="session")
async def test_append_round_trips_new_fields(audit_log_pool: asyncpg.Pool) -> None:
    """append() persists metadata/result/error and they read back unchanged."""
    pool = audit_log_pool
    await _upgrade_to_122(pool)

    metadata = {"path": "/api/settings", "trigger_source": "dashboard"}
    row_id = await append(
        pool,
        "owner",
        "setting_change",
        target="butler:qa",
        note="changed threshold",
        metadata=metadata,
        result="success",
        error=None,
    )

    row = await pool.fetchrow(
        "SELECT metadata, result, error FROM public.audit_log WHERE id = $1",
        row_id,
    )
    assert row is not None
    # asyncpg returns JSONB as a JSON string unless a codec is registered.
    stored_metadata = row["metadata"]
    if isinstance(stored_metadata, str):
        stored_metadata = json.loads(stored_metadata)
    assert stored_metadata == metadata
    assert row["result"] == "success"
    assert row["error"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_append_without_new_fields_inserts_nulls(audit_log_pool: asyncpg.Pool) -> None:
    """Omitting the new fields stores SQL NULLs (backward compatible)."""
    pool = audit_log_pool
    await _upgrade_to_122(pool)

    row_id = await append(pool, "owner", "setting_change")
    row = await pool.fetchrow(
        "SELECT metadata, result, error FROM public.audit_log WHERE id = $1",
        row_id,
    )
    assert row is not None
    assert row["metadata"] is None
    assert row["result"] is None
    assert row["error"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_downgrade_drops_new_columns(audit_log_pool: asyncpg.Pool) -> None:
    """Downgrade removes metadata/result/error, leaving the base table intact."""
    pool = audit_log_pool
    await _upgrade_to_122(pool)
    await _apply(pool, _load_migration("core_122", _CORE_122), "downgrade")

    remaining = await pool.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'audit_log' "
        "AND column_name IN ('metadata', 'result', 'error')"
    )
    assert remaining == []

    # Base table still works post-downgrade.
    base_id = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action) VALUES ($1, $2) RETURNING id",
        "owner",
        "setting_change",
    )
    assert isinstance(base_id, int)
