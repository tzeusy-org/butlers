"""Tests for core_133 — dedup duplicate-entity contacts on the core public/
connectors contact refs (bu-vcfyg, Phase 7.3a-3b).

Covers:
  (a) Module structure — revision/down_revision chain, callables, snapshot +
      to_regclass guards + parity RAISE.  Pure unit, no DB.
  (b) Dedup behaviour against a live DB (Docker/Postgres): duplicate contacts are
      merged onto the oldest (canonical) contact across priority_contacts (PK
      collision) and home_assistant_persons; snapshots written; idempotent.

The ninth FK (``contacts_source_links``) is owned by the contacts module and
covered by ``contacts_005``; the relationship-schema FKs by ``rel_030``.

Parent: bu-oluyt.7 (retire public.contacts) → bu-y6o7q (guarded DROP).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_133_dedup_contacts_public_refs.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_core_133", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: module structure + source guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision_chain(self):
        """core_133 -> core_132, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "core_133"
        assert mod.down_revision == "core_132"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_dedup_snapshots_guards_and_parity_raises(self):
        dedup = _load_migration()._SNAPSHOT_AND_DEDUP_SQL
        assert "to_regclass('public.contacts')" in dedup
        assert "priority_contacts_dedup_bak_core_133" in dedup
        assert "home_assistant_persons_dedup_bak_core_133" in dedup
        # Same canonical rule as rel_030 / contacts_005 (oldest contact per entity).
        assert "ORDER BY created_at ASC, id ASC" in dedup
        assert dedup.count("RAISE EXCEPTION") >= 2


# ---------------------------------------------------------------------------
# (b) Integration: dedup behaviour against a live DB
# ---------------------------------------------------------------------------

_PROVISION_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS connectors;
CREATE TABLE public.entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE public.contacts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE public.priority_contacts (
    contact_id UUID PRIMARY KEY,
    entity_id  UUID
);
CREATE TABLE connectors.home_assistant_persons (
    ha_entity_id TEXT PRIMARY KEY,
    contact_id   UUID,
    entity_id    UUID
);
"""


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dedup(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)
        e1 = await pool.fetchval("INSERT INTO public.entities DEFAULT VALUES RETURNING id")
        c1 = await pool.fetchval(
            "INSERT INTO public.contacts (entity_id, created_at) VALUES ($1, now() - interval '2 days') RETURNING id",
            e1,
        )
        c2 = await pool.fetchval(
            "INSERT INTO public.contacts (entity_id, created_at) VALUES ($1, now() - interval '1 day') RETURNING id",
            e1,
        )
        await pool.execute(
            "INSERT INTO public.priority_contacts (contact_id, entity_id) VALUES ($1,$3),($2,$3)",
            c1,
            c2,
            e1,
        )
        await pool.execute(
            "INSERT INTO connectors.home_assistant_persons (ha_entity_id, contact_id, entity_id) VALUES ('person.x',$1,$2)",
            c2,
            e1,
        )

        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)

        # priority_contacts collapsed to the single canonical c1.
        assert await pool.fetchval("SELECT count(*) FROM public.priority_contacts") == 1
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.priority_contacts WHERE contact_id = $1", c1
            )
            == 1
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.priority_contacts WHERE contact_id = $1", c2
            )
            == 0
        )

        # ha_persons repointed to canonical c1.
        assert (
            await pool.fetchval(
                "SELECT contact_id FROM connectors.home_assistant_persons WHERE ha_entity_id = 'person.x'"
            )
            == c1
        )

        # Snapshot written.
        assert (
            await pool.fetchval("SELECT to_regclass('public.priority_contacts_dedup_bak_core_133')")
            is not None
        )

        # Idempotent re-run.
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)
        assert await pool.fetchval("SELECT count(*) FROM public.priority_contacts") == 1


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_noop_when_contacts_absent(provisioned_postgres_pool) -> None:
    """Forward-compat: dedup cleanly no-ops if public.contacts is already gone."""
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)  # must not raise
