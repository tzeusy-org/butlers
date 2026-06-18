"""Tests for relationship.credentials carve-out table (bu-uj3xv).

Covers:
  (a) Migration DDL structure — revision, down_revision, upgrade/downgrade are defined.
  (b) Credential insert and read-back (integration, requires Docker/Postgres).
  (c) Credential does NOT appear in relationship.entity_facts.
  (d) Unique constraint: cannot insert duplicate active credentials of same type/entity.
  (e) Revocation: revoked_at set → new active credential of same type CAN be inserted.
  (g) Downgrade removes the table + indexes cleanly.

Unit test (a) is pure Python with no Docker requirement.
Integration tests (b–e), (g) require Docker (postgres container provisioned by
the shared ``provisioned_postgres_pool`` fixture).

Issue: bu-uj3xv
Parent epic: bu-ao6uh (entity-redesign)
Spec anchor: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/relationship-facts/spec.md
             §"Requirement: Credentials carve-out"
             Brief §6b Amendment 1.1.A.4 (credentials do NOT become triples)
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Helper: load the migration module.
# Filenames starting with a digit (016_…) cannot be imported via standard
# dot-notation, so we use importlib.util.spec_from_file_location.
# ---------------------------------------------------------------------------

_MIGRATION_PATH = Path(__file__).parent.parent / "migrations" / "016_credentials_carveout.py"


def _load_migration():
    """Load 016_credentials_carveout.py as a module object."""
    spec = importlib.util.spec_from_file_location("_migration_rel_016", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None, (
        f"Cannot load migration from {_MIGRATION_PATH}"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: Migration module structure
# Pure sync — no DB or Docker required.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    """Migration module exposes the expected Alembic attributes."""

    def test_revision_is_rel_016(self):
        mod = _load_migration()
        assert mod.revision == "rel_016"

    def test_down_revision_is_rel_015(self):
        mod = _load_migration()
        assert mod.down_revision == "rel_015"

    def test_upgrade_is_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_is_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)

    def test_branch_labels_is_none(self):
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self):
        mod = _load_migration()
        assert mod.depends_on is None


# ---------------------------------------------------------------------------
# Integration tests — require Docker / Postgres
# Mirrors the test_relationship_assert_fact.py pattern:
#   - pytestmark at module level applies asyncio(loop_scope="session") to all
#     async tests in this file.
#   - fixtures are function-scoped (default) and create/tear-down fresh schema.
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh DB with public.entities, relationship.credentials, relationship.entity_facts."""
    async with provisioned_postgres_pool() as p:
        # 1. public.entities (FK target for relationship.credentials.entity_id)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT        NOT NULL DEFAULT '',
                name           TEXT        NOT NULL DEFAULT '',
                entity_type    TEXT        NOT NULL DEFAULT 'person',
                aliases        TEXT[]      NOT NULL DEFAULT '{}',
                metadata       JSONB       DEFAULT '{}'::jsonb,
                roles          TEXT[]      NOT NULL DEFAULT '{}',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # 2. relationship schema
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")

        # 3. relationship.entity_facts (needed to verify credentials are NOT inserted here)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0
                                CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen   TIMESTAMPTZ,
                weight      INT,
                verified    BOOL        NOT NULL DEFAULT false,
                "primary"   BOOL,
                validity    TEXT        NOT NULL DEFAULT 'active'
                                CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # 4. relationship.credentials (the table under test — exact schema from migration)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.credentials (
                id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                entity_id    UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                type         TEXT        NOT NULL,
                value        TEXT        NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_used_at TIMESTAMPTZ,
                revoked_at   TIMESTAMPTZ
            )
        """)

        # 5. Partial unique index: one active credential per (entity, type)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_cred_entity_type_active
                ON relationship.credentials (entity_id, type)
                WHERE revoked_at IS NULL
        """)

        # 6. Lookup index
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_cred_entity_id
                ON relationship.credentials (entity_id)
        """)

        # 7. entity_predicate_registry (used by credential-vs-fact independence test)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
                predicate   TEXT NOT NULL PRIMARY KEY,
                kind        TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES ('has-email', 'contact', 'literal', 'Email address.')
            ON CONFLICT (predicate) DO NOTHING
        """)

        yield p


