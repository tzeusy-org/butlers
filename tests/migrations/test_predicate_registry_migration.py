"""Tests for rel_014 predicate_registry Alembic migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: CREATE SCHEMA, CREATE TABLE with expected columns,
   seed rows for all three predicate families.
3. downgrade() SQL shape: DROP TABLE, does NOT drop the schema.
4. Seed data: correct predicate count by kind, required predicates present,
   ON CONFLICT DO NOTHING for idempotency.
5. Integration: table exists after upgrade; seed rows present; clean downgrade.
6. Downgrade is reversible (re-upgrade succeeds after downgrade).

Issue: bu-hlovw
Parent epic: bu-ao6uh — entity-redesign backend contracts
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
    / "014_predicate_registry.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("rel_014", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sqls() -> list[str]:
    """Run upgrade() with op mocked; return SQL strings."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def _collect_downgrade_sqls() -> list[str]:
    """Run downgrade() with op mocked; return SQL strings."""
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


class TestMigrationFileAndChain:
    """Revision-chain contract test."""

    def test_revision_chain(self) -> None:
        """rel_014 -> rel_013 (entity_facts), no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "rel_014"
        assert mod.down_revision == "rel_013"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQLShape:
    """Verify upgrade() seeds the documented predicate content.

    Table/column shape and ON CONFLICT idempotency are exercised against a live
    DB by the integration column-set + idempotency tests; here we pin the
    seed-content spec (which predicates, by kind).
    """

    def test_contact_predicates_seeded(self) -> None:
        """All six contact predicates must be present in seed INSERTs."""
        sqls = _collect_upgrade_sqls()
        insert_stmts = [s for s in sqls if "INSERT INTO" in s.upper() and "predicate_registry" in s]
        all_text = " ".join(insert_stmts)
        contact_predicates = [
            "has-email",
            "has-phone",
            "has-handle",
            "has-address",
            "has-birthday",
            "has-website",
        ]
        for pred in contact_predicates:
            assert pred in all_text, f"Contact predicate '{pred}' must be seeded"

    def test_relational_predicates_seeded(self) -> None:
        """All relational predicates from the spec must be seeded."""
        sqls = _collect_upgrade_sqls()
        insert_stmts = [s for s in sqls if "INSERT INTO" in s.upper() and "predicate_registry" in s]
        all_text = " ".join(insert_stmts)
        relational_predicates = [
            "knows",
            "family-of",
            "partner-of",
            "parent-of",
            "child-of",
            "colleague-of",
            "friend-of",
            "co-attended",
            "purchased-from",
            "subscribed-to",
            "visited",
        ]
        for pred in relational_predicates:
            assert pred in all_text, f"Relational predicate '{pred}' must be seeded"

    def test_override_predicate_seeded(self) -> None:
        """dunbar_tier_override must be seeded (Phase 1 Amendment 6)."""
        sqls = _collect_upgrade_sqls()
        insert_stmts = [s for s in sqls if "INSERT INTO" in s.upper() and "predicate_registry" in s]
        all_text = " ".join(insert_stmts)
        assert "dunbar_tier_override" in all_text, (
            "Override predicate 'dunbar_tier_override' must be seeded"
        )

    def test_no_verified_by_predicate(self) -> None:
        """No verified-by predicate must be seeded (verified is a column, not a triple).

        Per spec §"Requirement: verified is a column, not a triple" — Scenario:
        No verification-triple predicate is registered.
        """
        sqls = _collect_upgrade_sqls()
        insert_stmts = [s for s in sqls if "INSERT INTO" in s.upper() and "predicate_registry" in s]
        all_text = " ".join(insert_stmts)
        assert "verified-by" not in all_text, (
            "'verified-by' must NOT be seeded; verified is a column on relationship.entity_facts"
        )

    def test_seed_count_by_kind(self) -> None:
        """Verify total and per-kind seed counts match the spec."""
        mod = _load_migration()
        contact_count = len(mod._CONTACT_PREDICATES)
        relational_count = len(mod._RELATIONAL_PREDICATES)
        override_count = len(mod._OVERRIDE_PREDICATES)

        assert contact_count == 6, f"Expected 6 contact predicates, got {contact_count}"
        assert relational_count == 11, f"Expected 11 relational predicates, got {relational_count}"
        assert override_count == 1, f"Expected 1 override predicate, got {override_count}"
        assert contact_count + relational_count + override_count == len(mod._ALL_PREDICATES)


class TestDowngradeSQLShape:
    """Verify downgrade() emits correct DROP statements."""

    def test_downgrade_drops_predicate_registry_table(self) -> None:
        sqls = _collect_downgrade_sqls()
        drop_stmts = [s for s in sqls if "DROP TABLE" in s.upper()]
        assert drop_stmts, (
            "downgrade() must emit DROP TABLE for relationship.entity_predicate_registry"
        )
        assert any("predicate_registry" in s.lower() for s in drop_stmts)

    def test_downgrade_does_not_drop_schema(self) -> None:
        """Downgrade must NOT drop the relationship schema.

        Other relationship-butler tables (facts, credentials) coexist in the
        schema.  Schema teardown is owned by the rel_001 root migration if ever
        needed.
        """
        sqls = _collect_downgrade_sqls()
        schema_drop_stmts = [s for s in sqls if "DROP SCHEMA" in s.upper()]
        assert not schema_drop_stmts, (
            f"downgrade() must NOT drop the relationship schema; found: {schema_drop_stmts}"
        )

    def test_downgrade_does_not_drop_facts_table(self) -> None:
        """rel_014 downgrade must not touch relationship.entity_facts (owned by rel_013)."""
        sqls = _collect_downgrade_sqls()
        drop_stmts = [s for s in sqls if "DROP TABLE" in s.upper()]
        assert not any("facts" in s.lower() for s in drop_stmts), (
            "rel_014 downgrade must not drop relationship.entity_facts — that is rel_013's responsibility"
        )


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


async def _run_sqls(pool, sqls: list[str]) -> None:
    """Execute SQL strings against the pool; skip idempotent re-runs."""
    import asyncpg

    for sql in sqls:
        try:
            await pool.execute(sql)
        except (asyncpg.DuplicateObjectError, asyncpg.DuplicateTableError):
            pass  # idempotent


async def _provision_prerequisites(pool) -> None:
    """Create public.entities + relationship.entity_facts (required by FK chain)."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.entities (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name TEXT        NOT NULL,
            entity_type    TEXT        NOT NULL DEFAULT 'person',
            roles          TEXT[]      NOT NULL DEFAULT '{}',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Ensure the relationship schema exists before creating facts.
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    # WARNING: This schema is a lightweight prerequisite fixture for predicate_registry tests.
    # It intentionally omits some CHECK constraints present in the canonical migration to keep
    # the fixture simple. Keep column names and NOT NULL constraints in sync with:
    # roster/relationship/migrations/013_facts_table.py
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_facts (
            id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            predicate   TEXT        NOT NULL,
            object      TEXT        NOT NULL,
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            src         TEXT        NOT NULL,
            conf        FLOAT       NOT NULL DEFAULT 1.0,
            last_seen   TIMESTAMPTZ,
            weight      INT,
            verified    BOOL        NOT NULL DEFAULT false,
            "primary"   BOOL,
            validity    TEXT        NOT NULL DEFAULT 'active',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


async def _run_upgrade(pool) -> None:
    sqls = _collect_upgrade_sqls()
    await _run_sqls(pool, sqls)


async def _run_downgrade(pool) -> None:
    sqls = _collect_downgrade_sqls()
    for sql in sqls:
        await pool.execute(sql)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_table_exists_after_upgrade(provisioned_postgres_pool) -> None:
    """relationship.entity_predicate_registry table exists with expected columns after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        table_oid = await pool.fetchval(
            "SELECT to_regclass('relationship.entity_predicate_registry')"
        )
        assert table_oid is not None, (
            "relationship.entity_predicate_registry must exist after upgrade"
        )

        rows = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'relationship'
              AND table_name   = 'entity_predicate_registry'
            ORDER BY column_name
            """
        )
        columns = {r["column_name"] for r in rows}
        required_columns = {"predicate", "kind", "object_kind", "description", "created_at"}
        missing = required_columns - columns
        assert not missing, f"Missing columns in relationship.entity_predicate_registry: {missing}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_tolerates_existing_memory_predicate_registry_table(
    provisioned_postgres_pool,
) -> None:
    """rel_014 must not mutate the memory module's relationship.predicate_registry table."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await pool.execute("""
            CREATE TABLE relationship.predicate_registry (
                name                  TEXT PRIMARY KEY,
                expected_subject_type TEXT,
                expected_object_type  TEXT,
                is_edge               BOOLEAN NOT NULL DEFAULT false,
                description           TEXT,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
                is_temporal           BOOLEAN NOT NULL DEFAULT false,
                usage_count           INTEGER NOT NULL DEFAULT 0,
                status                TEXT NOT NULL DEFAULT 'active',
                scope                 TEXT NOT NULL DEFAULT 'global',
                aliases               TEXT[] NOT NULL DEFAULT '{}'
            )
        """)

        await _run_upgrade(pool)

        memory_columns = {
            row["column_name"]
            for row in await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'relationship'
                  AND table_name   = 'predicate_registry'
                """
            )
        }
        assert "name" in memory_columns
        assert "predicate" not in memory_columns

        entity_columns = {
            row["column_name"]
            for row in await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'relationship'
                  AND table_name   = 'entity_predicate_registry'
                """
            )
        }
        assert {"predicate", "kind", "object_kind"} <= entity_columns


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_seed_rows_present_after_upgrade(provisioned_postgres_pool) -> None:
    """All 18 seed predicates are present in the table after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        total = await pool.fetchval("SELECT COUNT(*) FROM relationship.entity_predicate_registry")
        assert total == 18, f"Expected 18 seed predicates (6+11+1), got {total}"

        contact_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE kind = 'contact'"
        )
        assert contact_count == 6, f"Expected 6 contact predicates, got {contact_count}"

        relational_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE kind = 'relational'"
        )
        assert relational_count == 11, f"Expected 11 relational predicates, got {relational_count}"

        override_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE kind = 'override'"
        )
        assert override_count == 1, f"Expected 1 override predicate, got {override_count}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_seed_is_idempotent(provisioned_postgres_pool) -> None:
    """ON CONFLICT DO NOTHING makes re-running upgrade() safe (no duplicate rows)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        # Run upgrade SQL a second time to simulate re-run.
        await _run_upgrade(pool)

        total = await pool.fetchval("SELECT COUNT(*) FROM relationship.entity_predicate_registry")
        assert total == 18, (
            f"Re-running upgrade must not duplicate seed rows; expected 18, got {total}"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_no_verified_by_in_registry(provisioned_postgres_pool) -> None:
    """verified-by must not appear in the seeded registry.

    Per spec §"Requirement: verified is a column, not a triple".
    """
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        row = await pool.fetchrow(
            "SELECT predicate FROM relationship.entity_predicate_registry WHERE predicate = 'verified-by'"
        )
        assert row is None, "'verified-by' must not be seeded in predicate_registry"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_drops_table(provisioned_postgres_pool) -> None:
    """relationship.entity_predicate_registry is absent after downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        table_oid = await pool.fetchval(
            "SELECT to_regclass('relationship.entity_predicate_registry')"
        )
        assert table_oid is None, (
            "relationship.entity_predicate_registry must be absent after downgrade"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_does_not_drop_schema(provisioned_postgres_pool) -> None:
    """The relationship schema survives downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        schema_exists = await pool.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'relationship'"
        )
        assert schema_exists is not None, (
            "relationship schema must survive downgrade — other tables may coexist"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_preserves_facts_table(provisioned_postgres_pool) -> None:
    """relationship.entity_facts must survive rel_014 downgrade (owned by rel_013)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        table_oid = await pool.fetchval("SELECT to_regclass('relationship.entity_facts')")
        assert table_oid is not None, (
            "relationship.entity_facts must survive rel_014 downgrade — owned by rel_013"
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_reversible(provisioned_postgres_pool) -> None:
    """Upgrade → downgrade → upgrade again succeeds (reversibility contract)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)

        await _run_upgrade(pool)
        await _run_downgrade(pool)
        await _run_upgrade(pool)  # must not raise

        table_oid = await pool.fetchval(
            "SELECT to_regclass('relationship.entity_predicate_registry')"
        )
        assert table_oid is not None, (
            "relationship.entity_predicate_registry must exist after second upgrade"
        )

        total = await pool.fetchval("SELECT COUNT(*) FROM relationship.entity_predicate_registry")
        assert total == 18, f"Expected 18 seed rows after re-upgrade; got {total}"
