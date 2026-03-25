"""Tests for the whatsapp_sessions migration (whatsapp_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "whatsapp" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "001_whatsapp_sessions.py"


def _load_migration():
    """Load the whatsapp_001 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("whatsapp_sessions_migration", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestRevisionMetadata:
    def test_revision_identifiers(self) -> None:
        """The migration has correct revision metadata."""
        mod = _load_migration()
        assert mod.revision == "whatsapp_001"
        assert mod.down_revision is None
        assert mod.branch_labels == ("whatsapp",)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """The migration declares upgrade()/downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_creates_whatsapp_sessions_table(self) -> None:
        """Upgrade creates the whatsapp_sessions table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS whatsapp_sessions" in source

    def test_table_has_required_columns(self) -> None:
        """whatsapp_sessions has all spec-required columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
            "id",
            "phone_number",
            "device_id",
            "session_data",
            "paired_at",
            "last_seen_at",
            "active",
        ):
            assert column in source, f"Missing column: {column}"

    def test_id_is_uuid_primary_key(self) -> None:
        """id column is UUID primary key with gen_random_uuid() default."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "UUID" in source
        assert "PRIMARY KEY" in source
        assert "gen_random_uuid()" in source

    def test_phone_number_has_unique_constraint(self) -> None:
        """phone_number column has UNIQUE constraint."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # phone_number TEXT NOT NULL UNIQUE must appear in the upgrade source.
        assert "phone_number" in source
        assert "UNIQUE" in source, "phone_number must have UNIQUE constraint"
        # Verify that phone_number and UNIQUE appear together (not in separate statements)
        phone_idx = source.find("phone_number")
        unique_idx = source.find("UNIQUE", phone_idx)
        assert unique_idx != -1 and unique_idx - phone_idx < 100, (
            "UNIQUE constraint should appear immediately after phone_number column definition"
        )

    def test_session_data_is_jsonb(self) -> None:
        """session_data column uses JSONB type."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "JSONB" in source

    def test_active_has_default_true(self) -> None:
        """active column defaults to true."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DEFAULT true" in source or "DEFAULT TRUE" in source.upper()

    def test_timestamps_are_timestamptz(self) -> None:
        """Time columns use TIMESTAMPTZ for timezone-aware storage."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "TIMESTAMPTZ" in source

    def test_no_redundant_phone_number_index(self) -> None:
        """UNIQUE constraint on phone_number already creates an index; no separate index needed."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The UNIQUE constraint on phone_number implicitly creates an index in PostgreSQL.
        # A separate non-unique index would be redundant and add write overhead.
        assert "idx_whatsapp_sessions_phone_number" not in source

    def test_creates_active_index(self) -> None:
        """Upgrade creates an index on active for efficient session queries."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_whatsapp_sessions_active" in source


class TestDowngradeSQL:
    def test_drops_whatsapp_sessions_table(self) -> None:
        """Downgrade removes the whatsapp_sessions table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS whatsapp_sessions" in source

    def test_drops_indexes(self) -> None:
        """Downgrade removes the custom indexes."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        # Only the active index is created explicitly; phone_number index is implicit
        # from the UNIQUE constraint and is dropped with the table.
        assert "idx_whatsapp_sessions_phone_number" not in source
        assert "idx_whatsapp_sessions_active" in source
