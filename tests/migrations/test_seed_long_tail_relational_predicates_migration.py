"""Tests for rel_026 — long-tail relational predicate seed migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: seeds 7 new relational/entity predicates.
3. downgrade() SQL shape: removes exactly the seeded rows.
4. Integration: seed rows present after upgrade.

Issue: bu-kgh8g
Parent epic: bu-5vpyh — relational-edges-single-home
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "026_seed_long_tail_relational_predicates.py"
)

pytestmark = pytest.mark.unit

_EXPECTED_PREDICATES = [
    "manages",
    "managed-by",
    "manages-property",
    "participant-of",
    "invited-by",
    "rental-agent",
    "rental-location",
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("rel_026", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sqls() -> list[str]:
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def _collect_downgrade_sqls() -> list[str]:
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    return sqls


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


def test_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "rel_026"
    assert mod.down_revision == "rel_025"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_upgrade_creates_schema_guard() -> None:
    sqls = _collect_upgrade_sqls()
    schema_stmts = [s for s in sqls if "CREATE SCHEMA" in s.upper()]
    assert schema_stmts, "upgrade() must emit CREATE SCHEMA IF NOT EXISTS relationship"
    assert any("relationship" in s for s in schema_stmts)


def test_downgrade_does_not_drop_table() -> None:
    sqls = _collect_downgrade_sqls()
    drop_stmts = [s for s in sqls if "DROP" in s.upper()]
    assert not drop_stmts, "downgrade() must only DELETE rows, not DROP anything; found: " + str(
        drop_stmts
    )


def test_new_predicates_count() -> None:
    """Migration must seed exactly 7 predicates (the complete long-tail triage set)."""
    mod = _load_migration()
    assert len(mod._NEW_PREDICATES) == 7, (
        f"Expected 7 new predicates in rel_026, got {len(mod._NEW_PREDICATES)}"
    )


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _provision_registry(pool) -> None:
    """Create minimal prerequisites: schema and entity_predicate_registry."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
            predicate   TEXT        NOT NULL PRIMARY KEY,
            kind        TEXT        NOT NULL CHECK (kind IN ('contact', 'relational', 'override')),
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


async def _run_upgrade(pool) -> None:
    sqls = _collect_upgrade_sqls()
    for sql in sqls:
        await pool.execute(sql)


async def _run_downgrade(pool) -> None:
    sqls = _collect_downgrade_sqls()
    for sql in sqls:
        await pool.execute(sql)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_all_predicates_present_after_upgrade(provisioned_postgres_pool) -> None:
    """All 7 new predicates are in entity_predicate_registry after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)

        for predicate in _EXPECTED_PREDICATES:
            row = await pool.fetchrow(
                "SELECT kind, object_kind FROM relationship.entity_predicate_registry "
                "WHERE predicate = $1",
                predicate,
            )
            assert row is not None, f"'{predicate}' must be present after upgrade"
            assert row["kind"] == "relational", (
                f"'{predicate}' must have kind='relational', got {row['kind']!r}"
            )
            assert row["object_kind"] == "entity", (
                f"'{predicate}' must have object_kind='entity', got {row['object_kind']!r}"
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_idempotent(provisioned_postgres_pool) -> None:
    """Running upgrade() twice does not raise and produces exactly one row per predicate."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)
        await _run_upgrade(pool)  # must not raise

        for predicate in _EXPECTED_PREDICATES:
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE predicate = $1",
                predicate,
            )
            assert count == 1, (
                f"Expected exactly 1 '{predicate}' row after idempotent re-upgrade, got {count}"
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_removes_seeded_rows(provisioned_postgres_pool) -> None:
    """Downgrade removes all 7 seeded rows."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        for predicate in _EXPECTED_PREDICATES:
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE predicate = $1",
                predicate,
            )
            assert count == 0, f"'{predicate}' must be absent after downgrade, got {count} rows"
