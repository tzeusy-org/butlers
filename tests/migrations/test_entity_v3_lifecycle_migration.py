"""Tests for rel_021 entity v3 lifecycle Alembic migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: additive observed_at/metadata columns, cardinality
   column + seed, entity_view_marks + merge_reviews tables, no-cascade FKs,
   cross-schema guard.
3. downgrade() SQL shape: drops only what this migration created.
4. Integration: columns/tables/constraints exist after upgrade; cardinality
   seeding correct; merge_reviews FKs do NOT cascade-delete (audit rows survive
   a tombstoned entity); clean downgrade; reversibility.

Issue: bu-mxxjy
Parent epic: bu-89993 — entity v3 lifecycle & backend contracts
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
    / "021_entity_v3_lifecycle.py"
)


def _load_migration():
    """Import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("rel_021", _MIGRATION_PATH)
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

    def test_revision_chain(self) -> None:
        """rel_021 -> rel_020 (chain head at authoring); no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "rel_021"
        assert mod.down_revision == "rel_020", (
            "rel_021 must chain from the current head (rel_020); do NOT fork the chain"
        )
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_migration_ordered_after_020(self) -> None:
        """021_* must sort after 020_* in the migrations directory."""
        migrations_dir = _MIGRATION_PATH.parent
        files = sorted(f.name for f in migrations_dir.glob("[0-9]*.py"))
        idx_020 = next((i for i, f in enumerate(files) if f.startswith("020_")), None)
        idx_021 = next((i for i, f in enumerate(files) if f.startswith("021_")), None)
        assert idx_020 is not None, "020_* migration not found"
        assert idx_021 is not None, "021_* migration not found"
        assert idx_021 > idx_020, "021_* must sort after 020_*"

    def test_single_cardinality_seed_list(self) -> None:
        """The single-cardinality seed set must be exactly the two spec predicates."""
        mod = _load_migration()
        assert set(mod._SINGLE_CARDINALITY_PREDICATES) == {
            "has-birthday",
            "dunbar_tier_override",
        }


class TestUpgradeSQLShape:
    """Verify upgrade() emits the expected SQL."""

    def test_guards_public_entities_presence(self) -> None:
        sqls = _collect_upgrade_sqls()
        assert any("public.entities" in s and "RAISE EXCEPTION" in s.upper() for s in sqls), (
            "upgrade() must fail-fast guard on public.entities existence (cross-schema dep)"
        )

    def test_adds_observed_at_column_additively(self) -> None:
        sqls = _collect_upgrade_sqls()
        stmt = next(
            (s for s in sqls if "ADD COLUMN" in s.upper() and "observed_at" in s.lower()),
            None,
        )
        assert stmt is not None, "upgrade() must ADD COLUMN observed_at"
        assert "entity_facts" in stmt.lower()
        assert "timestamptz" in stmt.lower()
        assert "if not exists" in stmt.lower(), "ADD COLUMN must be idempotent (IF NOT EXISTS)"

    def test_adds_metadata_jsonb_column(self) -> None:
        sqls = _collect_upgrade_sqls()
        stmt = next(
            (s for s in sqls if "ADD COLUMN" in s.upper() and "metadata" in s.lower()),
            None,
        )
        assert stmt is not None, "upgrade() must ADD COLUMN metadata"
        assert "jsonb" in stmt.lower()
        assert "entity_facts" in stmt.lower()

    def test_observed_at_and_metadata_have_no_default(self) -> None:
        """Additive-only: no in-DDL default backfill (spec — no table rewrite)."""
        sqls = _collect_upgrade_sqls()
        for col in ("observed_at", "metadata"):
            stmt = next(s for s in sqls if "ADD COLUMN" in s.upper() and col in s.lower())
            assert "default" not in stmt.lower(), (
                f"{col} ADD COLUMN must not carry a DEFAULT (additive, no rewrite/backfill in DDL)"
            )

    def test_adds_cardinality_column_with_check(self) -> None:
        sqls = _collect_upgrade_sqls()
        stmt = next(
            (s for s in sqls if "ADD COLUMN" in s.upper() and "cardinality" in s.lower()),
            None,
        )
        assert stmt is not None, "upgrade() must ADD COLUMN cardinality"
        assert "entity_predicate_registry" in stmt.lower()
        assert "not null" in stmt.lower()
        assert "'multi'" in stmt, "cardinality default must be 'multi'"
        assert "check" in stmt.lower()
        assert "single" in stmt and "multi" in stmt

    def test_seeds_single_cardinality_for_spec_predicates(self) -> None:
        sqls = _collect_upgrade_sqls()
        update_stmts = [s for s in sqls if "UPDATE" in s.upper() and "cardinality" in s.lower()]
        all_text = " ".join(update_stmts)
        assert "'single'" in all_text
        for pred in ("has-birthday", "dunbar_tier_override"):
            assert pred in all_text, f"cardinality='single' must be seeded for {pred}"

    def test_creates_entity_view_marks_table(self) -> None:
        sqls = _collect_upgrade_sqls()
        stmt = next(
            (s for s in sqls if "CREATE TABLE" in s.upper() and "entity_view_marks" in s.lower()),
            None,
        )
        assert stmt is not None, "upgrade() must CREATE relationship.entity_view_marks"
        assert "relationship.entity_view_marks" in stmt
        assert "if not exists" in stmt.lower()
        assert "entity_id" in stmt.lower()
        assert "unique" in stmt.lower(), "entity_id must be UNIQUE (one mark per entity)"
        assert "public.entities" in stmt
        assert "marked_at" in stmt.lower()

    def test_creates_merge_reviews_table(self) -> None:
        sqls = _collect_upgrade_sqls()
        stmt = next(
            (s for s in sqls if "CREATE TABLE" in s.upper() and "merge_reviews" in s.lower()),
            None,
        )
        assert stmt is not None, "upgrade() must CREATE relationship.merge_reviews"
        assert "relationship.merge_reviews" in stmt
        for col in (
            "entity_a",
            "entity_b",
            "shared_facts",
            "divergent_facts",
            "outcome",
            "reviewed_at",
            "created_at",
        ):
            assert col in stmt.lower(), f"merge_reviews must define {col}"
        assert "jsonb" in stmt.lower()
        assert "merged" in stmt and "dismissed" in stmt

    def test_merge_reviews_fks_do_not_cascade(self) -> None:
        """Audit rows survive entity tombstoning — FKs MUST NOT cascade-delete."""
        sqls = _collect_upgrade_sqls()
        stmt = next(s for s in sqls if "CREATE TABLE" in s.upper() and "merge_reviews" in s.lower())
        assert "on delete cascade" not in stmt.lower(), (
            "merge_reviews entity FKs must NOT cascade-delete; audit rows survive tombstoning"
        )


class TestDowngradeSQLShape:
    """Verify downgrade() drops only what this migration created."""

    def test_drops_supporting_tables(self) -> None:
        sqls = _collect_downgrade_sqls()
        all_text = " ".join(sqls).lower()
        assert "drop table if exists relationship.merge_reviews" in all_text
        assert "drop table if exists relationship.entity_view_marks" in all_text

    def test_drops_added_columns(self) -> None:
        sqls = _collect_downgrade_sqls()
        all_text = " ".join(sqls).lower()
        assert "drop column if exists cardinality" in all_text
        assert "drop column if exists metadata" in all_text
        assert "drop column if exists observed_at" in all_text

    def test_does_not_drop_owned_tables(self) -> None:
        """downgrade must NOT drop entity_facts or predicate_registry (earlier migrations)."""
        sqls = _collect_downgrade_sqls()
        drop_table_stmts = [s for s in sqls if "DROP TABLE" in s.upper()]
        joined = " ".join(drop_table_stmts).lower()
        assert "entity_facts" not in joined, "downgrade must not drop entity_facts (rel_013)"
        assert "predicate_registry" not in joined, (
            "downgrade must not drop predicate_registry (rel_014)"
        )

    def test_does_not_drop_schema(self) -> None:
        sqls = _collect_downgrade_sqls()
        assert not any("DROP SCHEMA" in s.upper() for s in sqls), (
            "downgrade must NOT drop the relationship schema"
        )


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _run_sqls(pool, sqls: list[str]) -> None:
    """Execute SQL strings against the pool; skip idempotent re-runs."""
    import asyncpg

    for sql in sqls:
        try:
            await pool.execute(sql)
        except (asyncpg.DuplicateObjectError, asyncpg.DuplicateTableError):
            pass  # idempotent


async def _provision_prerequisites(pool) -> None:
    """Create public.entities + relationship.{entity_facts,entity_predicate_registry}.

    Mirrors the canonical shapes from rel_013/rel_014 closely enough for rel_021
    to apply. The predicate registry is seeded with a representative subset so
    cardinality seeding can be verified.
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.entities (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name TEXT        NOT NULL,
            entity_type    TEXT        NOT NULL DEFAULT 'person',
            roles          TEXT[]      NOT NULL DEFAULT '{}',
            tombstone_at   TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
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
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
            predicate   TEXT        NOT NULL PRIMARY KEY,
            kind        TEXT        NOT NULL,
            object_kind TEXT        NOT NULL,
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Seed a representative subset (single + multi predicates).
    for predicate, kind, object_kind in (
        ("has-birthday", "contact", "literal"),
        ("dunbar_tier_override", "override", "literal"),
        ("has-email", "contact", "literal"),
        ("knows", "relational", "entity"),
    ):
        await pool.execute(
            """
            INSERT INTO relationship.entity_predicate_registry (predicate, kind, object_kind)
            VALUES ($1, $2, $3)
            ON CONFLICT (predicate) DO NOTHING
            """,
            predicate,
            kind,
            object_kind,
        )


async def _run_upgrade(pool) -> None:
    await _run_sqls(pool, _collect_upgrade_sqls())


async def _run_downgrade(pool) -> None:
    for sql in _collect_downgrade_sqls():
        await pool.execute(sql)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_columns_and_tables_exist_after_upgrade(provisioned_postgres_pool) -> None:
    """observed_at/metadata/cardinality columns and both tables exist after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        fact_cols = {
            r["column_name"]
            for r in await pool.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'relationship' AND table_name = 'entity_facts'
                """
            )
        }
        assert {"observed_at", "metadata"} <= fact_cols

        reg_cols = {
            r["column_name"]
            for r in await pool.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'relationship'
                  AND table_name = 'entity_predicate_registry'
                """
            )
        }
        assert "cardinality" in reg_cols

        assert (
            await pool.fetchval("SELECT to_regclass('relationship.entity_view_marks')") is not None
        )
        assert await pool.fetchval("SELECT to_regclass('relationship.merge_reviews')") is not None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_cardinality_seeding(provisioned_postgres_pool) -> None:
    """has-birthday=single, dunbar_tier_override=single, has-email/knows=multi."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        async def card(pred: str) -> str:
            return await pool.fetchval(
                "SELECT cardinality FROM relationship.entity_predicate_registry "
                "WHERE predicate = $1",
                pred,
            )

        assert await card("has-birthday") == "single"
        assert await card("dunbar_tier_override") == "single"
        assert await card("has-email") == "multi"
        assert await card("knows") == "multi"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_cardinality_check_constraint_rejects_bad_value(provisioned_postgres_pool) -> None:
    """The cardinality CHECK constraint rejects values outside {single, multi}."""
    import asyncpg

    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "UPDATE relationship.entity_predicate_registry "
                "SET cardinality = 'bogus' WHERE predicate = 'has-email'"
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_merge_reviews_survive_tombstoned_entity(provisioned_postgres_pool) -> None:
    """Scenario: audit rows survive the merged-away entity (no cascade delete).

    Tombstoning entity_b (soft-delete via tombstone_at) must NOT remove the
    merge_reviews row. The FKs are non-cascading, so even a hard DELETE of the
    entity would be *blocked* by the FK rather than cascading — but the live
    path is tombstoning (a soft UPDATE), which the audit row is unaffected by.
    """
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        entity_a = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('X') RETURNING id"
        )
        entity_b = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Y') RETURNING id"
        )

        review_id = await pool.fetchval(
            """
            INSERT INTO relationship.merge_reviews
                (entity_a, entity_b, shared_facts, divergent_facts, outcome, reviewed_at)
            VALUES ($1, $2, '[]'::jsonb, '[]'::jsonb, 'merged', now())
            RETURNING id
            """,
            entity_a,
            entity_b,
        )

        # Tombstone entity_b (the merged-away entity) — soft delete.
        await pool.execute(
            "UPDATE public.entities SET tombstone_at = now() WHERE id = $1", entity_b
        )

        # The audit row must still be readable.
        still_there = await pool.fetchval(
            "SELECT id FROM relationship.merge_reviews WHERE id = $1", review_id
        )
        assert still_there == review_id, "merge_reviews row must survive tombstoning of entity_b"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_merge_reviews_fk_blocks_hard_delete(provisioned_postgres_pool) -> None:
    """Non-cascading FK: a hard DELETE of a referenced entity is rejected, not cascaded."""
    import asyncpg

    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        entity_a = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('A') RETURNING id"
        )
        entity_b = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('B') RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO relationship.merge_reviews
                (entity_a, entity_b, shared_facts, divergent_facts, outcome, reviewed_at)
            VALUES ($1, $2, '[]'::jsonb, '[]'::jsonb, 'dismissed', now())
            """,
            entity_a,
            entity_b,
        )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await pool.execute("DELETE FROM public.entities WHERE id = $1", entity_b)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_entity_view_marks_unique_per_entity(provisioned_postgres_pool) -> None:
    """entity_view_marks enforces one mark per entity (UNIQUE on entity_id)."""
    import asyncpg

    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)

        entity = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Z') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO relationship.entity_view_marks (entity_id, marked_at) VALUES ($1, now())",
            entity,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO relationship.entity_view_marks (entity_id, marked_at) "
                "VALUES ($1, now())",
                entity,
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_idempotent(provisioned_postgres_pool) -> None:
    """Re-running upgrade SQL is a safe no-op (IF NOT EXISTS / idempotent UPDATEs)."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_upgrade(pool)  # must not raise

        # Cardinality seeding still correct after a second run.
        card = await pool.fetchval(
            "SELECT cardinality FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'has-birthday'"
        )
        assert card == "single"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_removes_only_owned_objects(provisioned_postgres_pool) -> None:
    """downgrade drops the new tables/columns but preserves entity_facts + registry."""
    async with provisioned_postgres_pool() as pool:
        await _provision_prerequisites(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        assert await pool.fetchval("SELECT to_regclass('relationship.merge_reviews')") is None
        assert await pool.fetchval("SELECT to_regclass('relationship.entity_view_marks')") is None

        fact_cols = {
            r["column_name"]
            for r in await pool.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'relationship' AND table_name = 'entity_facts'
                """
            )
        }
        assert "observed_at" not in fact_cols and "metadata" not in fact_cols
        # Owned-by-earlier-migration objects survive.
        assert await pool.fetchval("SELECT to_regclass('relationship.entity_facts')") is not None
        assert (
            await pool.fetchval("SELECT to_regclass('relationship.entity_predicate_registry')")
            is not None
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

        assert await pool.fetchval("SELECT to_regclass('relationship.merge_reviews')") is not None
