"""Tests for rel_013 relationship_facts Alembic migration.

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
    """File-level and revision-chain contract tests."""

    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"

    def test_revision_id(self) -> None:
        mod = _load_migration()
        assert mod.revision == "rel_013"

    def test_down_revision(self) -> None:
        """Must chain from rel_012 (backfill_interaction_predicates)."""
        mod = _load_migration()
        assert mod.down_revision == "rel_012"

    def test_branch_labels_none(self) -> None:
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(getattr(mod, "upgrade", None))

    def test_downgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(getattr(mod, "downgrade", None))

    def test_migration_ordered_after_012(self) -> None:
        """013_* must sort after 012_* in the migrations directory."""
        migrations_dir = _MIGRATION_PATH.parent
        files = sorted(f.name for f in migrations_dir.glob("[0-9]*.py"))
        idx_012 = next((i for i, f in enumerate(files) if f.startswith("012_")), None)
        idx_013 = next((i for i, f in enumerate(files) if f.startswith("013_")), None)
        assert idx_012 is not None, "012_* migration not found"
        assert idx_013 is not None, "013_* migration not found"
        assert idx_013 > idx_012, "013_* must sort after 012_*"


class TestUpgradeSQLShape:
    """Verify upgrade() emits the expected SQL."""

    def test_creates_relationship_schema(self) -> None:
        sqls = _collect_upgrade_sqls()
        schema_stmts = [s for s in sqls if "CREATE SCHEMA" in s.upper()]
        assert schema_stmts, "upgrade() must emit CREATE SCHEMA IF NOT EXISTS relationship"
        assert any("relationship" in s for s in schema_stmts)

    def test_creates_facts_table(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmts = [s for s in sqls if "CREATE TABLE" in s.upper() and "facts" in s.lower()]
        assert table_stmts, "upgrade() must emit CREATE TABLE … facts"
        stmt = table_stmts[0]
        assert "relationship.facts" in stmt, "Table must be schema-qualified as relationship.facts"

    def test_table_has_id_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "id" in table_stmt.lower()
        assert "uuid" in table_stmt.lower()
        assert "primary key" in table_stmt.lower()

    def test_table_has_subject_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "subject" in table_stmt.lower()
        assert "uuid" in table_stmt.lower()
        assert "not null" in table_stmt.lower()

    def test_table_has_predicate_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "predicate" in table_stmt.lower()

    def test_table_has_object_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "object" in table_stmt.lower()

    def test_table_has_object_kind_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "object_kind" in table_stmt.lower()
        assert "literal" in table_stmt.lower()
        assert "entity" in table_stmt.lower()

    def test_table_has_src_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "src" in table_stmt.lower()

    def test_table_has_conf_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "conf" in table_stmt.lower()
        assert "1.0" in table_stmt  # default

    def test_table_has_validity_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "validity" in table_stmt.lower()
        assert "active" in table_stmt
        assert "retracted" in table_stmt
        assert "superseded" in table_stmt

    def test_table_has_verified_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "verified" in table_stmt.lower()

    def test_table_has_created_at_and_updated_at(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "created_at" in table_stmt.lower()
        assert "updated_at" in table_stmt.lower()

    def test_table_has_last_seen_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "last_seen" in table_stmt.lower()

    def test_table_has_weight_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "weight" in table_stmt.lower()

    def test_table_has_primary_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "primary" in table_stmt.lower()

    def test_subject_references_public_entities(self) -> None:
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "public.entities" in table_stmt

    def test_index_subject_predicate(self) -> None:
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any("subject" in s.lower() and "predicate" in s.lower() for s in idx_stmts), (
            "Missing (subject, predicate) index"
        )

    def test_index_predicate_object_literal_partial(self) -> None:
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any(
            "predicate" in s.lower() and "object" in s.lower() and "literal" in s.lower()
            for s in idx_stmts
        ), "Missing (predicate, object) WHERE object_kind='literal' partial index"

    def test_index_predicate_active_partial(self) -> None:
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any(
            "predicate" in s.lower() and "validity" in s.lower() and "active" in s.lower()
            for s in idx_stmts
        ), "Missing (predicate) WHERE validity='active' partial index"

    def test_index_last_seen(self) -> None:
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any("last_seen" in s.lower() for s in idx_stmts), "Missing last_seen index"

    def test_index_subject_has_active_partial(self) -> None:
        sqls = _collect_upgrade_sqls()
        idx_stmts = [s for s in sqls if "CREATE INDEX" in s.upper()]
        assert any(
            "subject" in s.lower() and "has-" in s.lower() and "active" in s.lower()
            for s in idx_stmts
        ), "Missing (subject) WHERE validity='active' AND predicate LIKE 'has-%' partial index"

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
        """relationship.facts MUST NOT have a scope column.

        Schema isolation is enforced via the ``relationship.`` schema prefix
        (RFC 0006), not a scope column.  Adding scope would break all Phase 2
        endpoints which query this table without a scope filter.

        Older migrations (rel_007, rel_010, rel_011, rel_012) reference
        ``AND scope = 'relationship'`` against the *memory module's* bare
        ``facts`` table — NOT relationship.facts.  This test guards against
        confusing the two tables.
        """
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
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
                "relationship.facts must NOT define a 'scope' column.  "
                "Schema isolation is enforced via the relationship. prefix.  "
                f"Found: {scope_cols}"
            )

    def test_table_has_subject_not_entity_id(self) -> None:
        """relationship.facts uses 'subject' for the entity FK, not 'entity_id'.

        This prevents API code from accidentally using the memory module column
        name 'entity_id' in queries against relationship.facts.
        """
        sqls = _collect_upgrade_sqls()
        table_stmt = next(
            s for s in sqls if "CREATE TABLE" in s.upper() and "relationship.facts" in s
        )
        assert "subject" in table_stmt.lower(), (
            "relationship.facts must have a 'subject' column (entity FK)"
        )
        # 'entity_id' must NOT appear as a column name in the CREATE TABLE body
        # (it may appear in FK constraints referencing public.entities, but not as a column name)
        # We check that there's no 'entity_id' column definition
        lines = [ln.strip().lower() for ln in table_stmt.split("\n")]
        entity_id_col_lines = [ln for ln in lines if ln.startswith("entity_id") and "uuid" in ln]
        assert not entity_id_col_lines, (
            "relationship.facts must NOT define 'entity_id' as a column name.  "
            "Use 'subject' instead.  "
            f"Found lines: {entity_id_col_lines}"
        )


class TestDowngradeSQLShape:
    """Verify downgrade() emits correct DROP statements."""

    def test_downgrade_drops_facts_table(self) -> None:
        sqls = _collect_downgrade_sqls()
        drop_stmts = [s for s in sqls if "DROP TABLE" in s.upper()]
        assert drop_stmts, "downgrade() must emit DROP TABLE for relationship.facts"
        assert any("facts" in s.lower() for s in drop_stmts)

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

    def test_downgrade_drops_indexes(self) -> None:
        sqls = _collect_downgrade_sqls()
        idx_drop_stmts = [s for s in sqls if "DROP INDEX" in s.upper()]
        assert idx_drop_stmts, "downgrade() must emit DROP INDEX statements"


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
    """relationship.facts table exists and has the expected columns after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        # Table exists
        table_oid = await pool.fetchval("SELECT to_regclass('relationship.facts')")
        assert table_oid is not None, "relationship.facts must exist after upgrade"

        # Expected columns
        rows = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'relationship'
              AND table_name   = 'facts'
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
        assert not missing, f"Missing columns in relationship.facts: {missing}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_indexes_exist_after_upgrade(provisioned_postgres_pool) -> None:
    """All required indexes exist on relationship.facts after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        rows = await pool.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'relationship'
              AND tablename  = 'facts'
            """
        )
        index_names = {r["indexname"] for r in rows}

        required_indexes = {
            "idx_rf_subject_predicate",
            "idx_rf_predicate_object_literal",
            "idx_rf_predicate_active",
            "idx_rf_last_seen",
            "idx_rf_subject_has_active",
            "uq_rf_spo_active",
        }
        missing = required_indexes - index_names
        assert not missing, f"Missing indexes on relationship.facts: {missing}"


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
            INSERT INTO relationship.facts
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
            INSERT INTO relationship.facts
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
            "SELECT COUNT(*) FROM relationship.facts WHERE subject = $1", entity_id
        )
        assert count == 2, f"Expected 2 rows (1 active + 1 retracted), got {count}"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_drops_table(provisioned_postgres_pool) -> None:
    """relationship.facts is absent after downgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        table_oid = await pool.fetchval("SELECT to_regclass('relationship.facts')")
        assert table_oid is None, "relationship.facts must be absent after downgrade"


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

        table_oid = await pool.fetchval("SELECT to_regclass('relationship.facts')")
        assert table_oid is not None, "relationship.facts must exist after second upgrade"


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
            INSERT INTO relationship.facts
                (subject, predicate, object, object_kind, src)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (subject, predicate, object)
            WHERE validity = 'active'
            DO UPDATE SET src = EXCLUDED.src, updated_at = now()
        """
        await pool.execute(upsert, entity_id, "knows", str(entity_id), "entity", "butler-a")
        await pool.execute(upsert, entity_id, "knows", str(entity_id), "entity", "butler-b")

        rows = await pool.fetch(
            "SELECT src, validity FROM relationship.facts WHERE subject = $1", entity_id
        )
        assert len(rows) == 1, f"Expected exactly 1 row after upsert; got {len(rows)}"
        assert rows[0]["validity"] == "active"
        assert rows[0]["src"] == "butler-b", "src should be updated to the latest upsert value"
