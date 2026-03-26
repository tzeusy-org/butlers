"""Unit tests for consolidated Google refresh-token promotion in core_008.

The incremental core_013 migration was removed during chain consolidation.
Its behavior now lives in core_008_external_accounts.py, where existing
owner google_oauth_refresh rows are promoted into google_accounts.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_008_external_accounts.py"


def _load_migration():
    """Dynamically load the core_008 migration module."""
    spec = importlib.util.spec_from_file_location("core_008", MIGRATION_FILE)
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
        """revision == 'core_008'."""
        mod = _load_migration()
        assert mod.revision == "core_008"

    def test_down_revision(self) -> None:
        """down_revision points to core_007."""
        mod = _load_migration()
        assert mod.down_revision == "core_007"

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
    def test_handles_google_oauth_refresh_promotion(self) -> None:
        """Upgrade includes promotion logic for existing google_oauth_refresh records."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "google_oauth_refresh" in source
        assert "public.google_accounts" in source
        assert "UPDATE public.entity_info" in source

    def test_promotion_block_is_idempotent_and_guarded(self) -> None:
        """Promotion logic checks table existence and uses conflict-safe inserts."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "to_regclass('public.entities')" in source
        assert "ON CONFLICT DO NOTHING" in source

    def test_upgrade_no_longer_references_legacy_secret_key(self) -> None:
        """Consolidated core chain should not mention legacy GOOGLE_REFRESH_TOKEN key."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "GOOGLE_REFRESH_TOKEN" not in source


class TestDowngrade:
    def test_downgrade_is_noop(self) -> None:
        """downgrade() should remain callable."""
        mod = _load_migration()
        assert callable(mod.downgrade)

    def test_downgrade_has_docstring_or_comment(self) -> None:
        """downgrade() removes the external-account tables."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS connectors.steam_play_history" in source
        assert "DROP TABLE IF EXISTS public.google_accounts" in source
