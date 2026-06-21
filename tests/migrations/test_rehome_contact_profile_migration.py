"""Tests for rel_031 — re-home contact-only columns onto public.entities (bu-v4c39,
Phase 7.4a of the contacts retirement epic bu-oluyt).

Covers:
  (a) Module structure — revision/down_revision chain, callables, and that the
      source contains the to_regclass guard, snapshot table name, idempotent DDL,
      parity RAISE statements, and the additive merge strategy.  Pure unit, no DB.
  (b) Backfill behaviour against a live DB (Docker/Postgres):
      - stay_in_touch_days column is added to entities
      - Backfill populates stay_in_touch_days from linked contacts
      - Backfill populates metadata['profile'] from linked contacts
      - Existing metadata['profile'] keys are NOT overwritten (additive)
      - NULL profile fields are not written into metadata
      - Gender and pronouns (not in Google Contacts) are backfilled from contacts
      - Guard no-ops cleanly when public.contacts is absent
      - Backfill is idempotent (running upgrade twice yields same result)
      - Downgrade restores metadata['profile'] and drops the column

Parent: bu-oluyt (retire public.contacts).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "031_rehome_contact_profile_columns.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_rel_031", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: module structure + source guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    """Chain + the parity-RAISE invariant (no integration test triggers a
    parity mismatch, so this source guard is the only proof of the abort)."""

    def test_revision_chain(self):
        mod = _load_migration()
        assert mod.revision == "rel_031"
        assert mod.down_revision == "rel_030"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_parity_raise_exceptions_present(self):
        sql = _load_migration()._BACKFILL_AND_PARITY_SQL
        # Both parity assertions must be able to abort the migration.
        assert sql.count("RAISE EXCEPTION") >= 2
        assert "parity failure (stay_in_touch_days)" in sql
        assert "parity failure (avatar_url)" in sql

    def test_to_regclass_guard_and_snapshot_constant(self):
        """Forward-compat to_regclass guard + the snapshot table name the
        downgrade restores from."""
        mod = _load_migration()
        assert "to_regclass('public.contacts')" in mod._BACKFILL_AND_PARITY_SQL
        assert mod._SNAPSHOT_TABLE == "public.entities_contact_profile_bak_rel_031"
        assert "entities_contact_profile_bak_rel_031" in mod._DOWNGRADE_SQL


# ---------------------------------------------------------------------------
# (b) Integration: backfill + downgrade behaviour against a live DB
# ---------------------------------------------------------------------------

# Minimal schema that mirrors the columns the migration touches.
# public.entities intentionally omits stay_in_touch_days — the migration adds it.
_PROVISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name VARCHAR     NOT NULL DEFAULT '',
    metadata       JSONB       DEFAULT '{}'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.contacts (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name               TEXT        NOT NULL,
    entity_id          UUID        REFERENCES public.entities(id) ON DELETE SET NULL,
    stay_in_touch_days INTEGER,
    first_name         VARCHAR,
    last_name          VARCHAR,
    company            VARCHAR,
    job_title          VARCHAR,
    gender             VARCHAR,
    pronouns           VARCHAR,
    avatar_url         VARCHAR,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _run_upgrade(pool):
    mod = _load_migration()
    await pool.execute(mod._ADD_COLUMN_SQL)
    await pool.execute(mod._BACKFILL_AND_PARITY_SQL)


async def _run_downgrade(pool):
    mod = _load_migration()
    await pool.execute(mod._DOWNGRADE_SQL)
    await pool.execute("ALTER TABLE public.entities DROP COLUMN IF EXISTS stay_in_touch_days")


async def _sit(pool, entity_id):
    """Read stay_in_touch_days for an entity."""
    return await pool.fetchval(
        "SELECT stay_in_touch_days FROM public.entities WHERE id = $1", entity_id
    )


async def _profile(pool, entity_id):
    """Read metadata['profile'] for an entity as a dict (or None)."""
    row = await pool.fetchrow(
        "SELECT metadata -> 'profile' AS profile FROM public.entities WHERE id = $1", entity_id
    )
    if row is None:
        return None
    raw = row["profile"]
    if raw is None:
        return None
    import json

    return json.loads(raw) if isinstance(raw, str) else raw


async def _column_exists(pool):
    """Return True if public.entities.stay_in_touch_days column exists."""
    return (
        await pool.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'entities'
              AND column_name  = 'stay_in_touch_days'
            """
        )
        is not None
    )


