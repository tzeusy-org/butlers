"""Tests for contacts_005 — dedup + drop the contacts_source_links → public.contacts
FK (bu-vcfyg, Phase 7.3a-3b).

Covers:
  (a) Module structure — revision/down_revision chain, callables, snapshot +
      to_regclass guards + parity RAISE + the FK drop.  Pure unit, no DB.
  (b) Behaviour against a live DB (Docker/Postgres): local_contact_id is repointed
      from a superseded duplicate onto the canonical contact, the FK to
      public.contacts is dropped, the column survives, snapshot written, idempotent.

This is the ninth and last FK to public.contacts (siblings: core_133, rel_030).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "modules"
    / "contacts"
    / "migrations"
    / "005_drop_source_links_contacts_fk.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_contacts_005", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision_chain(self):
        """contacts_005 -> contacts_004, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "contacts_005"
        assert mod.down_revision == "contacts_004"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_drops_fk_idempotently(self):
        sql = _load_migration()._DROP_FK_SQL
        assert "contacts_source_links_local_contact_id_fkey" in sql
        assert "DROP CONSTRAINT IF EXISTS" in sql

    def test_dedup_snapshots_guards_and_parity_raises(self):
        dedup = _load_migration()._SNAPSHOT_AND_DEDUP_SQL
        assert "to_regclass('public.contacts')" in dedup
        assert "contacts_source_links_dedup_bak_contacts_005" in dedup
        assert "ORDER BY created_at ASC, id ASC" in dedup
        assert dedup.count("RAISE EXCEPTION") >= 2


_PROVISION_SCHEMA = """
CREATE TABLE public.entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE public.contacts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id  UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE public.contacts_source_links (
    provider            TEXT NOT NULL,
    account_id          TEXT NOT NULL,
    external_contact_id TEXT NOT NULL,
    local_contact_id    UUID,
    local_entity_id     UUID,
    PRIMARY KEY (provider, account_id, external_contact_id),
    CONSTRAINT contacts_source_links_local_contact_id_fkey FOREIGN KEY (local_contact_id)
        REFERENCES public.contacts(id) ON DELETE SET NULL
);
"""


def _fk_count_sql() -> str:
    return """
    SELECT count(*) FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_class refrel ON refrel.oid = con.confrelid
    JOIN pg_namespace rn ON rn.oid = refrel.relnamespace
    WHERE con.contype = 'f' AND rel.relname = 'contacts_source_links'
      AND refrel.relname = 'contacts' AND rn.nspname = 'public'
    """


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dedup_and_fk_drop(provisioned_postgres_pool) -> None:
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
            "INSERT INTO public.contacts_source_links "
            "(provider, account_id, external_contact_id, local_contact_id, local_entity_id) "
            "VALUES ('google','acct','ext1',$1,$2)",
            c2,
            e1,
        )

        assert await pool.fetchval(_fk_count_sql()) == 1

        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)
        await pool.execute(mod._DROP_FK_SQL)

        # FK dropped; local_contact_id column retained and repointed to canonical c1.
        assert await pool.fetchval(_fk_count_sql()) == 0
        assert (
            await pool.fetchval(
                "SELECT local_contact_id FROM public.contacts_source_links WHERE external_contact_id = 'ext1'"
            )
            == c1
        )
        assert (
            await pool.fetchval(
                "SELECT to_regclass('public.contacts_source_links_dedup_bak_contacts_005')"
            )
            is not None
        )

        # Idempotent re-run.
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)
        await pool.execute(mod._DROP_FK_SQL)
        assert await pool.fetchval(_fk_count_sql()) == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_noop_when_contacts_absent(provisioned_postgres_pool) -> None:
    mod = _load_migration()
    async with provisioned_postgres_pool() as pool:
        await pool.execute(mod._SNAPSHOT_AND_DEDUP_SQL)  # must not raise
        await pool.execute(mod._DROP_FK_SQL)  # must not raise
