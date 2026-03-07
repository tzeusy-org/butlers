"""Tests for mem_009 facts GIN indexes migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE = MIGRATION_DIR / "009_facts_gin_indexes.py"


def _load_migration():
    """Load mem_009 migration module dynamically."""
    spec = importlib.util.spec_from_file_location("mem_009_facts_gin_indexes", MIGRATION_FILE)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMem009FileStructure:
    def test_migration_file_exists(self) -> None:
        """The mem_009 migration file exists on disk."""
        assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"

    def test_revision_identifiers(self) -> None:
        """mem_009 has correct revision metadata chaining from mem_008."""
        mod = _load_migration()
        assert mod.revision == "mem_009"
        assert mod.down_revision == "mem_008"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_exist(self) -> None:
        """Migration declares upgrade()/downgrade() callables."""
        mod = _load_migration()
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestMem009GinIndex:
    def test_gin_index_on_metadata_exists(self) -> None:
        """Upgrade creates a GIN index on facts.metadata."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_metadata_gin" in source

    def test_gin_index_uses_gin_operator_class(self) -> None:
        """GIN index is created with USING gin(metadata)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "USING gin(metadata)" in source

    def test_gin_index_targets_facts_table(self) -> None:
        """GIN index is created on the facts table."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # The index creation statement should reference facts
        assert "ON facts USING gin(metadata)" in source

    def test_gin_index_is_idempotent(self) -> None:
        """GIN index is created with IF NOT EXISTS."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Find the gin index creation and confirm IF NOT EXISTS precedes it
        gin_idx_pos = source.index("idx_facts_metadata_gin")
        create_pos = source.rindex("CREATE INDEX IF NOT EXISTS", 0, gin_idx_pos)
        assert create_pos >= 0


class TestMem009MealIndex:
    def test_meal_predicate_index_exists(self) -> None:
        """Upgrade creates a partial B-tree index for meal predicates."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_meal_predicate_valid_at" in source

    def test_meal_index_covers_predicate_and_valid_at(self) -> None:
        """Meal index covers (predicate, valid_at) columns."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "predicate, valid_at" in source

    def test_meal_index_is_partial_on_meal_prefix(self) -> None:
        """Meal index WHERE clause scopes to the 'meal_*' predicate range."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "predicate >= 'meal_'" in source
        assert "predicate < 'meal`'" in source

    def test_meal_index_filters_active_validity(self) -> None:
        """Meal index WHERE clause requires validity = 'active'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        # Locate the meal index block and verify validity filter is present
        meal_idx_pos = source.index("idx_facts_meal_predicate_valid_at")
        # Find the next index name to bound the search
        try:
            next_idx_pos = source.index("idx_facts_transaction_predicate_valid_at")
        except ValueError:
            next_idx_pos = len(source)
        meal_block = source[meal_idx_pos:next_idx_pos]
        assert "validity = 'active'" in meal_block


class TestMem009TransactionIndex:
    def test_transaction_predicate_index_exists(self) -> None:
        """Upgrade creates a partial B-tree index for transaction predicates."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_transaction_predicate_valid_at" in source

    def test_transaction_index_is_partial_on_transaction_prefix(self) -> None:
        """Transaction index WHERE clause scopes to the 'transaction_*' range."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "predicate >= 'transaction_'" in source
        assert "predicate < 'transaction`'" in source

    def test_transaction_index_filters_active_validity(self) -> None:
        """Transaction index WHERE clause requires validity = 'active'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        transaction_idx_pos = source.index("idx_facts_transaction_predicate_valid_at")
        try:
            next_idx_pos = source.index("idx_facts_measurement_predicate_valid_at")
        except ValueError:
            next_idx_pos = len(source)
        transaction_block = source[transaction_idx_pos:next_idx_pos]
        assert "validity = 'active'" in transaction_block


class TestMem009MeasurementIndex:
    def test_measurement_predicate_index_exists(self) -> None:
        """Upgrade creates a partial B-tree index for measurement predicates."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "idx_facts_measurement_predicate_valid_at" in source

    def test_measurement_index_is_partial_on_measurement_prefix(self) -> None:
        """Measurement index WHERE clause scopes to the 'measurement_*' range."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert "predicate >= 'measurement_'" in source
        assert "predicate < 'measurement`'" in source

    def test_measurement_index_filters_active_validity(self) -> None:
        """Measurement index WHERE clause requires validity = 'active'."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        measurement_idx_pos = source.index("idx_facts_measurement_predicate_valid_at")
        measurement_block = source[measurement_idx_pos:]
        assert "validity = 'active'" in measurement_block


class TestMem009AllIndexesIdempotent:
    def test_all_indexes_use_if_not_exists(self) -> None:
        """All four index creations use IF NOT EXISTS for idempotency."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        index_names = [
            "idx_facts_metadata_gin",
            "idx_facts_meal_predicate_valid_at",
            "idx_facts_transaction_predicate_valid_at",
            "idx_facts_measurement_predicate_valid_at",
        ]
        for name in index_names:
            idx_pos = source.index(name)
            create_stmt = source.rindex("CREATE INDEX IF NOT EXISTS", 0, idx_pos)
            assert create_stmt >= 0, f"Missing IF NOT EXISTS for {name}"

    def test_upgrade_creates_exactly_four_indexes(self) -> None:
        """Upgrade creates exactly four indexes (1 GIN + 3 partial B-tree)."""
        mod = _load_migration()
        source = inspect.getsource(mod.upgrade)
        assert source.count("CREATE INDEX IF NOT EXISTS") == 4


class TestMem009Downgrade:
    def test_downgrade_drops_metadata_gin_index(self) -> None:
        """Downgrade drops the GIN index on metadata."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_facts_metadata_gin" in source

    def test_downgrade_drops_meal_index(self) -> None:
        """Downgrade drops the meal predicate partial index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_facts_meal_predicate_valid_at" in source

    def test_downgrade_drops_transaction_index(self) -> None:
        """Downgrade drops the transaction predicate partial index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_facts_transaction_predicate_valid_at" in source

    def test_downgrade_drops_measurement_index(self) -> None:
        """Downgrade drops the measurement predicate partial index."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert "DROP INDEX IF EXISTS idx_facts_measurement_predicate_valid_at" in source

    def test_downgrade_drops_all_four_indexes(self) -> None:
        """Downgrade removes all four indexes created by upgrade."""
        mod = _load_migration()
        source = inspect.getsource(mod.downgrade)
        assert source.count("DROP INDEX IF EXISTS") == 4
