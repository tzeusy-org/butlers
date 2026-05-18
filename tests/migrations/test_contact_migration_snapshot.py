"""Tests for src/butlers/scripts/contact_migration_snapshot.py.

Covers:
- Snapshot tables are created with all rows from the source tables.
- Row counts in the snapshot match the source.
- Idempotency: re-running the script is a no-op when snapshots already exist.
- Per-type breakdown for contact_info is correct.
- Orphan count (entity_id IS NULL) is accurate.
- Report is written and contains expected content.
- dry_run=True does not create tables or write the report.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers to create the public schema tables used by the snapshot script
# ---------------------------------------------------------------------------

_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS public.entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  VARCHAR NOT NULL,
    entity_type     VARCHAR NOT NULL DEFAULT 'other',
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    metadata        JSONB DEFAULT '{}'::jsonb,
    roles           TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS public.contacts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    details           JSONB DEFAULT '{}',
    first_name        VARCHAR,
    last_name         VARCHAR,
    nickname          VARCHAR,
    company           VARCHAR,
    job_title         VARCHAR,
    gender            VARCHAR,
    pronouns          VARCHAR,
    avatar_url        VARCHAR,
    listed            BOOLEAN NOT NULL DEFAULT true,
    archived_at       TIMESTAMPTZ,
    metadata          JSONB,
    stay_in_touch_days INTEGER,
    entity_id         UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    preferred_channel VARCHAR,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_CONTACT_INFO = """
