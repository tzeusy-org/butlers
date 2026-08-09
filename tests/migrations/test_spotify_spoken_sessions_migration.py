"""PostgreSQL-backed coverage for core_195 Spotify spoken-session evidence."""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_195_spotify_spoken_sessions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_195", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(
    pool,
    operation: str,
    *,
    connector_role: str | None = None,
    chronicler_role: str | None = None,
) -> None:
    module = _load_migration()
    if connector_role is not None:
        module._CONNECTOR_ROLE = connector_role
    if chronicler_role is not None:
        module._CHRONICLER_ROLE = chronicler_role
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
async def test_upgrade_guards_absent_roles_and_creates_bounded_evidence_table(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE SCHEMA connectors")
        missing_connector_role = f"core_195_missing_connector_{uuid.uuid4().hex[:12]}"
        missing_chronicler_role = f"core_195_missing_chronicler_{uuid.uuid4().hex[:12]}"

        await _apply(
            pool,
            "upgrade",
            connector_role=missing_connector_role,
            chronicler_role=missing_chronicler_role,
        )

        table_exists = await pool.fetchval(
            "SELECT to_regclass('connectors.spotify_spoken_sessions') IS NOT NULL"
        )
        assert table_exists is True
        columns = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'connectors' AND table_name = 'spotify_spoken_sessions'
            """
        )
        names = {row["column_name"] for row in columns}
        assert {"content_kind", "episode_id", "metadata", "started_at", "ended_at"} <= names
        assert "raw_payload" not in names


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_grants_only_expected_privileges_and_downgrade_removes_table(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE SCHEMA connectors")
        connector_role = f"core_195_connector_{uuid.uuid4().hex[:12]}"
        chronicler_role = f"core_195_chronicler_{uuid.uuid4().hex[:12]}"
        await pool.execute(f'CREATE ROLE "{connector_role}"')
        await pool.execute(f'CREATE ROLE "{chronicler_role}"')

        await _apply(
            pool,
            "upgrade",
            connector_role=connector_role,
            chronicler_role=chronicler_role,
        )
        await _apply(
            pool,
            "upgrade",
            connector_role=connector_role,
            chronicler_role=chronicler_role,
        )

        connector_privileges = await pool.fetchrow(
            """
            SELECT
                has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'SELECT') AS can_select,
                has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'INSERT') AS can_insert,
                has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'UPDATE') AS can_update,
                has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'DELETE') AS can_delete
            """,
            connector_role,
        )
        assert connector_privileges is not None
        assert all(connector_privileges.values())
        assert (
            await pool.fetchval(
                "SELECT has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'SELECT')",
                chronicler_role,
            )
            is True
        )
        assert (
            await pool.fetchval(
                "SELECT has_table_privilege($1, 'connectors.spotify_spoken_sessions', 'INSERT')",
                chronicler_role,
            )
            is False
        )

        await _apply(pool, "downgrade")
        assert (
            await pool.fetchval("SELECT to_regclass('connectors.spotify_spoken_sessions') IS NULL")
            is True
        )
