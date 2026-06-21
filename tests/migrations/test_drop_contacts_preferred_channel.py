"""Tests for core_123_drop_contacts_preferred_channel (bu-1yihq).

Covers:
  (a) Module structure — revision/down_revision, callables, force-env parsing,
      and that the source DROPs the column + snapshots + guards cross-chain.
      Pure unit, no DB.
  (b) Backfill + parity SQL — the in-migration column→fact data migration and
      its zero-loss guard. Integration (Docker/Postgres): seeds
      entities/contacts/entity_facts and asserts the backfill inserts exactly one
      single-valued ``prefers-channel`` fact per entity-linked contact that has no
      pre-existing active fact, leaves live facts untouched, and that the parity
      sweep reports zero gaps once backfilled.
  (c) rel_003 cross-chain guard — that the consolidate migration omits
      ``preferred_channel`` from its INSERT when the column is already gone.

Parent epic: bu-sbdwt (entity-keyed-preferred-channel).
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
    / "core_123_drop_contacts_preferred_channel.py"
)
_REL_003_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "003_consolidate_contacts_to_public.py"
)


def _load_migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
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
        mod = _load_migration(_MIGRATION_PATH, "_core_123")
        assert mod.revision == "core_123"
        assert mod.down_revision == "core_122"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_force_env_parsing(self, monkeypatch):
        mod = _load_migration(_MIGRATION_PATH, "_core_123")
        monkeypatch.delenv("PREFERRED_CHANNEL_DROP_FORCE", raising=False)
        assert mod._forced() is False
        monkeypatch.setenv("PREFERRED_CHANNEL_DROP_FORCE", "1")
        assert mod._forced() is True

    def test_source_drops_column_snapshots_and_guards(self):
        src = _MIGRATION_PATH.read_text()
        # The actual destructive op.
        assert "DROP COLUMN IF EXISTS preferred_channel" in src
        # Self-guarding: snapshot + force override.
        assert "contacts_preferred_channel_dropbak_core_123" in src
        assert "PREFERRED_CHANNEL_DROP_FORCE" in src
        # Cross-chain guard against the fact store + column existence.
        assert "to_regclass('relationship.entity_facts')" in src
        assert "information_schema.columns" in src

    def test_rel_003_omits_preferred_channel_when_column_absent(self):
        src = _REL_003_PATH.read_text()
        # Cross-chain guard: detect column presence, conditionally include it.
        assert "information_schema.columns" in src
        assert "column_name  = 'preferred_channel'" in src
        assert "pref_col" in src

    def test_rel_003_snapshots_relationship_prefs_before_drop(self):
        # When core_123 runs first and the public column is gone, the copy omits
        # preferred_channel; rel_003 must snapshot non-null relationship.contacts
        # preferences before DROP TABLE so they are not silently lost (they cannot
        # be backfilled to facts here — entity_facts is created later, in rel_013).
        src = _REL_003_PATH.read_text()
        assert "contacts_preferred_channel_dropbak_rel_003" in src
        # The snapshot must precede the DROP TABLE in source order.
        assert src.index("contacts_preferred_channel_dropbak_rel_003") < src.index(
            "DROP TABLE relationship.contacts"
        )

    def test_rel_003_downgrade_also_guards_preferred_channel(self):
        # The downgrade copies contacts back from public.contacts and would
        # SELECT the dropped column unguarded; it must use the same presence
        # check so a post-core_123 downgrade stays order-independent.
        src = _REL_003_PATH.read_text()
        assert "_public_contacts_has_preferred_channel" in src
        # Used in both upgrade and downgrade (>= 2 call sites + the definition).
        assert src.count("_public_contacts_has_preferred_channel") >= 3
        assert "pref_select" in src


# ---------------------------------------------------------------------------
# (b) Integration: backfill + parity behaviour against a live DB
# ---------------------------------------------------------------------------

# provisioned_postgres_pool() only creates the DB + extensions; the migration
# chain is not run here. Build the minimal schema the backfill/parity reference.
_PROVISION_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS relationship;

CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.contacts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    entity_id         UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    preferred_channel VARCHAR
        CONSTRAINT contacts_preferred_channel_check
        CHECK (preferred_channel IN ('telegram', 'email')),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS relationship.entity_facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject     UUID NOT NULL,
    predicate   TEXT NOT NULL,
    object      TEXT,
    object_kind TEXT NOT NULL DEFAULT 'literal',
    src         TEXT,
    conf        FLOAT NOT NULL DEFAULT 1.0,
    verified    BOOL NOT NULL DEFAULT false,
    validity    TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _mk_contact(pool, name, channel, *, entity: bool = True):
    entity_id = None
    if entity:
        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id", name
        )
    contact_id = await pool.fetchval(
        "INSERT INTO public.contacts (name, entity_id, preferred_channel) "
        "VALUES ($1, $2, $3) RETURNING id",
        name,
        entity_id,
        channel,
    )
    return entity_id, contact_id


async def _active_channel_facts(pool, subject):
    return await pool.fetch(
        "SELECT object FROM relationship.entity_facts "
        "WHERE subject = $1 AND predicate = 'prefers-channel' AND validity = 'active'",
        subject,
    )


def _backfill_sql():
    mod = _load_migration(_MIGRATION_PATH, "_core_123")
    return mod._BACKFILL_SQL.text.replace(":predicate", "$1")


def _parity_sql():
    mod = _load_migration(_MIGRATION_PATH, "_core_123")
    return mod._PARITY_SQL.text.replace(":predicate", "$1")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_inserts_one_fact_per_entity_linked_contact(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        e1, _ = await _mk_contact(pool, "alice", "email")
        e2, _ = await _mk_contact(pool, "bob", "telegram")
        # Orphan contact (no entity) — must be skipped, not error.
        await _mk_contact(pool, "carol", "email", entity=False)
        # Contact with no preference — must be skipped.
        await _mk_contact(pool, "dave", None)

        await pool.execute(_backfill_sql(), "prefers-channel")

        a = await _active_channel_facts(pool, e1)
        b = await _active_channel_facts(pool, e2)
        assert [r["object"] for r in a] == ["email"]
        assert [r["object"] for r in b] == ["telegram"]

        total = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE predicate = 'prefers-channel'"
        )
        assert total == 2, "exactly one fact per entity-linked, preference-bearing contact"

        # Parity is clean.
        gap = await pool.fetchval(_parity_sql(), "prefers-channel")
        assert gap == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_collapses_multiple_contacts_to_single_fact(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('shared') RETURNING id"
        )
        # Two contacts share one entity; the most-recently-updated wins.
        await pool.execute(
            "INSERT INTO public.contacts (name, entity_id, preferred_channel, updated_at) "
            "VALUES ('older', $1, 'email', now() - interval '1 day')",
            entity_id,
        )
        await pool.execute(
            "INSERT INTO public.contacts (name, entity_id, preferred_channel, updated_at) "
            "VALUES ('newer', $1, 'telegram', now())",
            entity_id,
        )

        await pool.execute(_backfill_sql(), "prefers-channel")

        facts = await _active_channel_facts(pool, entity_id)
        assert len(facts) == 1, "single-valued: one active fact per entity"
        assert facts[0]["object"] == "telegram", "most-recently-updated contact wins"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_does_not_overwrite_live_fact(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        e1, _ = await _mk_contact(pool, "alice", "email")
        # A live entity-keyed preference already exists (the authoritative path).
        await pool.execute(
            "INSERT INTO relationship.entity_facts "
            "(subject, predicate, object, object_kind, src, validity) "
            "VALUES ($1, 'prefers-channel', 'telegram', 'literal', 'relationship', 'active')",
            e1,
        )

        await pool.execute(_backfill_sql(), "prefers-channel")

        facts = await _active_channel_facts(pool, e1)
        assert len(facts) == 1, "must not insert a second active fact"
        assert facts[0]["object"] == "telegram", "live preference wins over the column value"
        # Parity is still clean (the entity has an active fact).
        gap = await pool.fetchval(_parity_sql(), "prefers-channel")
        assert gap == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_is_idempotent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)
        e1, _ = await _mk_contact(pool, "alice", "email")

        await pool.execute(_backfill_sql(), "prefers-channel")
        await pool.execute(_backfill_sql(), "prefers-channel")

        facts = await _active_channel_facts(pool, e1)
        assert len(facts) == 1, "re-running the backfill must not duplicate the fact"
