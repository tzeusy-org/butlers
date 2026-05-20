"""Integration tests for src/butlers/scripts/contact_orphan_resolver.py.

Covers:
- dry-run mode produces no writes and emits a report.
- apply mode with canonical-name signal mints an entity and backfills.
- apply mode with no name signal marks row as deferred (notify attempt).
- missing snapshot table returns rc=1.
- invalid date label returns rc=1.
- report file is written with correct content.
- contacts with entity_id already set are not included in orphan list.
- report includes per-row outcomes for both minted and deferred rows.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Schema helpers (mirror test_contact_migration_snapshot.py)
# ---------------------------------------------------------------------------

_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS public.entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  VARCHAR NOT NULL DEFAULT '',
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
    roles             TEXT[] NOT NULL DEFAULT '{}',
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

_DATE_LABEL = "20260901"
_SNAPSHOT_TABLE = f"contacts_pre_migration_{_DATE_LABEL}"


async def _setup_schema(pool: asyncpg.Pool) -> None:
    """Create the public identity tables in the test DB."""
    await pool.execute(_CREATE_ENTITIES)
    await pool.execute(_CREATE_CONTACTS)
    await pool.execute(_CREATE_CONTACT_INFO)


async def _create_snapshot_from_contacts(pool: asyncpg.Pool, date_label: str) -> None:
    """Create a contacts snapshot table mirroring the current public.contacts."""
    snap = f"contacts_pre_migration_{date_label}"
    await pool.execute(
        f'CREATE TABLE IF NOT EXISTS public."{snap}" AS SELECT * FROM public.contacts'
    )


async def _insert_contact(
    pool: asyncpg.Pool,
    *,
    name: str,
    first_name: str | None = None,
    last_name: str | None = None,
    nickname: str | None = None,
    company: str | None = None,
    entity_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.contacts
            (name, first_name, last_name, nickname, company, entity_id, roles)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        name,
        first_name,
        last_name,
        nickname,
        company,
        entity_id,
        roles or [],
    )


async def _insert_entity(pool: asyncpg.Pool, canonical_name: str = "Test Entity") -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') RETURNING id",
        canonical_name,
    )


# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------


def _load_script():
    import importlib

    return importlib.import_module("butlers.scripts.contact_orphan_resolver")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def resolver_pool(provisioned_postgres_pool):
    """Fresh DB with public identity tables, no data."""
    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)
        yield pool


@pytest.fixture
async def seeded_pool_with_orphans(provisioned_postgres_pool):
    """DB with one linked contact, one orphan with a name, one orphan without."""
    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        # Contact with entity (should be ignored by resolver)
        eid = await _insert_entity(pool, "Alice Linked")
        await _insert_contact(pool, name="Alice Linked", first_name="Alice", entity_id=eid)

        # Orphan with a canonical name signal
        await _insert_contact(
            pool,
            name="Bob Orphan",
            first_name="Bob",
            last_name="Orphan",
        )

        # Orphan with no usable name signal
        await _insert_contact(pool, name="unknown")

        await _create_snapshot_from_contacts(pool, _DATE_LABEL)
        yield pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_dry_run_performs_no_writes(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """dry-run (apply=False) does not write to entities or contacts."""
    mod = _load_script()

    rc = await mod.run_resolver(
        date_label=_DATE_LABEL,
        report_path=tmp_path / "orphans.md",
        apply=False,
        _pool=seeded_pool_with_orphans,
    )

    assert rc == 0

    # No new entities should be minted
    count_before = await seeded_pool_with_orphans.fetchval("SELECT COUNT(*) FROM public.entities")
    assert count_before == 1, "dry-run must not mint new entities"

    # Orphan contacts must still have entity_id IS NULL
    orphan_count = await seeded_pool_with_orphans.fetchval(
        "SELECT COUNT(*) FROM public.contacts WHERE entity_id IS NULL"
    )
    assert orphan_count == 2, "dry-run must not backfill entity_id on contacts"


@pytest.mark.asyncio(loop_scope="session")
async def test_dry_run_writes_report(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """dry-run writes a report file even though no DB writes occur."""
    mod = _load_script()
    report_path = tmp_path / "sub" / "orphans.md"

    rc = await mod.run_resolver(
        date_label=_DATE_LABEL,
        report_path=report_path,
        apply=False,
        _pool=seeded_pool_with_orphans,
    )

    assert rc == 0
    assert report_path.exists(), "Report must be written in dry-run mode"

    content = report_path.read_text()
    assert "DRY-RUN" in content
    assert "# Contact Migration Orphan Resolver Report" in content
    assert _DATE_LABEL in content
    # Both orphan rows should appear in the report
    assert "dry-run-would-mint" in content
    assert "dry-run-would-defer" in content


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_mints_entity_for_named_orphan(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """--apply mints an entity and backfills entity_id for orphan with a name signal."""
    mod = _load_script()

    # Count entities before
    entities_before: int = await seeded_pool_with_orphans.fetchval(
        "SELECT COUNT(*) FROM public.entities"
    )

    rc = await mod.run_resolver(
        date_label=_DATE_LABEL,
        report_path=tmp_path / "orphans.md",
        apply=True,
        _pool=seeded_pool_with_orphans,
        # Patch notify to avoid real Telegram call
    )

    assert rc == 0

    # One new entity should have been minted (for "Bob Orphan")
    entities_after: int = await seeded_pool_with_orphans.fetchval(
        "SELECT COUNT(*) FROM public.entities"
    )
    assert entities_after == entities_before + 1

    # The named orphan should now have entity_id set in public.contacts
    bob_entity_id = await seeded_pool_with_orphans.fetchval(
        "SELECT entity_id FROM public.contacts WHERE first_name = 'Bob'"
    )
    assert bob_entity_id is not None, "entity_id should be backfilled for Bob Orphan"

    # The entity's canonical_name should match
    canonical = await seeded_pool_with_orphans.fetchval(
        "SELECT canonical_name FROM public.entities WHERE id = $1",
        bob_entity_id,
    )
    assert canonical == "Bob Orphan"


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_defers_nameless_orphan(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """--apply does not mint entity for nameless orphan; marks as deferred."""
    mod = _load_script()

    with patch.object(
        mod,
        "_send_telegram_notification",
        new_callable=AsyncMock,
        return_value=False,  # Simulate no Telegram creds
    ):
        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            _pool=seeded_pool_with_orphans,
        )

    assert rc == 0

    # The nameless orphan should still have entity_id IS NULL in contacts
    nameless_entity_id = await seeded_pool_with_orphans.fetchval(
        "SELECT entity_id FROM public.contacts WHERE name = 'unknown'"
    )
    assert nameless_entity_id is None, "nameless orphan must not have entity_id set"


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_report_contains_minted_and_deferred(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """Report includes per-row outcome table with minted and deferred entries."""
    mod = _load_script()

    report_path = tmp_path / "orphans-apply.md"
    with patch.object(
        mod,
        "_send_telegram_notification",
        new_callable=AsyncMock,
        return_value=False,
    ):
        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=report_path,
            apply=True,
            _pool=seeded_pool_with_orphans,
        )

    assert rc == 0
    content = report_path.read_text()

    assert "APPLY" in content
    assert "`minted`" in content
    assert "`deferred`" in content
    # Summary numbers
    assert "| Entities minted (backfilled) | 1 |" in content
    assert "| Deferred (owner notified) | 1 |" in content


@pytest.mark.asyncio(loop_scope="session")
async def test_already_linked_contacts_excluded(provisioned_postgres_pool, tmp_path: Path) -> None:
    """Contacts with entity_id already set in snapshot are not processed."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        # All contacts have entity_id set
        eid = await _insert_entity(pool, "Eve Linked")
        await _insert_contact(pool, name="Eve Linked", first_name="Eve", entity_id=eid)

        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            _pool=pool,
        )

    assert rc == 0

    # Report should say 0 orphans
    content = (tmp_path / "orphans.md").read_text()
    assert "| Total orphan contacts examined | 0 |" in content
    assert "_No orphan contacts found in the snapshot._" in content


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_snapshot_table_returns_error(
    resolver_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    """run_resolver returns 1 when the snapshot table does not exist."""
    mod = _load_script()

    rc = await mod.run_resolver(
        date_label="20260902",  # no snapshot for this date
        report_path=tmp_path / "orphans.md",
        apply=False,
        _pool=resolver_pool,
    )

    assert rc == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_date_label_returns_error(
    resolver_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    """run_resolver returns 1 for a non-YYYYMMDD date label."""
    mod = _load_script()

    rc = await mod.run_resolver(
        date_label="not-a-date",
        report_path=tmp_path / "orphans.md",
        apply=False,
        _pool=resolver_pool,
    )

    assert rc == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_notify_called_for_nameless_orphan(provisioned_postgres_pool, tmp_path: Path) -> None:
    """_send_telegram_notification is called for orphans with no name signal."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        # Insert one nameless orphan
        await _insert_contact(pool, name="unknown")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        with patch.object(
            mod,
            "_send_telegram_notification",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_notify:
            rc = await mod.run_resolver(
                date_label=_DATE_LABEL,
                report_path=tmp_path / "orphans.md",
                apply=True,
                _pool=pool,
            )

    assert rc == 0
    mock_notify.assert_called_once()
    # Message should contain the contact ID
    call_args = mock_notify.call_args
    message = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("message", "")
    assert "unknown" in message.lower() or "orphan" in message.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_canonical_name_signal_uses_first_last(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """canonical_name_signal prefers first_name + last_name over bare name."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(
            pool,
            name="Zara Z",
            first_name="Zara",
            last_name="Zimmerman",
        )
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            _pool=pool,
        )

        assert rc == 0

        canonical = await pool.fetchval(
            "SELECT canonical_name FROM public.entities WHERE canonical_name LIKE 'Zara%'"
        )
        assert canonical == "Zara Zimmerman"


