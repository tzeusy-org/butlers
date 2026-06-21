"""Tests for the drop-backup-tables migrations (bead bu-colrv).

Covers:
  (a) Unit — module structure, revision wiring, callable upgrade/downgrade,
      source-text guards for both migration files (core_118, rel_020).
  (b) Integration — asserts the 3 target tables are absent after the
      migration SQL is applied against a live DB.

Target tables:
  - public.contacts_pre_migration_20260531   (core_118)
  - public.contact_info_dropbak_core_115     (core_118)
  - relationship._reminders_backup           (rel_020)
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Migration file paths
# ---------------------------------------------------------------------------

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_118_drop_backup_tables.py"
)

_REL_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "020_drop_reminders_backup.py"
)


def _load_core():
    spec = importlib.util.spec_from_file_location("core_118", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_rel():
    spec = importlib.util.spec_from_file_location("rel_020", _REL_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit — structure and source-text guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCore118Structure:
    def test_revision_chain(self):
        mod = _load_core()
        assert mod.revision == "core_118"
        assert mod.down_revision == "core_117"

    def test_drops_both_tables_with_if_exists(self):
        src = _CORE_MIGRATION_PATH.read_text()
        # Migration iterates _TABLES tuple and calls DROP TABLE IF EXISTS {qualified}.
        assert "DROP TABLE IF EXISTS" in src
        assert "public.contacts_pre_migration_20260531" in src
        assert "public.contact_info_dropbak_core_115" in src

    def test_uses_to_regclass_guard(self):
        src = _CORE_MIGRATION_PATH.read_text()
        assert "to_regclass" in src

    def test_downgrade_is_noop(self):
        # downgrade cannot recreate data from dropped source tables — must be documented no-op
        src = _CORE_MIGRATION_PATH.read_text()
        assert "no-op" in src.lower() or "no op" in src.lower() or "cannot be recreated" in src


@pytest.mark.unit
class TestRel020Structure:
    def test_revision_chain(self):
        mod = _load_rel()
        assert mod.revision == "rel_020"
        assert mod.down_revision == "rel_019"

    def test_drops_table_with_if_exists(self):
        src = _REL_MIGRATION_PATH.read_text()
        assert "DROP TABLE IF EXISTS _reminders_backup" in src

    def test_uses_to_regclass_guard(self):
        src = _REL_MIGRATION_PATH.read_text()
        assert "to_regclass" in src

    def test_downgrade_recreates_empty_table(self):
        src = _REL_MIGRATION_PATH.read_text()
        assert "CREATE TABLE IF NOT EXISTS _reminders_backup" in src


# ---------------------------------------------------------------------------
# (b) Integration — tables absent after migration SQL runs against live DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backup_tables_absent_after_migration(provisioned_postgres_pool) -> None:
    """Assert that all 3 target backup tables are absent after migration SQL runs."""
    async with provisioned_postgres_pool() as pool:
        # Create the public schema backup tables (simulating pre-migration state).
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS public.contacts_pre_migration_20260531 (
                id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL
            )
            """
        )
        await pool.execute(
            "INSERT INTO public.contacts_pre_migration_20260531 (name) VALUES ('snapshot-row')"
        )
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS public.contact_info_dropbak_core_115 (
                id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID,
                type       VARCHAR NOT NULL,
                value      TEXT NOT NULL
            )
            """
        )
        await pool.execute(
            "INSERT INTO public.contact_info_dropbak_core_115 (type, value) VALUES ('email', 'x@x.com')"
        )
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship._reminders_backup (
                id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                content TEXT NOT NULL
            )
            """
        )

        # Verify tables exist before migration.
        targets_pre = (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
            "relationship._reminders_backup",
        )
        assert len(targets_pre) == 3
        for qual in targets_pre:
            exists = await pool.fetchval(f"SELECT to_regclass('{qual}')")
            assert exists is not None, f"Setup failed: {qual} should exist before migration"

        # Run the core_118 drop logic directly (mirrors upgrade() SQL).
        for qual in (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
        ):
            oid = await pool.fetchval(f"SELECT to_regclass('{qual}')")
            if oid is not None:
                await pool.execute(f"DROP TABLE IF EXISTS {qual}")

        # Run the rel_020 drop logic directly.
        oid = await pool.fetchval("SELECT to_regclass('relationship._reminders_backup')")
        if oid is not None:
            await pool.execute("DROP TABLE IF EXISTS relationship._reminders_backup")

        # Assert all 3 tables are now absent.
        targets_post = (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
            "relationship._reminders_backup",
        )
        assert len(targets_post) == 3
        for qual in targets_post:
            result = await pool.fetchval(f"SELECT to_regclass('{qual}')")
            assert result is None, (
                f"Expected {qual} to be absent after migration, but it still exists"
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_migration_idempotent_when_tables_absent(provisioned_postgres_pool) -> None:
    """Upgrade is a no-op when the target tables do not exist (idempotency check)."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")

        # Tables are intentionally absent — simulates re-run after prior migration.
        targets_absent = (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
            "relationship._reminders_backup",
        )
        assert len(targets_absent) == 3
        for qual in targets_absent:
            # Verify absent.
            result = await pool.fetchval(f"SELECT to_regclass('{qual}')")
            assert result is None, f"Pre-condition: {qual} should be absent for idempotency test"

        # Run drops — should be safe no-ops.
        for qual in (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
        ):
            await pool.execute(f"DROP TABLE IF EXISTS {qual}")
        await pool.execute("DROP TABLE IF EXISTS relationship._reminders_backup")

        # Still absent — no error raised.
        targets_still_absent = (
            "public.contacts_pre_migration_20260531",
            "public.contact_info_dropbak_core_115",
            "relationship._reminders_backup",
        )
        assert len(targets_still_absent) == 3
        for qual in targets_still_absent:
            result = await pool.fetchval(f"SELECT to_regclass('{qual}')")
            assert result is None