_skip_no_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_column_added_by_upgrade(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        assert not await _column_exists(pool), "column should not exist before upgrade"
        await _run_upgrade(pool)
        assert await _column_exists(pool), "column should exist after upgrade"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_backfill_stay_in_touch_days(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Alice') RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, stay_in_touch_days)
            VALUES ('Alice Smith', $1, 30)
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        assert await _sit(pool, entity_id) == 30


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_backfill_profile_metadata(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Bob') RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO public.contacts
                (name, entity_id, first_name, last_name, company, job_title,
                 gender, pronouns, avatar_url)
            VALUES ('Bob Jones', $1, 'Bob', 'Jones', 'Acme', 'Engineer',
                    'male', 'he/him', 'https://example.com/bob.jpg')
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        profile = await _profile(pool, entity_id)
        assert profile is not None
        assert profile["first_name"] == "Bob"
        assert profile["last_name"] == "Jones"
        assert profile["company"] == "Acme"
        assert profile["job_title"] == "Engineer"
        assert profile["gender"] == "male"
        assert profile["pronouns"] == "he/him"
        assert profile["avatar_url"] == "https://example.com/bob.jpg"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_additive_existing_profile_keys_not_overwritten(provisioned_postgres_pool) -> None:
    """Existing metadata.profile keys (e.g. from Google Contacts) must not be overwritten."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            """
            INSERT INTO public.entities (canonical_name, metadata)
            VALUES ('Carol', '{"profile": {"avatar_url": "https://google.com/carol.jpg", "first_name": "Carol-GC"}}')
            RETURNING id
            """
        )
        # Contact has different avatar_url and first_name from what Google Contacts wrote.
        await pool.execute(
            """
            INSERT INTO public.contacts
                (name, entity_id, first_name, avatar_url)
            VALUES ('Carol', $1, 'Carol-Contacts', 'https://old.example.com/carol.jpg')
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        profile = await _profile(pool, entity_id)
        assert profile is not None
        # Existing keys win — Google Contacts version must be preserved.
        assert profile["avatar_url"] == "https://google.com/carol.jpg", (
            "existing avatar_url overwritten"
        )
        assert profile["first_name"] == "Carol-GC", "existing first_name overwritten"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_null_profile_fields_not_written_to_metadata(provisioned_postgres_pool) -> None:
    """NULL contact profile fields must not appear in entities.metadata['profile']."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Dave') RETURNING id"
        )
        # Only first_name is set; all others are NULL.
        await pool.execute(
            "INSERT INTO public.contacts (name, entity_id, first_name) VALUES ('Dave', $1, 'Dave')",
            entity_id,
        )

        await _run_upgrade(pool)

        profile = await _profile(pool, entity_id)
        assert profile is not None
        assert profile.get("first_name") == "Dave"
        # NULL fields must not appear in the profile (jsonb_strip_nulls).
        for field in ("last_name", "company", "job_title", "gender", "pronouns", "avatar_url"):
            assert field not in profile, f"null field '{field}' written to metadata"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_gender_pronouns_backfilled(provisioned_postgres_pool) -> None:
    """Gender and pronouns (not in Google Contacts) must be backfilled from contacts."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Eve') RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, gender, pronouns)
            VALUES ('Eve', $1, 'female', 'she/her')
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        profile = await _profile(pool, entity_id)
        assert profile is not None
        assert profile["gender"] == "female"
        assert profile["pronouns"] == "she/her"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_contact_without_entity_skipped(provisioned_postgres_pool) -> None:
    """Contacts with entity_id IS NULL must be silently ignored."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, stay_in_touch_days)
            VALUES ('Orphan', NULL, 14)
            """
        )

        await _run_upgrade(pool)
        # No exception — and no entities were touched.
        count = await pool.fetchval("SELECT COUNT(*) FROM public.entities")
        assert count == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_guard_noop_when_contacts_absent(provisioned_postgres_pool) -> None:
    """Migration must no-op cleanly when public.contacts does not exist."""
    async with provisioned_postgres_pool() as pool:
        # Provision only entities (no contacts table).
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR     NOT NULL DEFAULT '',
                metadata       JSONB       DEFAULT '{}'::jsonb,
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # Should run without raising an exception.
        await _run_upgrade(pool)

        # Column should still have been added (DDL runs before the guarded DO block).
        assert await _column_exists(pool)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_upgrade_is_idempotent(provisioned_postgres_pool) -> None:
    """Running upgrade twice must produce the same result as running it once."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Frank') RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO public.contacts
                (name, entity_id, stay_in_touch_days, first_name, avatar_url)
            VALUES ('Frank', $1, 21, 'Frank', 'https://example.com/frank.jpg')
            """,
            entity_id,
        )

        await _run_upgrade(pool)
        # Second run must not raise and must yield the same values.
        await _run_upgrade(pool)

        assert await _sit(pool, entity_id) == 21
        profile = await _profile(pool, entity_id)
        assert profile["first_name"] == "Frank"
        assert profile["avatar_url"] == "https://example.com/frank.jpg"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_most_recently_updated_contact_wins(provisioned_postgres_pool) -> None:
    """When an entity has multiple contacts, the most-recently-updated one wins for backfill."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name) VALUES ('Grace') RETURNING id"
        )
        # Older contact with a higher stay_in_touch_days value.
        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, stay_in_touch_days, updated_at)
            VALUES ('Grace-old', $1, 60, now() - interval '2 days')
            """,
            entity_id,
        )
        # Newer contact with a lower stay_in_touch_days value — must win.
        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, stay_in_touch_days, updated_at)
            VALUES ('Grace-new', $1, 14, now())
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        assert await _sit(pool, entity_id) == 14


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_downgrade_drops_column_and_restores_profile(provisioned_postgres_pool) -> None:
    """Downgrade removes stay_in_touch_days and restores pre-upgrade metadata['profile']."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            """
            INSERT INTO public.entities (canonical_name, metadata)
            VALUES ('Henry', '{"existing_key": "keep_me"}')
            RETURNING id
            """
        )
        await pool.execute(
            """
            INSERT INTO public.contacts
                (name, entity_id, stay_in_touch_days, first_name)
            VALUES ('Henry', $1, 30, 'Henry')
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        # Sanity: column and backfill exist after upgrade.
        assert await _column_exists(pool)
        assert await _sit(pool, entity_id) == 30

        await _run_downgrade(pool)

        # Column should be gone.
        assert not await _column_exists(pool)

        # Existing non-profile metadata must survive downgrade.
        meta = await pool.fetchval("SELECT metadata FROM public.entities WHERE id = $1", entity_id)
        import json

        meta_dict = json.loads(meta) if isinstance(meta, str) else meta
        assert meta_dict.get("existing_key") == "keep_me"

        # Profile key should be gone (entity had no profile before upgrade).
        assert "profile" not in meta_dict


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_skip_no_docker
async def test_downgrade_preserves_pre_existing_profile(provisioned_postgres_pool) -> None:
    """Downgrade must restore a profile that existed before the upgrade ran."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        entity_id = await pool.fetchval(
            """
            INSERT INTO public.entities (canonical_name, metadata)
            VALUES ('Iris', '{"profile": {"avatar_url": "https://pre-existing.example.com/iris.jpg"}}')
            RETURNING id
            """
        )
        # Contact adds a new profile field that entity didn't have.
        await pool.execute(
            """
            INSERT INTO public.contacts (name, entity_id, first_name, avatar_url)
            VALUES ('Iris', $1, 'Iris', 'https://contact.example.com/iris.jpg')
            """,
            entity_id,
        )

        await _run_upgrade(pool)

        # After upgrade: first_name was added; existing avatar_url preserved.
        profile_after = await _profile(pool, entity_id)
        assert profile_after["avatar_url"] == "https://pre-existing.example.com/iris.jpg"
        assert profile_after.get("first_name") == "Iris"

        await _run_downgrade(pool)

        # After downgrade: profile restored to pre-upgrade state.
        profile_restored = await _profile(pool, entity_id)
        assert profile_restored is not None
        assert profile_restored["avatar_url"] == "https://pre-existing.example.com/iris.jpg"
        # first_name was not in the pre-upgrade profile — it should be gone after downgrade.
        assert "first_name" not in profile_restored
