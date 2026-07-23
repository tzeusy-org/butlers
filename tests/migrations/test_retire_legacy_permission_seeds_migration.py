"""Regression tests for retiring the legacy core_121 permission default rows."""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.routers.permissions import _get_db_manager
from butlers.core.permissions import PERMISSION_DEFAULT_GRANTED

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_180_retire_legacy_permission_seeds.py"
)
_LEGACY_REASON = "seeded default (core_121)"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_180", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(pool, fn_name: str) -> None:
    """Replay the exact SQL emitted by the migration against PostgreSQL."""
    module = _load_migration()
    sqls: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(module, "op", fake_op):
        getattr(module, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


async def _provision_permissions(pool) -> None:
    await pool.execute(
        """
        CREATE TABLE public.permissions (
            butler TEXT NOT NULL,
            permission TEXT NOT NULL,
            granted BOOLEAN NOT NULL,
            reason TEXT,
            updated_at TIMESTAMPTZ,
            PRIMARY KEY (butler, permission)
        )
        """
    )


class _SwitchboardDb:
    """Minimal dashboard DB manager backed by the real migrated pool."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def pool(self, name: str):
        assert name == "switchboard"
        return self._pool


@pytest.mark.unit
def test_migration_chain_and_exact_provenance_guard() -> None:
    module = _load_migration()

    assert module.revision == "core_180"
    assert module.down_revision == "core_179"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert "to_regclass('public.permissions')" in module.RETIRE_LEGACY_PERMISSION_SEEDS_SQL
    assert f"reason = '{_LEGACY_REASON}'" in module.RETIRE_LEGACY_PERMISSION_SEEDS_SQL


@pytest.mark.unit
def test_downgrade_is_an_intentional_noop() -> None:
    module = _load_migration()
    fake_op = MagicMock()

    with patch.object(module, "op", fake_op):
        module.downgrade()

    fake_op.execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_noops_when_permissions_table_is_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _apply(pool, "upgrade")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_removes_only_exact_legacy_rows_and_is_idempotent(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _provision_permissions(pool)
        explicit_grant_at = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
        explicit_revoke_at = datetime(2026, 7, 23, 9, 1, tzinfo=UTC)
        near_match_at = datetime(2026, 7, 23, 9, 2, tzinfo=UTC)
        await pool.executemany(
            """
            INSERT INTO public.permissions (butler, permission, granted, reason, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [
                ("legacy", "spawn", True, _LEGACY_REASON, None),
                ("grant", "spawn", True, "operator grant", explicit_grant_at),
                ("revoke", "spawn", False, "operator revoke", explicit_revoke_at),
                ("near-match", "spawn", True, f"{_LEGACY_REASON} ", near_match_at),
            ],
        )

        await _apply(pool, "upgrade")
        first_rows = await pool.fetch(
            """
            SELECT butler, permission, granted, reason, updated_at
            FROM public.permissions
            ORDER BY butler
            """
        )
        await _apply(pool, "upgrade")
        second_rows = await pool.fetch(
            """
            SELECT butler, permission, granted, reason, updated_at
            FROM public.permissions
            ORDER BY butler
            """
        )

    expected = [
        ("grant", "spawn", True, "operator grant", explicit_grant_at),
        ("near-match", "spawn", True, f"{_LEGACY_REASON} ", near_match_at),
        ("revoke", "spawn", False, "operator revoke", explicit_revoke_at),
    ]
    assert [tuple(row.values()) for row in first_rows] == expected
    assert [tuple(row.values()) for row in second_rows] == expected


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_migrated_api_serializes_inherited_and_explicit_permission_states(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _provision_permissions(pool)
        await pool.execute("CREATE TABLE public.butler_registry (name TEXT PRIMARY KEY)")
        await pool.executemany(
            "INSERT INTO public.butler_registry (name) VALUES ($1)",
            [("legacy",), ("grant",), ("revoke",)],
        )
        await pool.executemany(
            """
            INSERT INTO public.permissions (butler, permission, granted, reason, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [
                ("legacy", "spawn", True, _LEGACY_REASON, None),
                ("grant", "spawn", True, "operator grant", datetime(2026, 7, 23, tzinfo=UTC)),
                ("revoke", "spawn", False, "operator revoke", datetime(2026, 7, 23, 1, tzinfo=UTC)),
            ],
        )
        await _apply(pool, "upgrade")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: _SwitchboardDb(pool)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/permissions")
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200
    cells = response.json()["data"]["cells"]
    legacy = cells["legacy"]["spawn"]
    assert legacy == {
        "granted": PERMISSION_DEFAULT_GRANTED,
        "reason": None,
        "updated_at": None,
        "inherited": True,
    }
    assert cells["grant"]["spawn"]["granted"] is True
    assert cells["grant"]["spawn"]["reason"] == "operator grant"
    assert cells["grant"]["spawn"]["inherited"] is False
    assert cells["revoke"]["spawn"]["granted"] is False
    assert cells["revoke"]["spawn"]["reason"] == "operator revoke"
    assert cells["revoke"]["spawn"]["inherited"] is False