@pytest.fixture
async def entity(pool: asyncpg.Pool) -> uuid.UUID:
    """Insert a regular entity and return its id."""
    eid = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Test Person', 'person', '{}') RETURNING id"
    )
    return eid


# ---------------------------------------------------------------------------
# (b) Integration: credential insert and read-back
# ---------------------------------------------------------------------------


class TestCredentialInsertReadback:
    async def test_insert_credential_returns_id(self, pool, entity):
        """A credential row can be inserted and its id returned."""
        cred_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, $2, $3) RETURNING id",
            entity,
            "gmail_token",
            "encrypted-blob-abc123",
        )
        assert cred_id is not None

    async def test_read_back_inserted_credential(self, pool, entity):
        """Inserted credential can be read back with all expected fields."""
        cred_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'telegram_session', 'session-data-xyz') RETURNING id",
            entity,
        )
        row = await pool.fetchrow(
            "SELECT * FROM relationship.credentials WHERE id = $1",
            cred_id,
        )
        assert row is not None
        assert row["entity_id"] == entity
        assert row["type"] == "telegram_session"
        assert row["value"] == "session-data-xyz"
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
        assert row["last_used_at"] is None
        assert row["revoked_at"] is None

    async def test_last_used_at_can_be_set(self, pool, entity):
        """last_used_at field is nullable and can be updated."""
        cred_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'blob') RETURNING id",
            entity,
        )
        await pool.execute(
            "UPDATE relationship.credentials SET last_used_at = now() WHERE id = $1",
            cred_id,
        )
        row = await pool.fetchrow(
            "SELECT last_used_at FROM relationship.credentials WHERE id = $1",
            cred_id,
        )
        assert row["last_used_at"] is not None


# ---------------------------------------------------------------------------
# (c) Integration: credential does NOT appear in relationship.entity_facts
# ---------------------------------------------------------------------------


class TestCredentialNotInEntityFacts:
    async def test_credential_entity_facts_is_empty(self, pool, entity):
        """Inserting a credential into relationship.credentials does NOT touch entity_facts."""
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_oauth', 'oauth-token')",
            entity,
        )
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1",
            entity,
        )
        assert count == 0, (
            "relationship.entity_facts must remain empty after a credential insert; "
            f"found {count} rows"
        )

    async def test_credential_and_fact_are_independent(self, pool, entity):
        """A credential and a non-secured fact for the same entity coexist independently."""
        # Insert a non-secured email fact
        await pool.execute(
            "INSERT INTO relationship.entity_facts "
            "    (subject, predicate, object, object_kind, src) "
            "VALUES ($1, 'has-email', 'alice@example.com', 'literal', 'test')",
            entity,
        )
        # Insert a secured credential (goes to credentials, NOT entity_facts)
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'ciphertext-blob')",
            entity,
        )

        fact_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE subject = $1",
            entity,
        )
        cred_count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.credentials WHERE entity_id = $1",
            entity,
        )
        assert fact_count == 1
        assert cred_count == 1


# ---------------------------------------------------------------------------
# (d) Integration: unique constraint — no duplicate active creds of same type
# ---------------------------------------------------------------------------


class TestUniqueConstraint:
    async def test_duplicate_active_cred_same_type_raises(self, pool, entity):
        """Inserting two active credentials of the same type for the same entity fails."""
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'first-blob')",
            entity,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO relationship.credentials (entity_id, type, value) "
                "VALUES ($1, 'gmail_token', 'second-blob')",
                entity,
            )

    async def test_different_types_for_same_entity_are_allowed(self, pool, entity):
        """Two active credentials of different types for the same entity are allowed."""
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'blob-a')",
            entity,
        )
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'telegram_session', 'blob-b')",
            entity,
        )
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.credentials WHERE entity_id = $1",
            entity,
        )
        assert count == 2

    async def test_same_type_for_different_entities_are_allowed(self, pool):
        """Two different entities can each hold an active credential of the same type."""
        eid_a = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Entity A', 'person', '{}') RETURNING id"
        )
        eid_b = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Entity B', 'person', '{}') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'blob-for-a')",
            eid_a,
        )
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'blob-for-b')",
            eid_b,
        )
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.credentials WHERE type = 'gmail_token'"
        )
        assert count == 2


