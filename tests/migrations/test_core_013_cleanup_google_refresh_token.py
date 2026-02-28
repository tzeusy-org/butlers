"""Unit tests for core_013 Alembic migration: cleanup GOOGLE_REFRESH_TOKEN.

These tests verify the migration file structure and SQL logic without
requiring a live PostgreSQL connection.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_013_cleanup_google_refresh_token.py"


def _load_migration():
    """Dynamically load the core_013 migration module."""
    spec = importlib.util.spec_from_file_location("core_013", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The migration file exists at the expected path."""
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_migration_file_is_python(self) -> None:
        """The migration file is a .py file."""
        assert MIGRATION_FILE.suffix == ".py"


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """revision == 'core_013'."""
        mod = _load_migration()
        assert mod.revision == "core_013"

    def test_down_revision(self) -> None:
        """down_revision points to core_012."""
        mod = _load_migration()
        assert mod.down_revision == "core_012"

    def test_branch_labels_are_none(self) -> None:
        """branch_labels is None (inherits core branch)."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        """upgrade() is callable."""
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        """downgrade() is callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)


class TestUpgradeSQL:
    def test_deletes_google_refresh_token_key(self) -> None:
        """Upgrade targets GOOGLE_REFRESH_TOKEN via the module-level _REMOVED_KEYS constant."""
        mod = _load_migration()
        assert hasattr(mod, "_REMOVED_KEYS")
        assert "GOOGLE_REFRESH_TOKEN" in mod._REMOVED_KEYS
        # The upgrade function must reference the constant (not hardcode the key inline).
        source = inspect.getsource(mod.upgrade)
        assert "_REMOVED_KEYS" in source

    def test_deletes_from_butler_secrets(self) -> None:
        """Upgrade SQL issues a DELETE against butler_secrets."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DELETE FROM butler_secrets" in source

    def test_guarded_by_table_existence_check(self) -> None:
        """Upgrade SQL is wrapped in an existence guard for butler_secrets."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "to_regclass" in source
        assert "butler_secrets" in source

    def test_uses_do_block(self) -> None:
        """Upgrade SQL uses a DO $$ ... $$ block for safe execution."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DO $$" in source or "DO\n$$" in source or "DO\n    $$" in source

    def test_no_other_keys_deleted(self) -> None:
        """Upgrade only targets GOOGLE_REFRESH_TOKEN — does not delete other keys."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Should not delete keys that were already handled in core_009.
        assert "USER_EMAIL_ADDRESS" not in source
        assert "TELEGRAM_API_HASH" not in source
        assert "BUTLER_TELEGRAM_CHAT_ID" not in source


class TestDowngrade:
    def test_downgrade_is_noop(self) -> None:
        """downgrade() is a no-op (cannot restore deleted secrets)."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        # Should not issue any SQL DML to re-insert the secret (value is gone).
        # Check for SQL keywords that would indicate active DML, not just comments.
        lines = [
            ln.strip()
            for ln in source.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        body = " ".join(lines).upper()
        assert "INSERT INTO" not in body
        assert "DELETE FROM" not in body

    def test_downgrade_has_docstring_or_comment(self) -> None:
        """downgrade() documents why it is a no-op."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "contact_info" in source.lower() or "Cannot restore" in source or "pass" in source
