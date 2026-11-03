"""Regression tests for retiring the legacy core_121 permission default rows."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_db
from butlers.core.permissions import PERMISSION_DEFAULT_GRANTED
from butlers.testing.migration import load_migration
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_180_retire_legacy_permission_seeds.py"
)
_LEGACY_REASON = "seeded default (core_121)"


async def _apply(pool, fn_name: str) -> None:
    """Replay the exact SQL emitted by the migration against PostgreSQL."""
    module = load_migration(_MIGRATION_PATH, "core_180")
    sqls: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(module, "op", fake_op):
        getattr(module, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


def _switchboard_db(pool) -> MagicMock:
    """Minimal dashboard DB manager backed by the real migrated pool."""

    def _pool_for(name: str):
        assert name == "switchboard"
        return pool

    db = MagicMock()
    db.pool.side_effect = _pool_for
    return db


@pytest.mark.unit
def test_migration_chain_and_exact_provenance_guard() -> None:
    module = load_migration(_MIGRATION_PATH, "core_180")

    assert module.revision == "core_180"
    assert module.down_revision == "core_179"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert "to_regclass('public.permissions')" in module.RETIRE_LEGACY_PERMISSION_SEEDS_SQL
    assert f"reason = '{_LEGACY_REASON}'" in module.RETIRE_LEGACY_PERMISSION_SEEDS_SQL


@pytest.mark.unit
def test_downgrade_is_an_intentional_noop() -> None:
    module = load_migration(_MIGRATION_PATH, "core_180")
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
async def test_upgrade_removes_only_exact_legacy_rows_and_is_idempotent(db) -> None:
    # core_180's predecessor: the real core_121 seed rows are still present.
    pool = await db("core", revisions={"core": "core_179"})
    explicit_grant_at = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
    explicit_revoke_at = datetime(2026, 7, 23, 9, 1, tzinfo=UTC)
    near_match_at = datetime(2026, 7, 23, 9, 2, tzinfo=UTC)
    await pool.executemany(
        """
        INSERT INTO public.permissions (butler, permission, granted, reason, updated_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [
            ("legacy", "spawn", True, _LEGACY_REASON, datetime(2026, 7, 23, tzinfo=UTC)),
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
async def test_migrated_api_serializes_inherited_and_explicit_permission_states(db) -> None:
    pool = await db(
        "core",
        "switchboard",
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        schema="switchboard",
    )
    await pool.executemany(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ($1, 'http://test')",
        [("legacy",), ("grant",), ("revoke",)],
    )
    await pool.executemany(
        """
        INSERT INTO public.permissions (butler, permission, granted, reason, updated_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [
            ("legacy", "spawn", True, _LEGACY_REASON, datetime(2026, 7, 22, tzinfo=UTC)),
            ("grant", "spawn", True, "operator grant", datetime(2026, 7, 23, tzinfo=UTC)),
            ("revoke", "spawn", False, "operator revoke", datetime(2026, 7, 23, 1, tzinfo=UTC)),
        ],
    )
    await _apply(pool, "upgrade")

    app = create_app()
    app.dependency_overrides[get_db] = lambda: _switchboard_db(pool)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
