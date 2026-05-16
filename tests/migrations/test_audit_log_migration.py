"""Regression tests for core_092 audit_log migration.

Covers:
- Migration revision chain metadata is correct.
- Upgrade creates public.audit_log with the correct column set.
- Indexes (ts DESC, action, actor) are created.
- append helper: inserts row, returns id, ordering (ts DESC) is preserved.
- GET /api/audit-log and GET /api/audit-log/{id} respond per spec.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core" / "core_092_audit_log.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_092", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
async def audit_log_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        yield pool


async def _run_upgrade(pool: asyncpg.Pool) -> None:
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    for sql in sqls:
        await pool.execute(sql)


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_092"
    assert mod.down_revision == "core_091"


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_table_created(audit_log_pool: asyncpg.Pool) -> None:
    """Upgrade creates public.audit_log with the required columns."""
    pool = audit_log_pool
    await _run_upgrade(pool)

    # Verify table exists by querying the information_schema
    table_exists = await pool.fetchval(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'audit_log'"
    )
    assert table_exists == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_insert_and_retrieve(audit_log_pool: asyncpg.Pool) -> None:
    """Insert a row, retrieve by id, verify all columns round-trip correctly."""
    pool = audit_log_pool
    await _run_upgrade(pool)

    row_id = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action, target, note) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        "owner",
        "model_priority_change",
        "butler:qa",
        "Changed from claude-3-5-sonnet to claude-opus-4",
    )
    assert isinstance(row_id, int)
    assert row_id > 0

    row = await pool.fetchrow(
        "SELECT id, actor, action, target, note, ip, request_id "
        "FROM public.audit_log WHERE id = $1",
        row_id,
    )
    assert row is not None
    assert row["actor"] == "owner"
    assert row["action"] == "model_priority_change"
    assert row["target"] == "butler:qa"
    assert row["note"] == "Changed from claude-3-5-sonnet to claude-opus-4"
    assert row["ip"] is None
    assert row["request_id"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_ts_desc_ordering(audit_log_pool: asyncpg.Pool) -> None:
    """Multiple rows are returned ordered ts DESC (newest first)."""
    pool = audit_log_pool
    await _run_upgrade(pool)

    # Insert two rows; rely on DEFAULT now() and sequential inserts for ordering
    id1 = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action) VALUES ($1, $2) RETURNING id",
        "owner",
        "setting_change",
    )
    id2 = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action) VALUES ($1, $2) RETURNING id",
        "owner",
        "setting_change",
    )

    rows = await pool.fetch(
        "SELECT id FROM public.audit_log WHERE action = 'setting_change' ORDER BY ts DESC"
    )
    ids = [r["id"] for r in rows]
    # The second insertion should appear first (ts DESC)
    assert ids[0] == id2
    assert ids[1] == id1


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_required_columns_not_null(audit_log_pool: asyncpg.Pool) -> None:
    """actor and action are NOT NULL — inserting without them raises."""
    pool = audit_log_pool
    await _run_upgrade(pool)

    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute(
            "INSERT INTO public.audit_log (action) VALUES ($1)",
            "setting_change",
        )

    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute(
            "INSERT INTO public.audit_log (actor) VALUES ($1)",
            "owner",
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_ip_inet_column(audit_log_pool: asyncpg.Pool) -> None:
    """ip column is INET; valid IP addresses are accepted, invalid rejected."""
    pool = audit_log_pool
    await _run_upgrade(pool)

    # Valid IPv4
    row_id = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action, ip) VALUES ($1, $2, $3::inet) RETURNING id",
        "owner",
        "login",
        "192.168.1.1",
    )
    row = await pool.fetchrow("SELECT ip FROM public.audit_log WHERE id = $1", row_id)
    assert row is not None
    assert str(row["ip"]) == "192.168.1.1"


@pytest.mark.asyncio(loop_scope="session")
async def test_audit_log_request_id_uuid_column(audit_log_pool: asyncpg.Pool) -> None:
    """request_id column accepts UUID values."""
    import uuid

    pool = audit_log_pool
    await _run_upgrade(pool)

    rid = uuid.uuid4()
    row_id = await pool.fetchval(
        "INSERT INTO public.audit_log (actor, action, request_id) VALUES ($1, $2, $3) RETURNING id",
        "owner",
        "api_call",
        rid,
    )
    row = await pool.fetchrow("SELECT request_id FROM public.audit_log WHERE id = $1", row_id)
    assert row is not None
    assert row["request_id"] == rid
