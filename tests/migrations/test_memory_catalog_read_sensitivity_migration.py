"""Live-Postgres contract for the held catalog read ceiling."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_209_memory_catalog_read_sensitivity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "core_209_memory_catalog_read_sensitivity", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply_upgrade(pool: asyncpg.Pool) -> None:
    sqls: list[str] = []
    migration = _load_migration()
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = sqls.append
    with patch.object(migration, "op", mocked_op):
        migration.upgrade()
    for sql in sqls:
        await pool.execute(sql)


@pytest.fixture
async def runtime_config_pool(db):
    # core_209's predecessor: the real runtime_config without the read ceiling.
    pool = await db("core", revisions={"core": "core_208"})
    await _apply_upgrade(pool)
    return pool


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_rows_receive_normal_fail_closed_default(runtime_config_pool) -> None:
    await runtime_config_pool.execute("INSERT INTO runtime_config (butler_name) VALUES ('finance')")

    value = await runtime_config_pool.fetchval(
        "SELECT catalog_read_sensitivity FROM runtime_config WHERE butler_name = 'finance'"
    )

    assert value == "normal"


@pytest.mark.asyncio(loop_scope="session")
async def test_authority_vocabulary_is_database_constrained(runtime_config_pool) -> None:
    await runtime_config_pool.execute(
        "INSERT INTO runtime_config (butler_name, catalog_read_sensitivity) "
        "VALUES ('finance', 'internal')"
    )

    with pytest.raises(asyncpg.CheckViolationError):
        await runtime_config_pool.execute(
            "UPDATE runtime_config SET catalog_read_sensitivity = 'caller-invented'"
        )