CREATE TABLE IF NOT EXISTS public.contact_info (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id  UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
    type        VARCHAR NOT NULL,
    value       TEXT NOT NULL,
    label       VARCHAR,
    is_primary  BOOLEAN DEFAULT false,
    secured     BOOLEAN NOT NULL DEFAULT false,
    parent_id   UUID REFERENCES public.contact_info(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT now()
)
"""


async def _setup_schema(pool: asyncpg.Pool) -> None:
    """Create the public identity tables in the test DB."""
    await pool.execute(_CREATE_ENTITIES)
    await pool.execute(_CREATE_CONTACTS)
    await pool.execute(_CREATE_CONTACT_INFO)


async def _seed_contacts(
    pool: asyncpg.Pool,
    *,
    with_entity: int = 2,
    orphans: int = 1,
) -> list[uuid.UUID]:
    """Insert sample contacts; some linked to entities, some orphaned.

    Returns list of all contact IDs (entity-linked first, then orphans).
    """
    contact_ids: list[uuid.UUID] = []

    # Contacts with entities
    for i in range(with_entity):
        entity_id: uuid.UUID = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            f"Entity {i}",
        )
        cid: uuid.UUID = await pool.fetchval(
            "INSERT INTO public.contacts (name, entity_id) VALUES ($1, $2) RETURNING id",
            f"Contact {i}",
            entity_id,
        )
        contact_ids.append(cid)

    # Orphan contacts (no entity)
    for i in range(orphans):
        cid = await pool.fetchval(
            "INSERT INTO public.contacts (name) VALUES ($1) RETURNING id",
            f"Orphan {i}",
        )
        contact_ids.append(cid)

    return contact_ids


async def _seed_contact_info(
    pool: asyncpg.Pool,
    contact_ids: list[uuid.UUID],
) -> None:
    """Insert a variety of contact_info rows across different types.

    Requires at least 3 contact IDs: [0] entity-linked, [1] entity-linked, [2] orphan.
    """
    rows = [
        # (contact_id, type, value, is_primary, secured)
        (contact_ids[0], "email", "alice@example.com", True, False),
        (contact_ids[0], "telegram", "123456789", False, False),
        (contact_ids[1], "email", "bob@example.com", True, False),
        (contact_ids[1], "phone", "+1-555-0100", False, False),
        (contact_ids[1], "google_account", "tok_abc", False, True),  # secured credential
        (contact_ids[2], "email", "orphan@example.com", True, False),  # orphan contact
    ]
    for cid, ci_type, value, is_primary, secured in rows:
        await pool.execute(
            "INSERT INTO public.contact_info "
            "(contact_id, type, value, is_primary, secured) "
            "VALUES ($1, $2, $3, $4, $5)",
            cid,
            ci_type,
            value,
            is_primary,
            secured,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def snapshot_pool(provisioned_postgres_pool):
    """Fresh DB with public identity tables (no data)."""
    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)
        yield pool


@pytest.fixture
async def seeded_pool(provisioned_postgres_pool):
    """Fresh DB with public identity tables and sample data."""
    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)
        contact_ids = await _seed_contacts(pool, with_entity=2, orphans=1)
        await _seed_contact_info(pool, contact_ids)
        yield pool


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------


def _load_script():
    import importlib

    return importlib.import_module("butlers.scripts.contact_migration_snapshot")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_creates_tables(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """run_snapshot creates the two snapshot tables."""
    mod = _load_script()

    rc = await mod.run_snapshot(
        date_label="20260101",
        report_path=tmp_path / "baseline.md",
        dry_run=False,
        _pool=seeded_pool,
    )

    assert rc == 0

    # Verify snapshot tables exist
    contacts_exists = await seeded_pool.fetchval(
        "SELECT to_regclass('public.contacts_pre_migration_20260101')"
    )
    assert contacts_exists is not None, "contacts snapshot table not created"

    ci_exists = await seeded_pool.fetchval(
        "SELECT to_regclass('public.contact_info_pre_migration_20260101')"
    )
    assert ci_exists is not None, "contact_info snapshot table not created"


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_row_counts_match(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """Snapshot tables contain the same number of rows as source tables."""
    mod = _load_script()

    await mod.run_snapshot(
        date_label="20260102",
        report_path=tmp_path / "baseline.md",
        dry_run=False,
        _pool=seeded_pool,
    )

    src_contacts = await seeded_pool.fetchval("SELECT COUNT(*) FROM public.contacts")
    snap_contacts = await seeded_pool.fetchval(
        "SELECT COUNT(*) FROM public.contacts_pre_migration_20260102"
    )
    assert src_contacts == snap_contacts

    src_ci = await seeded_pool.fetchval("SELECT COUNT(*) FROM public.contact_info")
    snap_ci = await seeded_pool.fetchval(
        "SELECT COUNT(*) FROM public.contact_info_pre_migration_20260102"
    )
    assert src_ci == snap_ci


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_idempotency(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """Re-running with the same date label is a no-op (tables already exist)."""
    mod = _load_script()

    rc1 = await mod.run_snapshot(
        date_label="20260103",
        report_path=tmp_path / "baseline.md",
        dry_run=False,
        _pool=seeded_pool,
    )
    # Second run — tables exist; should succeed without error
    rc2 = await mod.run_snapshot(
        date_label="20260103",
        report_path=tmp_path / "baseline2.md",
        dry_run=False,
        _pool=seeded_pool,
    )

    assert rc1 == 0
    assert rc2 == 0

    # Row count should still be the original (no duplication)
    count = await seeded_pool.fetchval(
        "SELECT COUNT(*) FROM public.contacts_pre_migration_20260103"
    )
    src_count = await seeded_pool.fetchval("SELECT COUNT(*) FROM public.contacts")
    assert count == src_count


@pytest.mark.asyncio(loop_scope="session")
async def test_contact_info_breakdown_per_type(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """_contact_info_breakdown returns correct per-type stats."""
    mod = _load_script()

    await mod.run_snapshot(
        date_label="20260104",
        report_path=tmp_path / "baseline.md",
        dry_run=False,
        _pool=seeded_pool,
    )

    breakdown = await mod._contact_info_breakdown(
        seeded_pool, "contact_info_pre_migration_20260104"
    )

    # Convert to a dict keyed by type for easy assertion
    by_type = {r["type"]: r for r in breakdown}

    assert "email" in by_type
    assert by_type["email"]["row_count"] == 3

    assert "telegram" in by_type
    assert by_type["telegram"]["row_count"] == 1

    assert "phone" in by_type
    assert by_type["phone"]["row_count"] == 1

    assert "google_account" in by_type
    assert by_type["google_account"]["row_count"] == 1
    # The google_account row is secured (credential)
    assert by_type["google_account"]["secured_count"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_orphan_count(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """_orphan_count returns the number of contacts with entity_id IS NULL."""
    mod = _load_script()

    await mod.run_snapshot(
        date_label="20260105",
        report_path=tmp_path / "baseline.md",
        dry_run=False,
        _pool=seeded_pool,
    )

    orphans = await mod._orphan_count(seeded_pool, "contacts_pre_migration_20260105")
    # We seeded 1 orphan contact
    assert orphans == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_report_written(seeded_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """run_snapshot writes the baseline report to the specified path."""
    mod = _load_script()

    report_path = tmp_path / "sub" / "contact-migration-baseline.md"

    rc = await mod.run_snapshot(
        date_label="20260106",
        report_path=report_path,
        dry_run=False,
        _pool=seeded_pool,
    )

    assert rc == 0
    assert report_path.exists(), "Report file was not written"

    content = report_path.read_text()
    assert "# Contact Migration Baseline Report" in content
    assert "contacts_pre_migration_20260106" in content
    assert "contact_info_pre_migration_20260106" in content
    # Row counts section
    assert "Row counts" in content
    # Breakdown section
    assert "contact_info breakdown" in content


@pytest.mark.asyncio(loop_scope="session")
async def test_dry_run_does_not_create_tables(snapshot_pool: asyncpg.Pool, tmp_path: Path) -> None:
    """dry_run=True does not create snapshot tables or write the report."""
    mod = _load_script()

    # Seed a contact so COUNT(*) can run
    await pool_execute_seed(snapshot_pool)

    report_path = tmp_path / "dry-run-baseline.md"

    rc = await mod.run_snapshot(
        date_label="20260107",
        report_path=report_path,
        dry_run=True,
        _pool=snapshot_pool,
    )

    assert rc == 0

    # Tables should NOT exist
    contacts_exists = await snapshot_pool.fetchval(
        "SELECT to_regclass('public.contacts_pre_migration_20260107')"
    )
    assert contacts_exists is None, "dry_run should not create contacts snapshot"

    ci_exists = await snapshot_pool.fetchval(
        "SELECT to_regclass('public.contact_info_pre_migration_20260107')"
    )
    assert ci_exists is None, "dry_run should not create contact_info snapshot"

    # Report should NOT be written
    assert not report_path.exists(), "dry_run should not write the report"


async def pool_execute_seed(pool: asyncpg.Pool) -> None:
    """Insert one contact (no entity) to ensure source tables are non-empty."""
    await pool.execute(
        "INSERT INTO public.contacts (name) VALUES ($1)",
        "Test Contact",
    )