@pytest.mark.asyncio(loop_scope="session")
async def test_report_path_parent_created(
    seeded_pool_with_orphans: asyncpg.Pool, tmp_path: Path
) -> None:
    """run_resolver creates parent directories for the report path if needed."""
    mod = _load_script()
    report_path = tmp_path / "deep" / "nested" / "orphans.md"

    rc = await mod.run_resolver(
        date_label=_DATE_LABEL,
        report_path=report_path,
        apply=False,
        _pool=seeded_pool_with_orphans,
    )

    assert rc == 0
    assert report_path.exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_is_idempotent_no_double_mint(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """Re-running --apply on an already-minted contact does not double-mint."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(
            pool,
            name="Carol Re-run",
            first_name="Carol",
            last_name="Re-run",
        )
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        # First apply run
        rc1 = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans-1.md",
            apply=True,
            _pool=pool,
        )
        assert rc1 == 0

        entities_after_first: int = await pool.fetchval("SELECT COUNT(*) FROM public.entities")
        assert entities_after_first == 1

        # Second apply run (re-run on same snapshot — idempotent guard)
        rc2 = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans-2.md",
            apply=True,
            _pool=pool,
        )
        assert rc2 == 0

        # No additional entity should have been minted
        entities_after_second: int = await pool.fetchval("SELECT COUNT(*) FROM public.entities")
        assert entities_after_second == 1, "Re-run must not double-mint entities"

        # contact must still have entity_id set (not overwritten)
        carol_entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE first_name = 'Carol'"
        )
        assert carol_entity_id is not None


# ---------------------------------------------------------------------------
# --force-nameless tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_false_still_defers_nameless_orphan(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """Default behavior (force_nameless=False): nameless orphan goes to notify-path."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(pool, name="unknown", company="Acme Corp")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        with patch.object(
            mod,
            "_send_telegram_notification",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_notify:
            rc = await mod.run_resolver(
                date_label=_DATE_LABEL,
                report_path=tmp_path / "orphans.md",
                apply=True,
                force_nameless=False,
                _pool=pool,
            )

        assert rc == 0
        mock_notify.assert_called_once(), "Notify should be called for nameless orphan by default"

        # entity_id must remain NULL — check while pool is still open
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE name = 'unknown'"
        )
        assert entity_id is None, "Default (no --force-nameless): entity_id must stay NULL"


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_true_mints_with_company(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """force_nameless=True + company set → minted with company-derived canonical_name."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(pool, name="unknown", company="Acme Corp")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            force_nameless=True,
            _pool=pool,
        )

        assert rc == 0

        # An entity should have been minted
        entity_count = await pool.fetchval("SELECT COUNT(*) FROM public.entities")
        assert entity_count == 1, "force_nameless should mint an entity"

        canonical = await pool.fetchval("SELECT canonical_name FROM public.entities LIMIT 1")
        assert canonical is not None
        assert "Acme Corp" in canonical, f"Expected company name in canonical: {canonical!r}"
        assert "Contact at" in canonical, f"Expected 'Contact at' prefix: {canonical!r}"

        # contact.entity_id should be backfilled
        entity_id = await pool.fetchval(
            "SELECT entity_id FROM public.contacts WHERE name = 'unknown'"
        )
        assert entity_id is not None, "force_nameless should backfill entity_id"


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_true_mints_with_roles(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """force_nameless=True + no company but has roles → minted with roles-derived name."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(pool, name="unknown", roles=["admin", "billing"])
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            force_nameless=True,
            _pool=pool,
        )

        assert rc == 0

        canonical = await pool.fetchval("SELECT canonical_name FROM public.entities LIMIT 1")
        assert canonical is not None
        assert "admin" in canonical and "billing" in canonical, (
            f"Expected roles in canonical: {canonical!r}"
        )
        assert "contact" in canonical.lower(), f"Expected 'contact' suffix: {canonical!r}"


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_true_mints_with_id_fallback(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """force_nameless=True + no company + no roles → minted with id-fallback name."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        contact_id = await _insert_contact(pool, name="unknown")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            force_nameless=True,
            _pool=pool,
        )

        assert rc == 0

        canonical = await pool.fetchval("SELECT canonical_name FROM public.entities LIMIT 1")
        assert canonical is not None
        # Short UUID prefix should appear in the fallback name
        short_id = str(contact_id)[:8]
        assert short_id in canonical, (
            f"Expected short contact ID {short_id!r} in canonical: {canonical!r}"
        )
        assert "Unnamed" in canonical or "contact" in canonical.lower(), (
            f"Expected fallback indicator in canonical: {canonical!r}"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_dry_run_produces_would_mint(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """force_nameless=True + dry-run → dry-run-would-mint with synthetic name in note."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        await _insert_contact(pool, name="unknown", company="Beta Inc")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        report_path = tmp_path / "orphans.md"
        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=report_path,
            apply=False,
            force_nameless=True,
            _pool=pool,
        )

        assert rc == 0

        # No writes should have happened
        entity_count = await pool.fetchval("SELECT COUNT(*) FROM public.entities")
        assert entity_count == 0, "Dry-run must not write entities even with force_nameless"

    # Report is on disk — read after pool closes, that's fine
    content = report_path.read_text()
    assert "dry-run-would-mint" in content, "Should show would-mint outcome"
    assert "synthetic canonical_name (--force-nameless)" in content, (
        "Note should identify it as synthetic"
    )
    assert "Beta Inc" in content, "Company name should appear in report"


@pytest.mark.asyncio(loop_scope="session")
async def test_force_nameless_does_not_affect_named_orphans(
    provisioned_postgres_pool, tmp_path: Path
) -> None:
    """force_nameless=True does not change behaviour for orphans that have a real name signal."""
    mod = _load_script()

    async with provisioned_postgres_pool() as pool:
        await _setup_schema(pool)

        # Orphan with a real name signal
        await _insert_contact(pool, name="Diana Real", first_name="Diana", last_name="Real")
        await _create_snapshot_from_contacts(pool, _DATE_LABEL)

        rc = await mod.run_resolver(
            date_label=_DATE_LABEL,
            report_path=tmp_path / "orphans.md",
            apply=True,
            force_nameless=True,
            _pool=pool,
        )

        assert rc == 0

        canonical = await pool.fetchval("SELECT canonical_name FROM public.entities LIMIT 1")
        # Should use the real name, not a synthetic one
        assert canonical == "Diana Real", (
            f"Named orphan should keep real canonical_name, got: {canonical!r}"
        )

    content = (tmp_path / "orphans.md").read_text()
    assert "synthetic" not in content, "Named orphan note must not mention synthetic"
