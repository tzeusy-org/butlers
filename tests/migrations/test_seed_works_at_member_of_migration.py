"""Tests for rel_024 — works-at + member-of predicate seed migration.

Covers:
1. Migration file structure and revision chain (unit — no DB required).
2. upgrade() SQL shape: seeds works-at and member-of as relational/entity predicates.
3. downgrade() SQL shape: removes exactly the two seeded rows.
4. Integration: seed rows present after upgrade; assert_fact(works-at, entity) inserts active row.

Issue: bu-i3sps
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
    / "024_seed_works_at_member_of.py"
)

pytestmark = pytest.mark.unit


def _load_migration():
    spec = importlib.util.spec_from_file_location("rel_024", _MIGRATION_PATH)
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
    assert mod.revision == "rel_024"
    assert mod.down_revision == "rel_023"
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


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


async def _provision_registry(pool) -> None:
    """Create minimal prerequisites: schema, entity_predicate_registry, public.entities."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
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
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
            predicate   TEXT        NOT NULL PRIMARY KEY,
            kind        TEXT        NOT NULL CHECK (kind IN ('contact', 'relational', 'override')),
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_facts (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            subject     UUID        NOT NULL REFERENCES public.entities(id),
            predicate   TEXT        NOT NULL,
            object      TEXT        NOT NULL,
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            src         TEXT        NOT NULL DEFAULT 'test',
            conf        FLOAT       NOT NULL DEFAULT 1.0,
            validity    TEXT        NOT NULL DEFAULT 'active'
                            CHECK (validity IN ('active', 'superseded', 'retracted')),
            verified    BOOLEAN     NOT NULL DEFAULT FALSE,
            "primary"   BOOLEAN,
            weight      FLOAT,
            last_seen   TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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
async def test_works_at_row_present_after_upgrade(provisioned_postgres_pool) -> None:
    """works-at row is in entity_predicate_registry after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)

        row = await pool.fetchrow(
            "SELECT kind, object_kind FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'works-at'"
        )
        assert row is not None, "works-at must be present after upgrade"
        assert row["kind"] == "relational"
        assert row["object_kind"] == "entity"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_member_of_row_present_after_upgrade(provisioned_postgres_pool) -> None:
    """member-of row is in entity_predicate_registry after upgrade."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)

        row = await pool.fetchrow(
            "SELECT kind, object_kind FROM relationship.entity_predicate_registry "
            "WHERE predicate = 'member-of'"
        )
        assert row is not None, "member-of must be present after upgrade"
        assert row["kind"] == "relational"
        assert row["object_kind"] == "entity"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_assert_fact_works_at_entity_inserts_active_row(
    provisioned_postgres_pool,
) -> None:
    """assert_fact for works-at with object_kind=entity inserts an active row.

    Spec scenario: Person-to-organization edges are registered as relational.
    After the registry seed, relationship_assert_fact() accepts works-at and
    writes an active row to relationship.entity_facts.
    """
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)

        # Mint two entities: a person and an org.
        person_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('Alice', 'person') RETURNING id"
        )
        org_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('Acme Corp', 'organization') RETURNING id"
        )

        # Simulate the central-writer insert (predicate is registry-valid).
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src)
            VALUES ($1, 'works-at', $2, 'entity', 'test-seed-migration')
            """,
            person_id,
            str(org_id),
        )

        row = await pool.fetchrow(
            "SELECT validity, predicate, object_kind "
            "FROM relationship.entity_facts "
            "WHERE subject = $1 AND predicate = 'works-at'",
            person_id,
        )
        assert row is not None, "works-at fact must exist after insert"
        assert row["validity"] == "active"
        assert row["predicate"] == "works-at"
        assert row["object_kind"] == "entity"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_idempotent(provisioned_postgres_pool) -> None:
    """Running upgrade() twice does not raise and produces exactly one row per predicate."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)
        await _run_upgrade(pool)  # must not raise

        for predicate in ("works-at", "member-of"):
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
    """Downgrade removes exactly works-at and member-of rows."""
    async with provisioned_postgres_pool() as pool:
        await _provision_registry(pool)
        await _run_upgrade(pool)
        await _run_downgrade(pool)

        for predicate in ("works-at", "member-of"):
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM relationship.entity_predicate_registry WHERE predicate = $1",
                predicate,
            )
            assert count == 0, f"'{predicate}' must be absent after downgrade, got {count} rows"
