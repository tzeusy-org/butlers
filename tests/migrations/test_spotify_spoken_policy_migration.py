"""PostgreSQL-backed coverage for the Spotify spoken capture-only rule seed."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "030_switchboard_spotify_spoken_metadata_only.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_030", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(pool, operation: str) -> None:
    module = _load_migration()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_seeds_only_spotify_spoken_metadata_rule_and_downgrade_removes_it(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            """
            CREATE TABLE ingestion_rules (
                id UUID PRIMARY KEY,
                scope TEXT NOT NULL,
                rule_type TEXT NOT NULL,
                condition JSONB NOT NULL,
                action TEXT NOT NULL,
                priority INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_by TEXT NOT NULL
            )
            """
        )

        await _apply(pool, "upgrade")
        await _apply(pool, "upgrade")
        row = await pool.fetchrow(
            "SELECT scope, rule_type, condition, action, enabled FROM ingestion_rules"
        )
        assert row is not None
        assert dict(row) == {
            "scope": "global",
            "rule_type": "substring",
            "condition": {"pattern": "spotify:spoken:"},
            "action": "metadata_only",
            "enabled": True,
        }

        await _apply(pool, "downgrade")
        assert await pool.fetchval("SELECT count(*) FROM ingestion_rules") == 0
