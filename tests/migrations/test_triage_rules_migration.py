"""Tests for the switchboard triage_rules migration (sw_017)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
MIGRATION_DIR = ROSTER_DIR / "switchboard" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "017_create_triage_rules.py"


def _load_migration(
    filename: str = "017_create_triage_rules.py",
    module_name: str = "sw_017_create_triage_rules",
):
    """Load the sw_017 migration module dynamically."""
    filepath = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigrationFileLayout:
    def test_migration_file_exists(self) -> None:
        """The sw_017 migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_init_file_exists(self) -> None:
        """The __init__.py file exists in the switchboard migrations directory."""
        init_file = MIGRATION_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        """Migration revision is sw_017."""
        mod = _load_migration()
        assert mod.revision == "sw_017"

    def test_down_revision(self) -> None:
        """Migration chains from sw_016."""
        mod = _load_migration()
        assert mod.down_revision == "sw_016"

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


class TestUpgradeTableSchema:
    def test_creates_triage_rules_table(self) -> None:
        """Upgrade SQL creates the triage_rules table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE triage_rules" in source

    def test_primary_key_is_uuid(self) -> None:
        """triage_rules has a UUID primary key with gen_random_uuid() default."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in source

    def test_required_columns_present(self) -> None:
        """Upgrade SQL contains all spec §4.1 required columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        for column in (
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
            assert column in source, f"Missing column: {column}"

    def test_condition_is_jsonb(self) -> None:
        """condition column is JSONB type."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "condition JSONB NOT NULL" in source

    def test_soft_delete_column_is_nullable(self) -> None:
        """deleted_at column allows NULL (active row indicator)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "deleted_at TIMESTAMPTZ NULL" in source

    def test_enabled_defaults_true(self) -> None:
        """enabled column defaults to TRUE."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "enabled BOOLEAN NOT NULL DEFAULT TRUE" in source

    def test_timestamps_default_to_now(self) -> None:
        """created_at and updated_at default to NOW()."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in source
        assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in source


class TestUpgradeConstraints:
    def test_rule_type_check_constraint(self) -> None:
        """rule_type CHECK constraint covers all spec §4.1 values."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_rule_type_check" in source
        for rule_type in ("sender_domain", "sender_address", "header_condition", "mime_type"):
            assert rule_type in source

    def test_action_check_constraint(self) -> None:
        """action CHECK constraint covers spec §4.1 values and route_to: pattern."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_action_check" in source
        for action in ("skip", "metadata_only", "low_priority_queue", "pass_through"):
            assert action in source
        assert "route_to:%" in source or "LIKE 'route_to:" in source

    def test_created_by_check_constraint(self) -> None:
        """created_by CHECK constraint covers: dashboard, api, seed."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_created_by_check" in source
        for creator in ("dashboard", "api", "seed"):
            assert creator in source

    def test_priority_check_constraint(self) -> None:
        """priority CHECK constraint enforces priority >= 0."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_priority_check" in source
        assert "priority >= 0" in source


class TestUpgradeIndexes:
    def test_active_priority_index(self) -> None:
        """Upgrade creates the active+priority composite index on active rows."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_active_priority_idx" in source
        # Must be a partial index excluding deleted rows
        assert "WHERE deleted_at IS NULL" in source

    def test_active_priority_index_covers_required_columns(self) -> None:
        """The active priority index covers: enabled, priority, created_at, id."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        idx_start = source.find("triage_rules_active_priority_idx")
        idx_end = source.find("triage_rules_rule_type_idx", idx_start)
        idx_block = source[idx_start:idx_end]
        for col in ("enabled", "priority", "created_at", "id"):
            assert col in idx_block, f"Active priority index missing column: {col}"

    def test_rule_type_index(self) -> None:
        """Upgrade creates the rule_type partial index on active rows."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_rule_type_idx" in source

    def test_condition_gin_index(self) -> None:
        """Upgrade creates the GIN index on the condition JSONB column."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "triage_rules_condition_gin_idx" in source
        assert "USING GIN" in source
        # GIN index covers the condition column
        gin_start = source.find("triage_rules_condition_gin_idx")
        gin_end = source.find("INSERT INTO triage_rules", gin_start)
        gin_block = source[gin_start:gin_end]
        assert "condition" in gin_block


class TestSeedRules:
    def test_seed_insert_present(self) -> None:
        """Upgrade SQL contains the seed INSERT statement."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "INSERT INTO triage_rules" in source

    def test_seed_uses_on_conflict_do_nothing(self) -> None:
        """Seed INSERT is idempotent via ON CONFLICT DO NOTHING."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "ON CONFLICT (id) DO NOTHING" in source

    def test_seed_rows_marked_created_by_seed(self) -> None:
        """All seed rows use created_by='seed'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        insert_start = source.find("INSERT INTO triage_rules")
        insert_end = source.find("ON CONFLICT", insert_start)
        insert_block = source[insert_start:insert_end]
        # Every seed row block must contain 'seed' as the created_by value
        assert "'seed'" in insert_block

    def test_seed_contains_nine_rules(self) -> None:
        """Upgrade seeds exactly 9 baseline rules from spec §7."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Count occurrences of 'seed' in the VALUES block (one per row)
        insert_start = source.find("INSERT INTO triage_rules")
        insert_end = source.find("ON CONFLICT", insert_start)
        insert_block = source[insert_start:insert_end]
        seed_count = insert_block.count("'seed'")
        assert seed_count == 9, f"Expected 9 seed rows, found {seed_count}"

    def test_seed_chase_finance_rule(self) -> None:
        """Seed includes chase.com → route_to:finance rule (priority 10)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "chase.com" in source
        assert "route_to:finance" in source

    def test_seed_amex_finance_rule(self) -> None:
        """Seed includes americanexpress.com → route_to:finance rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "americanexpress.com" in source

    def test_seed_delta_travel_rule(self) -> None:
        """Seed includes delta.com → route_to:travel rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "delta.com" in source
        assert "route_to:travel" in source

    def test_seed_united_travel_rule(self) -> None:
        """Seed includes united.com → route_to:travel rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "united.com" in source

    def test_seed_paypal_finance_rule(self) -> None:
        """Seed includes paypal.com → route_to:finance rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "paypal.com" in source

    def test_seed_list_unsubscribe_metadata_only_rule(self) -> None:
        """Seed includes List-Unsubscribe header → metadata_only rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "List-Unsubscribe" in source
        assert "metadata_only" in source

    def test_seed_precedence_bulk_low_priority_rule(self) -> None:
        """Seed includes Precedence:bulk header → low_priority_queue rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "Precedence" in source
        assert "low_priority_queue" in source

    def test_seed_auto_submitted_skip_rule(self) -> None:
        """Seed includes Auto-Submitted header → skip rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "Auto-Submitted" in source
        assert "'skip'" in source

    def test_seed_text_calendar_relationship_rule(self) -> None:
        """Seed includes text/calendar MIME type → route_to:relationship rule."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "text/calendar" in source
        assert "route_to:relationship" in source

    def test_seed_uses_fixed_uuids(self) -> None:
        """Seed rows use fixed UUIDs to enable idempotent re-import."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Fixed UUIDs allow ON CONFLICT (id) DO NOTHING to deduplicate
        assert "00000000-0000-0000-0001-" in source

    def test_seed_sender_domain_rules_use_suffix_match(self) -> None:
        """Domain-based seed rules use suffix match for subdomain coverage."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert '"match": "suffix"' in source or "'match': 'suffix'" in source or "suffix" in source


class TestDowngrade:
    def test_drops_indexes(self) -> None:
        """Downgrade removes all three indexes."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS triage_rules_condition_gin_idx" in source
        assert "DROP INDEX IF EXISTS triage_rules_rule_type_idx" in source
        assert "DROP INDEX IF EXISTS triage_rules_active_priority_idx" in source

    def test_drops_triage_rules_table(self) -> None:
        """Downgrade drops the triage_rules table."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP TABLE IF EXISTS triage_rules" in source

    def test_indexes_dropped_before_table(self) -> None:
        """Indexes are dropped before the table in downgrade."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        index_drop_pos = source.find("DROP INDEX IF EXISTS triage_rules_condition_gin_idx")
        table_drop_pos = source.find("DROP TABLE IF EXISTS triage_rules")
        assert index_drop_pos < table_drop_pos, (
            "Indexes must be dropped before the table in downgrade"
        )
