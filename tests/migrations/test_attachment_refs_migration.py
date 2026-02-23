"""Tests for the switchboard attachment_refs and metadata pruning migration (sw_020)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "switchboard" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "020_add_attachment_refs_and_metadata_pruning.py"


def _load_migration(
    filename: str = "020_add_attachment_refs_and_metadata_pruning.py",
    module_name: str = "sw_020_attachment_refs_and_metadata_pruning",
):
    """Load the sw_020 migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The sw_020 migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the switchboard migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is sw_020."""
        mod = _load_migration()
        assert mod.revision == "sw_020"

    def test_down_revision(self) -> None:
        """Migration chains from sw_019."""
        mod = _load_migration()
        assert mod.down_revision == "sw_019"

    def test_branch_labels_is_none(self) -> None:
        """Migration does not open a new branch."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        """Migration has no cross-chain dependency."""
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        """Migration declares upgrade() and downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestAttachmentRefsTableSchema:
    def test_creates_attachment_refs_table(self) -> None:
        """Upgrade SQL creates the attachment_refs table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS attachment_refs" in source

    def test_primary_key_is_composite(self) -> None:
        """attachment_refs uses a composite PK of (message_id, attachment_id) per spec §5.2."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "PRIMARY KEY (message_id, attachment_id)" in source

    def test_required_columns_present(self) -> None:
        """Upgrade SQL contains all spec §5.2 required columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
            "message_id",
            "attachment_id",
            "filename",
            "media_type",
            "size_bytes",
            "fetched",
            "blob_ref",
            "created_at",
        ):
            assert column in source, f"Missing column: {column}"

    def test_message_id_is_text_not_null(self) -> None:
        """message_id column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "message_id TEXT NOT NULL" in source

    def test_attachment_id_is_text_not_null(self) -> None:
        """attachment_id column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "attachment_id TEXT NOT NULL" in source

    def test_filename_is_nullable(self) -> None:
        """filename column is nullable (per spec §5.2 and §7)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "filename TEXT NULL" in source

    def test_media_type_is_text_not_null(self) -> None:
        """media_type column is TEXT NOT NULL."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "media_type TEXT NOT NULL" in source

    def test_size_bytes_is_bigint_not_null(self) -> None:
        """size_bytes column is BIGINT NOT NULL for large file support."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "size_bytes BIGINT NOT NULL" in source

    def test_fetched_defaults_false(self) -> None:
        """fetched column defaults to FALSE (lazy-fetch model)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "fetched BOOLEAN NOT NULL DEFAULT FALSE" in source

    def test_blob_ref_is_nullable(self) -> None:
        """blob_ref column is nullable (NULL until attachment is materialized)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "blob_ref TEXT NULL" in source

    def test_created_at_defaults_to_now(self) -> None:
        """created_at column defaults to NOW()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in source


class TestAttachmentRefsIndexes:
    def test_lazy_fetch_index_created(self) -> None:
        """Upgrade creates the (fetched, created_at DESC) index for lazy-fetch queueing."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ix_attachment_refs_fetched_created_at" in source

    def test_lazy_fetch_index_covers_correct_columns(self) -> None:
        """The lazy-fetch index covers: fetched, created_at DESC."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        idx_start = source.find("ix_attachment_refs_fetched_created_at")
        idx_end = source.find("ix_attachment_refs_media_type_created_at", idx_start)
        idx_block = source[idx_start:idx_end]
        assert "fetched" in idx_block
        assert "created_at" in idx_block
        assert "DESC" in idx_block

    def test_media_type_index_created(self) -> None:
        """Upgrade creates the (media_type, created_at DESC) index for analytics."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ix_attachment_refs_media_type_created_at" in source

    def test_media_type_index_covers_correct_columns(self) -> None:
        """The media_type index covers: media_type, created_at DESC."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        idx_start = source.find("ix_attachment_refs_media_type_created_at")
        # Find next significant block after this index
        idx_end = source.find("switchboard_prune_email_metadata_refs", idx_start)
        idx_block = source[idx_start:idx_end]
        assert "media_type" in idx_block
        assert "created_at" in idx_block
        assert "DESC" in idx_block


class TestMetadataPruningFunction:
    def test_prune_function_created(self) -> None:
        """Upgrade creates the switchboard_prune_email_metadata_refs function."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "switchboard_prune_email_metadata_refs" in source
        assert "CREATE OR REPLACE FUNCTION" in source

    def test_prune_function_signature(self) -> None:
        """Prune function accepts (INTERVAL, TIMESTAMPTZ) parameters."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "retention INTERVAL" in source
        assert "reference_ts TIMESTAMPTZ" in source

    def test_prune_function_default_retention(self) -> None:
        """Prune function defaults to 90-day retention per policy §10."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "INTERVAL '90 days'" in source

    def test_prune_function_default_reference_ts(self) -> None:
        """Prune function defaults reference_ts to now()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "reference_ts TIMESTAMPTZ DEFAULT now()" in source

    def test_prune_function_targets_email_metadata_refs(self) -> None:
        """Prune function deletes from email_metadata_refs table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "DELETE FROM email_metadata_refs" in source

    def test_prune_function_uses_created_at_cutoff(self) -> None:
        """Prune function uses created_at for age-based pruning."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "created_at" in source
        assert "reference_ts - retention" in source

    def test_prune_function_returns_integer(self) -> None:
        """Prune function returns INTEGER (deleted row count)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "RETURNS INTEGER" in source

    def test_prune_function_is_plpgsql(self) -> None:
        """Prune function uses PL/pgSQL language."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "LANGUAGE plpgsql" in source


class TestDowngrade:
    def test_drops_prune_function(self) -> None:
        """Downgrade removes the switchboard_prune_email_metadata_refs function."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP FUNCTION IF EXISTS switchboard_prune_email_metadata_refs" in source
        assert "INTERVAL, TIMESTAMPTZ" in source

    def test_drops_media_type_index(self) -> None:
        """Downgrade removes the media_type/created_at index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS ix_attachment_refs_media_type_created_at" in source

    def test_drops_fetched_index(self) -> None:
        """Downgrade removes the fetched/created_at index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS ix_attachment_refs_fetched_created_at" in source

    def test_drops_attachment_refs_table(self) -> None:
        """Downgrade drops the attachment_refs table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS attachment_refs" in source

    def test_function_dropped_before_table(self) -> None:
        """Prune function is dropped before attachment_refs table in downgrade."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        func_drop_pos = source.find("DROP FUNCTION IF EXISTS switchboard_prune_email_metadata_refs")
        table_drop_pos = source.find("DROP TABLE IF EXISTS attachment_refs")
        assert func_drop_pos < table_drop_pos, (
            "Prune function must be dropped before attachment_refs table in downgrade"
        )

    def test_indexes_dropped_before_table(self) -> None:
        """Indexes are dropped before attachment_refs table in downgrade."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        idx_name = "DROP INDEX IF EXISTS ix_attachment_refs_media_type_created_at"
        media_type_idx_pos = source.find(idx_name)
        fetched_idx_pos = source.find("DROP INDEX IF EXISTS ix_attachment_refs_fetched_created_at")
        table_drop_pos = source.find("DROP TABLE IF EXISTS attachment_refs")
        assert media_type_idx_pos < table_drop_pos, (
            "media_type index must be dropped before attachment_refs table"
        )
        assert fetched_idx_pos < table_drop_pos, (
            "fetched index must be dropped before attachment_refs table"
        )

    def test_downgrade_does_not_touch_email_metadata_refs(self) -> None:
        """Downgrade does not drop email_metadata_refs (created in sw_019)."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS email_metadata_refs" not in source
        assert "DROP TABLE email_metadata_refs" not in source
