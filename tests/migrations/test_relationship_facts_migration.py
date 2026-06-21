"""Tests for rel_013 entity_facts Alembic migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: CREATE SCHEMA, CREATE TABLE, expected columns,
   five standard indexes, one UNIQUE partial index for SPO idempotency.
3. downgrade() SQL shape: DROP statements, does NOT drop the schema.
4. Integration: table and indexes exist after upgrade; clean downgrade.
5. Uniqueness partial index enforces Amendment 14 idempotency contract.
6. Downgrade is reversible (re-upgrade succeeds after downgrade).

Issue: bu-892tf
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
    / "013_relationship_facts.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("rel_013", _MIGRATION_PATH)
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
        """rel_013 -> rel_012, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "rel_013"
        assert mod.down_revision == "rel_012"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestUpgradeSQLShape:
    """Architectural-invariant guards on the CREATE TABLE / index DDL.

    Per-column presence, the five standard indexes, and the FK to
    public.entities are exercised against a live DB by the integration
    column-set + index-exists tests. The three SQL-text guards retained here are
    the SOLE proof of architectural invariants (no live-DB test triggers them):
    no scope column (RFC 0006 isolation), 'subject' (not 'entity_id') FK column,
    and the UNIQUE partial index for Amendment 14 idempotency.
    """

    def test_unique_partial_index_spo_active(self) -> None:
        """The uniqueness partial index supports Amendment 14 idempotency.

        Must be a UNIQUE index on (subject, predicate, object) WHERE validity='active'.
        """
        sqls = _collect_upgrade_sqls()
        unique_idx_stmts = [s for s in sqls if "CREATE UNIQUE INDEX" in s.upper()]
        assert unique_idx_stmts, "No UNIQUE INDEX found in upgrade SQL"
        stmt = unique_idx_stmts[0]
        assert "subject" in stmt.lower(), "Unique index must include subject"
        assert "predicate" in stmt.lower(), "Unique index must include predicate"
        assert "object" in stmt.lower(), "Unique index must include object"
        assert "validity" in stmt.lower(), "Unique index must be partial on validity"
        assert "active" in stmt, "Unique index WHERE clause must reference 'active'"

    def test_table_has_no_scope_column(self) -> None:
        """relationship.entity_facts MUST NOT have a scope column.

        Schema isolation is enforced via the ``relationship.`` schema prefix
        (RFC 0006), not a scope column.  Adding scope would break all Phase 2
        endpoints which query this table without a scope filter.

        Older migrations (rel_007, rel_010, rel_011, rel_012) reference
        ``AND scope = 'relationship'`` against the *memory module's* bare
        ``facts`` table — NOT relationship.entity_facts.  This test guards against
        confusing the two tables.
        """
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.entity_facts" in s
        )
        # The CREATE TABLE DDL must not define a 'scope' column
        import re

        # Extract only the column definition block (between the first ( and last ))
        # and check that 'scope' does not appear as a column name
        col_block_match = re.search(r"\(\s*(.*)\s*\)", table_stmt, re.DOTALL)
        if col_block_match:
            col_block = col_block_match.group(1)
            # Split on commas (rough parse) and check no line starts with 'scope'
            col_lines = [ln.strip() for ln in col_block.split("\n") if ln.strip()]
            scope_cols = [ln for ln in col_lines if ln.lower().startswith("scope")]
            assert not scope_cols, (
                "relationship.entity_facts must NOT define a 'scope' column.  "
                "Schema isolation is enforced via the relationship. prefix.  "
                f"Found: {scope_cols}"
            )

    def test_table_has_subject_not_entity_id(self) -> None:
        """relationship.entity_facts uses 'subject' for the entity FK, not 'entity_id'.

        This prevents API code from accidentally using the memory module column
        name 'entity_id' in queries against relationship.entity_facts.
        """
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.entity_facts" in s
        )
        assert "subject" in table_stmt.lower(), (
            "relationship.entity_facts must have a 'subject' column (entity FK)"
        )
        # 'entity_id' must NOT appear as a column name in the CREATE TABLE body
        # (it may appear in FK constraints referencing public.entities, but not as a column name)
        # We check that there's no 'entity_id' column definition
        lines = [ln.strip().lower() for ln in table_stmt.split("\n")]
        entity_id_col_lines = [ln for ln in lines if ln.startswith("entity_id") and "uuid" in ln]
        assert not entity_id_col_lines, (
            "relationship.entity_facts must NOT define 'entity_id' as a column name.  "
            "Use 'subject' instead.  "
            f"Found lines: {entity_id_col_lines}"
        )


