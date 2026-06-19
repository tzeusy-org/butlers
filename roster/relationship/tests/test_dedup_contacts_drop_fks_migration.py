"""Tests for rel_030 — dedup duplicate-entity contacts + drop the relationship→
public.contacts FK constraints (bu-vcfyg, Phase 7.3a-3b).

Covers:
  (a) Module structure — revision/down_revision chain, callables, and that the
      source snapshots before mutating, guards with to_regclass, parity-RAISEs,
      and drops all eight FK constraints.  Pure unit, no DB.
  (b) Dedup + FK-drop behaviour against a live DB (Docker/Postgres): two contacts
      sharing one entity are merged onto the oldest (canonical) contact across
      every dependent table, PK collisions and relationship self-loops are
      dropped, the contact_entity_map collapses 1:1, the eight FK constraints are
      gone, snapshots are written, and the whole thing is idempotent on re-run.

Parent: bu-oluyt.7 (retire public.contacts) → bu-y6o7q (guarded DROP).
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "030_dedup_contacts_drop_contacts_fks.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_rel_030", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: module structure + source guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision(self):
        assert _load_migration().revision == "rel_030"

    def test_down_revision_chains_from_029(self):
        assert _load_migration().down_revision == "rel_029"

    def test_branch_labels_and_depends_on_none(self):
        mod = _load_migration()
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_drops_all_eight_fk_constraints(self):
        sql = _load_migration()._DROP_FK_SQL
        for fk in (
            "addresses_contact_id_fkey",
            "contact_labels_contact_id_fkey",
            "group_members_contact_id_fkey",
            "important_dates_contact_id_fkey",
            "life_events_contact_id_fkey",
            "relationships_contact_a_fkey",
            "relationships_contact_b_fkey",
            "tasks_contact_id_fkey",
        ):
            assert fk in sql, f"missing FK drop for {fk}"
        # Idempotent drops.
        assert "DROP CONSTRAINT IF EXISTS" in sql

    def test_dedup_snapshots_guards_and_parity_raises(self):
        dedup = _load_migration()._SNAPSHOT_AND_DEDUP_SQL
        # Forward-compat guard: clean no-op if public.contacts is already dropped.
        assert "to_regclass('public.contacts')" in dedup
        # Snapshot-before-mutate for reversibility.
        assert "addresses_dedup_bak_rel_030" in dedup
        assert "contact_entity_map_dedup_bak_rel_030" in dedup
        # Canonical selection = oldest contact per entity.
        assert "ORDER BY created_at ASC, id ASC" in dedup
        # Parity guards must be able to abort the migration.
        assert dedup.count("RAISE EXCEPTION") >= 3


# ---------------------------------------------------------------------------
# (b) Integration: dedup + FK-drop behaviour against a live DB
# ---------------------------------------------------------------------------

# Minimal schema mirroring the live column layout the migration touches. The
# relationship tables land in `public` here (no search_path override), exactly
# as the migration's unqualified names resolve in schema-less test runs.
_PROVISION_SCHEMA = """
CREATE TABLE public.entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid()
);
CREATE TABLE public.contacts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL,
    CONSTRAINT addresses_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE labels (id UUID PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE contact_labels (
    label_id   UUID NOT NULL,
    contact_id UUID NOT NULL,
    PRIMARY KEY (label_id, contact_id),
    CONSTRAINT contact_labels_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE group_members (
    group_id   UUID NOT NULL,
    contact_id UUID NOT NULL,
    PRIMARY KEY (group_id, contact_id),
    CONSTRAINT group_members_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE important_dates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL,
    CONSTRAINT important_dates_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE life_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL,
    CONSTRAINT life_events_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_a UUID NOT NULL,
    contact_b UUID NOT NULL,
    CONSTRAINT relationships_contact_a_fkey FOREIGN KEY (contact_a)
        REFERENCES public.contacts(id) ON DELETE CASCADE,
    CONSTRAINT relationships_contact_b_fkey FOREIGN KEY (contact_b)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL,
    CONSTRAINT tasks_contact_id_fkey FOREIGN KEY (contact_id)
        REFERENCES public.contacts(id) ON DELETE CASCADE
);
CREATE TABLE contact_entity_map (
    contact_id UUID PRIMARY KEY,
    entity_id  UUID NOT NULL
);
"""


def _fk_to_contacts_count_sql() -> str:
    return """
    SELECT count(*) FROM pg_constraint con
    JOIN pg_class refrel ON refrel.oid = con.confrelid
    JOIN pg_namespace rn ON rn.oid = refrel.relnamespace
    WHERE con.contype = 'f' AND refrel.relname = 'contacts' AND rn.nspname = 'public'
    """


async def _seed(pool):
    e1 = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
    e2 = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
    # c1 older than c2 -> c1 canonical for e1. c3 alone for e2.
    c1 = await pool.fetchval(
        "INSERT INTO public.contacts (entity_id, created_at) VALUES ($1, now() - interval '2 days') RETURNING id",
        e1,
    )
    c2 = await pool.fetchval(
        "INSERT INTO public.contacts (entity_id, created_at) VALUES ($1, now() - interval '1 day') RETURNING id",
        e1,
    )
    c3 = await pool.fetchval(
        "INSERT INTO public.contacts (entity_id, created_at) VALUES ($1, now()) RETURNING id", e2
    )
    l1 = await pool.fetchval("INSERT INTO labels DEFAULT VALUES RETURNING id")
    l2 = await pool.fetchval("INSERT INTO labels DEFAULT VALUES RETURNING id")
    g1 = uuid.uuid4()

    # addresses: one per duplicate contact (both must survive on c1).
    await pool.execute("INSERT INTO addresses (contact_id) VALUES ($1), ($2)", c1, c2)
    # contact_labels: (l1,c1)+(l1,c2) collide; (l2,c2) repoints to (l2,c1).
    await pool.execute(
        "INSERT INTO contact_labels (label_id, contact_id) VALUES ($1,$2),($1,$3),($4,$3)",
        l1,
        c1,
        c2,
        l2,
    )
    # group_members: (g1,c1)+(g1,c2) collide.
    await pool.execute(
        "INSERT INTO group_members (group_id, contact_id) VALUES ($1,$2),($1,$3)", g1, c1, c2
    )
    await pool.execute("INSERT INTO important_dates (contact_id) VALUES ($1)", c2)
    await pool.execute("INSERT INTO life_events (contact_id) VALUES ($1)", c2)
    await pool.execute("INSERT INTO tasks (contact_id) VALUES ($1)", c2)
    # relationships: (c1,c3) keep; (c2,c3) -> (c1,c3); (c1,c2) -> self-loop DELETED.
    await pool.execute(
        "INSERT INTO relationships (contact_a, contact_b) VALUES ($1,$2),($3,$2),($1,$3)",
        c1,
        c3,
        c2,
    )
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1,$2),($3,$2),($4,$5)",
        c1,
        e1,
        c2,
        c3,
        e2,
    )
    return {"e1": e1, "e2": e2, "c1": c1, "c2": c2, "c3": c3, "l1": l1, "l2": l2, "g1": g1}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dedup_and_fk_drop(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)
        ids = await _seed(pool)
        c1, c2, c3, l1, l2 = ids["c1"], ids["c2"], ids["c3"], ids["l1"], ids["l2"]

        # Precondition: eight FK constraints reference public.contacts.
        assert await pool.fetchval(_fk_to_contacts_count_sql()) == 8

        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)
        await pool.execute(mod._DROP_FK_SQL)

        # FK constraints to public.contacts are gone -> table becomes droppable.
        assert await pool.fetchval(_fk_to_contacts_count_sql()) == 0

        # No dependent row references the superseded duplicate c2.
        for tbl, col in [
            ("addresses", "contact_id"),
            ("contact_labels", "contact_id"),
            ("group_members", "contact_id"),
            ("important_dates", "contact_id"),
            ("life_events", "contact_id"),
            ("tasks", "contact_id"),
            ("contact_entity_map", "contact_id"),
        ]:
            n = await pool.fetchval(f"SELECT count(*) FROM {tbl} WHERE {col} = $1", c2)  # noqa: S608
            assert n == 0, f"{tbl}.{col} still references superseded contact c2"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM relationships WHERE contact_a = $1 OR contact_b = $1", c2
            )
            == 0
        )

        # addresses: both rows survive, now on canonical c1.
        assert await pool.fetchval("SELECT count(*) FROM addresses WHERE contact_id = $1", c1) == 2
        # contact_labels: (l1,c1) kept, (l2,c1) repointed, (l1,c2) collision-dropped.
        rows = await pool.fetch("SELECT label_id, contact_id FROM contact_labels ORDER BY 1")
        pairs = {(r["label_id"], r["contact_id"]) for r in rows}
        assert pairs == {(l1, c1), (l2, c1)}
        # group_members: single (g1,c1).
        assert await pool.fetchval("SELECT count(*) FROM group_members") == 1
        assert (
            await pool.fetchval("SELECT count(*) FROM group_members WHERE contact_id = $1", c1) == 1
        )
        # important_dates/life_events/tasks repointed to c1.
        for tbl in ("important_dates", "life_events", "tasks"):
            assert await pool.fetchval(f"SELECT count(*) FROM {tbl} WHERE contact_id = $1", c1) == 1  # noqa: S608

        # relationships: self-loop deleted; remaining endpoints are c1<->c3 only.
        rels = await pool.fetch("SELECT contact_a, contact_b FROM relationships")
        assert all(r["contact_a"] != r["contact_b"] for r in rels), "self-loop not removed"
        endpoints = {r["contact_a"] for r in rels} | {r["contact_b"] for r in rels}
        assert endpoints == {c1, c3}

        # contact_entity_map collapsed 1:1 (c2 row gone; e1 -> exactly c1).
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM contact_entity_map WHERE entity_id = $1", ids["e1"]
            )
            == 1
        )
        assert (
            await pool.fetchval(
                "SELECT contact_id FROM contact_entity_map WHERE entity_id = $1", ids["e1"]
            )
            == c1
        )

        # Snapshot tables were written (reversibility).
        assert await pool.fetchval("SELECT to_regclass('addresses_dedup_bak_rel_030')") is not None

        # Idempotent: re-running is a clean no-op (no duplicate contacts remain).
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)
        await pool.execute(mod._DROP_FK_SQL)
        assert await pool.fetchval("SELECT count(*) FROM addresses WHERE contact_id = $1", c1) == 2
        assert await pool.fetchval("SELECT count(*) FROM contact_entity_map") == 2


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_noop_when_contacts_absent(provisioned_postgres_pool) -> None:
    """Forward-compat: dedup cleanly no-ops if public.contacts is already gone."""
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        # No public.contacts table at all — mirrors the post-DROP world.
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)  # must not raise
        await pool.execute(mod._DROP_FK_SQL)  # must not raise
