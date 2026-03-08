"""Tests for the switchboard ingestion_rules migration (sw_027).

Validates the migration file structure, table DDL, constraints, indexes,
data migration logic (triage_rules and source_filters), and downgrade.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "switchboard" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "027_create_ingestion_rules.py"


def _load_migration(
    filename: str = "027_create_ingestion_rules.py",
    module_name: str = "sw_027_create_ingestion_rules",
):
    """Load the sw_027 migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The sw_027 migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the switchboard migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is sw_027."""
        mod = _load_migration()
        assert mod.revision == "sw_027"

    def test_down_revision(self) -> None:
        """Migration chains from sw_026."""
        mod = _load_migration()
        assert mod.down_revision == "sw_026"

    def test_branch_labels_is_none(self) -> None:
        """Migration does not open a new branch (branch_labels=None)."""
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


# ---------------------------------------------------------------------------
# Table DDL
# ---------------------------------------------------------------------------


class TestIngestionRulesTableDDL:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.upgrade)

    def test_creates_ingestion_rules_table(self) -> None:
        """Upgrade SQL creates the ingestion_rules table."""
        assert "CREATE TABLE ingestion_rules" in self._src()

    def test_primary_key_is_uuid(self) -> None:
        """ingestion_rules has a UUID primary key with gen_random_uuid() default."""
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in self._src()

    def test_scope_column_text_not_null(self) -> None:
        """scope column is TEXT NOT NULL."""
        assert "scope TEXT NOT NULL" in self._src()

    def test_rule_type_column_text_not_null(self) -> None:
        """rule_type column is TEXT NOT NULL (open, not constrained by CHECK)."""
        assert "rule_type TEXT NOT NULL" in self._src()

    def test_condition_is_jsonb_not_null(self) -> None:
        """condition column is JSONB NOT NULL."""
        assert "condition JSONB NOT NULL" in self._src()

    def test_action_column_text_not_null(self) -> None:
        """action column is TEXT NOT NULL."""
        assert "action TEXT NOT NULL" in self._src()

    def test_priority_column_integer_not_null(self) -> None:
        """priority column is INTEGER NOT NULL."""
        assert "priority INTEGER NOT NULL" in self._src()

    def test_enabled_defaults_true(self) -> None:
        """enabled column defaults to TRUE."""
        assert "enabled BOOLEAN NOT NULL DEFAULT TRUE" in self._src()

    def test_name_column_is_nullable(self) -> None:
        """name column is optional (no NOT NULL)."""
        src = self._src()
        # Find the name column definition line
        name_idx = src.find("name TEXT")
        assert name_idx != -1
        # Confirm it does not have NOT NULL on the same definition
        line_end = src.find("\n", name_idx)
        line = src[name_idx:line_end]
        assert "NOT NULL" not in line

    def test_description_column_is_nullable(self) -> None:
        """description column is optional (no NOT NULL)."""
        src = self._src()
        desc_idx = src.find("description TEXT")
        assert desc_idx != -1
        line_end = src.find("\n", desc_idx)
        line = src[desc_idx:line_end]
        assert "NOT NULL" not in line

    def test_created_by_defaults_to_migration(self) -> None:
        """created_by defaults to 'migration'."""
        assert "created_by TEXT NOT NULL DEFAULT 'migration'" in self._src()

    def test_timestamps_default_to_now(self) -> None:
        """created_at and updated_at default to NOW()."""
        src = self._src()
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in src
        assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in src

    def test_soft_delete_column(self) -> None:
        """deleted_at column exists for soft-delete support."""
        assert "deleted_at TIMESTAMPTZ" in self._src()

    def test_required_columns_present(self) -> None:
        """All design.md D9 required columns are present."""
        src = self._src()
        for col in (
            "scope",
            "rule_type",
            "condition",
            "action",
            "priority",
            "enabled",
            "name",
            "description",
            "created_by",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            assert col in src, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.upgrade)

    def test_scope_check_constraint_name(self) -> None:
        """Scope CHECK constraint is named ingestion_rules_scope_check."""
        assert "ingestion_rules_scope_check" in self._src()

    def test_scope_check_allows_global(self) -> None:
        """Scope CHECK allows scope = 'global'."""
        assert "scope = 'global'" in self._src()

    def test_scope_check_allows_connector_prefix(self) -> None:
        """Scope CHECK allows scope LIKE 'connector:%'."""
        assert "scope LIKE 'connector:%'" in self._src()

    def test_connector_action_check_constraint_name(self) -> None:
        """Connector action CHECK constraint is named."""
        assert "ingestion_rules_connector_action_check" in self._src()

    def test_connector_scoped_rules_must_be_block(self) -> None:
        """Connector-scoped rules constrained to action = 'block'."""
        src = self._src()
        # The CHECK: scope = 'global' OR action = 'block'
        # This means: if scope != 'global' (i.e. connector), action must be 'block'
        assert "action = 'block'" in src

    def test_priority_check_constraint(self) -> None:
        """Priority CHECK constraint enforces priority >= 0."""
        src = self._src()
        assert "ingestion_rules_priority_check" in src
        assert "priority >= 0" in src

    def test_no_rule_type_check_constraint(self) -> None:
        """rule_type has no CHECK constraint — open TEXT per D7."""
        src = self._src()
        assert "ingestion_rules_rule_type_check" not in src


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


class TestIndexes:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.upgrade)

    def test_scope_active_index_created(self) -> None:
        """Scope-partitioned active index is created."""
        assert "ix_ingestion_rules_scope_active" in self._src()

    def test_scope_active_index_columns(self) -> None:
        """Scope active index covers: scope, priority, created_at, id."""
        src = self._src()
        idx_start = src.find("ix_ingestion_rules_scope_active")
        idx_end = src.find("ix_ingestion_rules_global_active", idx_start)
        idx_block = src[idx_start:idx_end]
        for col in ("scope", "priority", "created_at", "id"):
            assert col in idx_block, f"Scope active index missing column: {col}"

    def test_scope_active_index_is_partial(self) -> None:
        """Scope active index is partial: enabled=TRUE AND deleted_at IS NULL."""
        src = self._src()
        idx_start = src.find("ix_ingestion_rules_scope_active")
        idx_end = src.find("ix_ingestion_rules_global_active", idx_start)
        idx_block = src[idx_start:idx_end]
        assert "enabled = TRUE" in idx_block
        assert "deleted_at IS NULL" in idx_block

    def test_global_active_index_created(self) -> None:
        """Global-scope active index is created."""
        assert "ix_ingestion_rules_global_active" in self._src()

    def test_global_active_index_columns(self) -> None:
        """Global active index covers: priority, created_at, id."""
        src = self._src()
        idx_start = src.find("ix_ingestion_rules_global_active")
        # Find end of this index block — look for next SQL statement
        idx_end = src.find("INSERT INTO ingestion_rules", idx_start)
        if idx_end == -1:
            idx_end = len(src)
        idx_block = src[idx_start:idx_end]
        for col in ("priority", "created_at", "id"):
            assert col in idx_block, f"Global active index missing column: {col}"

    def test_global_active_index_is_partial(self) -> None:
        """Global active index filters to scope='global' + enabled + not deleted."""
        src = self._src()
        idx_start = src.find("ix_ingestion_rules_global_active")
        idx_end = src.find("INSERT INTO ingestion_rules", idx_start)
        if idx_end == -1:
            idx_end = len(src)
        idx_block = src[idx_start:idx_end]
        assert "scope = 'global'" in idx_block
        assert "enabled = TRUE" in idx_block
        assert "deleted_at IS NULL" in idx_block


# ---------------------------------------------------------------------------
# Data migration — triage_rules
# ---------------------------------------------------------------------------


class TestTriageRulesMigration:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.upgrade)

    def test_inserts_from_triage_rules(self) -> None:
        """Upgrade migrates rows from triage_rules table."""
        src = self._src()
        assert "FROM triage_rules" in src

    def test_sets_scope_global(self) -> None:
        """Migrated triage_rules rows get scope='global'."""
        src = self._src()
        # The SELECT should include 'global' as the scope value
        assert "'global'" in src

    def test_preserves_id(self) -> None:
        """Migration preserves the original triage_rules UUID id."""
        src = self._src()
        # The INSERT...SELECT should include id in the column list
        insert_block = src[src.find("INSERT INTO ingestion_rules") :]
        select_block = insert_block[: insert_block.find("FROM triage_rules")]
        # Check that id is selected (not generated)
        assert "id," in select_block or "id " in select_block

    def test_preserves_all_columns(self) -> None:
        """Migration carries over rule_type, condition, action, priority, enabled,
        created_by, created_at, updated_at, deleted_at."""
        src = self._src()
        insert_start = src.find("INSERT INTO ingestion_rules")
        from_pos = src.find("FROM triage_rules", insert_start)
        block = src[insert_start:from_pos]
        for col in (
            "rule_type",
            "condition",
            "action",
            "priority",
            "enabled",
            "created_by",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            assert col in block, f"Triage migration missing column: {col}"


# ---------------------------------------------------------------------------
# Data migration — source_filters
# ---------------------------------------------------------------------------


class TestSourceFiltersMigration:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod)

    def test_queries_connector_source_filters(self) -> None:
        """Migration queries connector_source_filters joined to source_filters."""
        src = self._src()
        assert "connector_source_filters" in src
        assert "source_filters" in src

    def test_only_migrates_enabled_assignments(self) -> None:
        """Only enabled connector_source_filters assignments are migrated."""
        src = self._src()
        assert "enabled = true" in src

    def test_blacklist_generates_block_rules(self) -> None:
        """Blacklist filter patterns generate action='block' rules."""
        src = self._src()
        assert "blacklist" in src
        assert '"block"' in src or "'block'" in src

    def test_whitelist_generates_pass_through_rules(self) -> None:
        """Whitelist filter patterns generate action='pass_through' rules."""
        src = self._src()
        assert "whitelist" in src
        assert "pass_through" in src

    def test_whitelist_generates_catchall_block(self) -> None:
        """Whitelist migration generates a catch-all block rule."""
        src = self._src()
        assert "catch-all block" in src.lower() or "catch-all" in src.lower()

    def test_catchall_priority_offset_is_1000(self) -> None:
        """Catch-all block priority = whitelist priority + 1000 (per D4)."""
        mod = _load_migration()
        assert hasattr(mod, "_WHITELIST_CATCHALL_PRIORITY_OFFSET")
        assert mod._WHITELIST_CATCHALL_PRIORITY_OFFSET == 1000

    def test_scope_format_for_connector_rules(self) -> None:
        """Connector-scoped rules use 'connector:<type>:<identity>' scope format."""
        src = self._src()
        assert "connector:" in src
        # Check the scope construction pattern
        assert "connector_type" in src
        assert "endpoint_identity" in src

    def test_domain_key_type_mapped_to_sender_domain(self) -> None:
        """source_key_type='domain' maps to rule_type='sender_domain'."""
        src = self._src()
        assert "sender_domain" in src
        # The mapping logic should check for 'domain' key_type
        assert '"domain"' in src or "'domain'" in src

    def test_created_by_is_migration(self) -> None:
        """Migrated source_filter rows use created_by='migration'."""
        src = self._src()
        assert "'migration'" in src or '"migration"' in src


# ---------------------------------------------------------------------------
# Condition building helpers
# ---------------------------------------------------------------------------


class TestConditionBuilders:
    def test_build_condition_sender_domain(self) -> None:
        """_build_condition for sender_domain produces domain+match dict."""
        mod = _load_migration()
        result = mod._build_condition("sender_domain", "example.com")
        assert result == {"domain": "example.com", "match": "suffix"}

    def test_build_condition_sender_address(self) -> None:
        """_build_condition for sender_address produces address dict."""
        mod = _load_migration()
        result = mod._build_condition("sender_address", "user@example.com")
        assert result == {"address": "user@example.com"}

    def test_build_condition_substring(self) -> None:
        """_build_condition for substring produces pattern dict."""
        mod = _load_migration()
        result = mod._build_condition("substring", "newsletter")
        assert result == {"pattern": "newsletter"}

    def test_build_condition_chat_id(self) -> None:
        """_build_condition for chat_id produces chat_id dict."""
        mod = _load_migration()
        result = mod._build_condition("chat_id", "12345")
        assert result == {"chat_id": "12345"}

    def test_build_condition_channel_id(self) -> None:
        """_build_condition for channel_id produces channel_id dict."""
        mod = _load_migration()
        result = mod._build_condition("channel_id", "67890")
        assert result == {"channel_id": "67890"}

    def test_build_condition_unknown_type_fallback(self) -> None:
        """_build_condition for unknown type produces generic pattern dict."""
        mod = _load_migration()
        result = mod._build_condition("phone_number", "+1234")
        assert result == {"pattern": "+1234"}

    def test_build_catchall_sender_domain(self) -> None:
        """_build_catchall_condition for sender_domain uses wildcard."""
        mod = _load_migration()
        result = mod._build_catchall_condition("sender_domain")
        assert result == {"domain": "*", "match": "any"}

    def test_build_catchall_sender_address(self) -> None:
        """_build_catchall_condition for sender_address uses wildcard."""
        mod = _load_migration()
        result = mod._build_catchall_condition("sender_address")
        assert result == {"address": "*"}

    def test_build_catchall_chat_id(self) -> None:
        """_build_catchall_condition for chat_id uses wildcard."""
        mod = _load_migration()
        result = mod._build_catchall_condition("chat_id")
        assert result == {"chat_id": "*"}

    def test_build_catchall_channel_id(self) -> None:
        """_build_catchall_condition for channel_id uses wildcard."""
        mod = _load_migration()
        result = mod._build_catchall_condition("channel_id")
        assert result == {"channel_id": "*"}

    def test_build_catchall_substring(self) -> None:
        """_build_catchall_condition for substring uses wildcard."""
        mod = _load_migration()
        result = mod._build_catchall_condition("substring")
        assert result == {"pattern": "*"}


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def _src(self) -> str:
        mod = _load_migration()
        return inspect.getsource(mod.downgrade)

    def test_drops_indexes(self) -> None:
        """Downgrade removes both indexes."""
        src = self._src()
        assert "DROP INDEX IF EXISTS ix_ingestion_rules_global_active" in src
        assert "DROP INDEX IF EXISTS ix_ingestion_rules_scope_active" in src

    def test_drops_ingestion_rules_table(self) -> None:
        """Downgrade drops the ingestion_rules table."""
        assert "DROP TABLE IF EXISTS ingestion_rules" in self._src()

    def test_indexes_dropped_before_table(self) -> None:
        """Indexes are dropped before the table in downgrade."""
        src = self._src()
        idx_pos = src.find("DROP INDEX IF EXISTS")
        table_pos = src.find("DROP TABLE IF EXISTS ingestion_rules")
        assert idx_pos < table_pos, "Indexes must be dropped before the table in downgrade"

    def test_does_not_drop_old_tables(self) -> None:
        """Downgrade does NOT drop triage_rules or source_filters (Phase 3 cleanup)."""
        src = self._src()
        assert "triage_rules" not in src
        assert "source_filters" not in src