class TestDowngradeSQLShape:
    """Verify downgrade() does not drop the shared relationship schema."""

    def test_downgrade_does_not_drop_schema(self) -> None:
        """Downgrade must NOT drop the relationship schema.

        Other relationship-butler tables coexist in the schema.
        Schema teardown is owned by the rel_001 root migration if ever needed.
        """
        sqls = _collect_downgrade_sqls()
        schema_drop_stmts = [s for s in sqls if "DROP SCHEMA" in s.upper()]
        assert not schema_drop_stmts, (
            f"downgrade() must NOT drop the relationship schema; found: {schema_drop_stmts}"
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
    """Create public.entities (minimal) required by the subject FK."""
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


async def _run_upgrade(pool) -> None:
    """Apply rel_013 upgrade SQL against the pool."""
    sqls = _collect_upgrade_sqls()
    await _run_sqls(pool, sqls)


async def _run_downgrade(pool) -> None:
    """Apply rel_013 downgrade SQL against the pool."""
    sqls = _collect_downgrade_sqls()
    for sql in sqls:
        await pool.execute(sql)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_table_exists_after_upgrade(provisioned_postgres_pool) -> None:
    """relationship.entity_facts table exists and has the expected columns after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        # Table exists
        table_oid = await pool.fetchval("SELECT to_regclass('relationship.entity_facts')")
        assert table_oid is not None, "relationship.entity_facts must exist after upgrade"

        # Expected columns
        rows = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'relationship'
              AND table_name   = 'entity_facts'
            ORDER BY column_name
            """
        )
        columns = {r["column_name"] for r in rows}
        required_columns = {
            "id",
            "subject",
            "predicate",
            "object",
            "object_kind",
            "src",
            "conf",
            "last_seen",
            "weight",
            "verified",
            "primary",
            "validity",
            "created_at",
            "updated_at",
        }
        missing = required_columns - columns
        assert not missing, f"Missing columns in relationship.entity_facts: {missing}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_tolerates_existing_memory_facts_table(
    provisioned_postgres_pool,
) -> None:
    """rel_013 must not collide with the relationship memory module's facts table."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await pool.execute("""
            CREATE TABLE relationship.facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'relationship',
                entity_id UUID,
                validity TEXT NOT NULL DEFAULT 'active'
            )
        """)

        await _run_upgrade(pool)

        legacy_columns = {
            row["column_name"]
            for row in await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'relationship'
                  AND table_name = 'facts'
                """
            )
        }
        triple_columns = {
            row["column_name"]
            for row in await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'relationship'
                  AND table_name = 'entity_facts'
                """
            )
        }

        assert "content" in legacy_columns
        assert "object_kind" not in legacy_columns
        assert {"subject", "predicate", "object", "object_kind", "src"} <= triple_columns


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_indexes_exist_after_upgrade(provisioned_postgres_pool) -> None:
    """All required indexes exist on relationship.entity_facts after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        rows = await pool.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'relationship'
              AND tablename  = 'entity_facts'
            """
        )
        index_names = {r["indexname"] for r in rows}

        required_indexes = {
            "idx_ef_subject_predicate",
            "idx_ef_predicate_object_literal",
            "idx_ef_predicate_active",
            "idx_ef_last_seen",
            "idx_ef_subject_has_active",
            "uq_ef_spo_active",
        }
        missing = required_indexes - index_names
        assert not missing, f"Missing indexes on relationship.entity_facts: {missing}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_unique_partial_index_enforces_spo_idempotency(
    provisioned_postgres_pool,
) -> None:
    """The UNIQUE partial index prevents duplicate active triples.

    Inserts two rows with the same (subject, predicate, object) and
    validity='active' — the second INSERT must fail with a uniqueness violation.
    A third row with validity='retracted' and the same SPO MUST succeed
    (tombstoned rows are excluded from the constraint).
    """
    import asyncpg

    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        # Mint an entity so the FK on subject is satisfied
        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Alice') RETURNING id"
        )

        insert = """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src)
            VALUES ($1, $2, $3, $4, $5)
        """

        # First active triple — must succeed
        await pool.execute(insert, entity_id, "has-email", "alice@example.com", "literal", "test")

        # Duplicate active triple — must fail with UniqueViolationError
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                insert, entity_id, "has-email", "alice@example.com", "literal", "test"
            )

        # Retracted triple with same SPO — must succeed (not covered by partial index)
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, validity)
            VALUES ($1, $2, $3, $4, $5, 'retracted')
        """,
            entity_id,
            "has-email",
            "alice@example.com",
            "literal",
            "test",
        )

        # Verify: 1 active + 1 retracted row exist
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1", entity_id
        )
        assert count == 2, f"Expected 2 rows (1 active + 1 retracted), got {count}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_drops_table(provisioned_postgres_pool) -> None:
    """relationship.entity_facts is absent after downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        table_oid = await pool.fetchval("SELECT to_regclass('relationship.entity_facts')")
        assert table_oid is None, "relationship.entity_facts must be absent after downgrade"


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
async def test_upgrade_is_reversible(provisioned_postgres_pool) -> None:
    """Upgrade → downgrade → upgrade again succeeds (reversibility contract)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)

        await _run_upgrade(pool)
        await _run_downgrade(pool)
        await _run_upgrade(pool)  # must not raise

        table_oid = await pool.fetchval("SELECT to_regclass('relationship.entity_facts')")
        assert table_oid is not None, "relationship.entity_facts must exist after second upgrade"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upsert_on_conflict_pattern(provisioned_postgres_pool) -> None:
    """The uniqueness index supports ON CONFLICT DO UPDATE (Amendment 14 upsert).

    Simulates the central writer's idempotency pattern:
        INSERT … ON CONFLICT (subject, predicate, object)
        WHERE validity='active' DO UPDATE SET src = EXCLUDED.src
    Must produce exactly one active row after two upserts with the same SPO.
    """
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Bob') RETURNING id"
        )

        upsert = """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (subject, predicate, object)
            WHERE validity = 'active'
            DO UPDATE SET src = EXCLUDED.src, updated_at = now()
        """
        await pool.execute(upsert, entity_id, "knows", str(entity_id), "entity", "butler-a")
        await pool.execute(upsert, entity_id, "knows", str(entity_id), "entity", "butler-b")

        rows = await pool.fetch(
            "SELECT src, validity FROM relationship.entity_facts WHERE subject = $1", entity_id
        )
        assert len(rows) == 1, f"Expected exactly 1 row after upsert; got {len(rows)}"
        assert rows[0]["validity"] == "active"
        assert rows[0]["src"] == "butler-b", "src should be updated to the latest upsert value"