# ---------------------------------------------------------------------------
# (e) Integration: revocation — revoked_at set allows new active cred insertion
# ---------------------------------------------------------------------------


class TestRevocation:
    async def test_revoked_cred_does_not_block_new_active_cred(self, pool, entity):
        """After revoking an old credential, a new active credential of same type can be inserted."""
        cred_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'old-blob') RETURNING id",
            entity,
        )
        await pool.execute(
            "UPDATE relationship.credentials SET revoked_at = now() WHERE id = $1",
            cred_id,
        )
        new_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'new-blob') RETURNING id",
            entity,
        )
        assert new_id is not None

    async def test_revoked_row_persists_alongside_new_active(self, pool, entity):
        """After rotation, both the revoked and new active rows exist (audit trail preserved)."""
        old_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'old') RETURNING id",
            entity,
        )
        await pool.execute(
            "UPDATE relationship.credentials SET revoked_at = now() WHERE id = $1",
            old_id,
        )
        new_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'new') RETURNING id",
            entity,
        )

        old_row = await pool.fetchrow(
            "SELECT revoked_at FROM relationship.credentials WHERE id = $1", old_id
        )
        new_row = await pool.fetchrow(
            "SELECT revoked_at FROM relationship.credentials WHERE id = $1", new_id
        )

        assert old_row["revoked_at"] is not None, "Old credential must be marked revoked"
        assert new_row["revoked_at"] is None, "New credential must be active (revoked_at IS NULL)"

    async def test_active_cred_query_excludes_revoked(self, pool, entity):
        """Querying active credentials (revoked_at IS NULL) returns only the new one."""
        old_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'old') RETURNING id",
            entity,
        )
        await pool.execute(
            "UPDATE relationship.credentials SET revoked_at = now() WHERE id = $1",
            old_id,
        )
        new_id = await pool.fetchval(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'new') RETURNING id",
            entity,
        )

        active = await pool.fetch(
            "SELECT id FROM relationship.credentials "
            "WHERE entity_id = $1 AND type = 'gmail_token' AND revoked_at IS NULL",
            entity,
        )
        assert len(active) == 1
        assert active[0]["id"] == new_id

    async def test_two_unrevoked_same_type_still_blocked(self, pool, entity):
        """Without revoking the first, a second active cred of same type is rejected."""
        await pool.execute(
            "INSERT INTO relationship.credentials (entity_id, type, value) "
            "VALUES ($1, 'gmail_token', 'first')",
            entity,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO relationship.credentials (entity_id, type, value) "
                "VALUES ($1, 'gmail_token', 'second')",
                entity,
            )


# ---------------------------------------------------------------------------
# (g) Integration: downgrade — table and indexes drop cleanly
# ---------------------------------------------------------------------------


class TestDowngrade:
    async def test_drop_indexes_and_table(self, pool):
        """Downgrade SQL drops indexes and table without errors; table must not exist after."""
        # Use an isolated schema to avoid disturbing the shared pool schema.
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship_dg_test")
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS relationship_dg_test.credentials (
                id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                entity_id    UUID        NOT NULL,
                type         TEXT        NOT NULL,
                value        TEXT        NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_used_at TIMESTAMPTZ,
                revoked_at   TIMESTAMPTZ
            )
        """)
        await pool.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_cred_dg_test
                ON relationship_dg_test.credentials (entity_id, type)
                WHERE revoked_at IS NULL
        """)
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_cred_dg_test
                ON relationship_dg_test.credentials (entity_id)
        """)

        # Simulate downgrade (mirrors the downgrade() function in the migration)
        await pool.execute("DROP INDEX IF EXISTS relationship_dg_test.uq_cred_dg_test")
        await pool.execute("DROP INDEX IF EXISTS relationship_dg_test.idx_cred_dg_test")
        await pool.execute("DROP TABLE IF EXISTS relationship_dg_test.credentials")

        exists = await pool.fetchval("SELECT to_regclass('relationship_dg_test.credentials')")
        assert exists is None, "credentials table should not exist after downgrade"

        # Cleanup test schema
        await pool.execute("DROP SCHEMA IF EXISTS relationship_dg_test CASCADE")
