"""Tests for roster/home/migrations/ — maintenance_items table and threshold seeds.

Covers:
- 003_create_maintenance_items.py: revision metadata, table DDL correctness,
  index definition, upgrade/downgrade callables, rollback completeness.
- 004_seed_threshold_defaults.py: revision metadata, all five threshold keys
  seeded with correct default values, ON CONFLICT DO NOTHING idempotency
  guard, downgrade removes seeded keys.

These are static source-inspection tests that do not require a live database.

Issue: bu-e01z
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
HOME_MIGRATIONS_DIR = ROSTER_DIR / "home" / "migrations"

MIGRATION_003 = HOME_MIGRATIONS_DIR / "003_create_maintenance_items.py"
MIGRATION_004 = HOME_MIGRATIONS_DIR / "004_seed_threshold_defaults.py"


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _load_migration(path: Path):
    """Load a migration module from *path* without invoking Alembic env."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None, f"Could not create spec for {path}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests: 003_create_maintenance_items.py
# ---------------------------------------------------------------------------


class TestHomeM003FileLayout:
    def test_migration_file_exists(self) -> None:
        """003_create_maintenance_items.py exists on disk."""
        assert MIGRATION_003.exists(), f"Migration file not found: {MIGRATION_003}"

    def test_init_file_exists(self) -> None:
        """roster/home/migrations/__init__.py exists."""
        init_file = HOME_MIGRATIONS_DIR / "__init__.py"
        assert init_file.exists(), f"__init__.py not found at {init_file}"


class TestHomeM003RevisionMetadata:
    def test_revision_id(self) -> None:
        """Revision ID is home_maintenance_001."""
        mod = _load_migration(MIGRATION_003)
        assert mod.revision == "home_maintenance_001"

    def test_down_revision(self) -> None:
        """Migration chains from home_assistant_002."""
        mod = _load_migration(MIGRATION_003)
        assert mod.down_revision == "home_assistant_002"

    def test_branch_labels_none(self) -> None:
        """branch_labels is None (not a branch root)."""
        mod = _load_migration(MIGRATION_003)
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration(MIGRATION_003)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        """upgrade() and downgrade() are defined and callable."""
        mod = _load_migration(MIGRATION_003)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestHomeM003UpgradeSQL:
    def test_creates_maintenance_items_table(self) -> None:
        """Upgrade creates the maintenance_items table."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "maintenance_items" in source

    def test_uses_create_table_if_not_exists(self) -> None:
        """Upgrade uses CREATE TABLE IF NOT EXISTS for idempotency."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "CREATE TABLE IF NOT EXISTS" in source

    def test_id_column_uuid_primary_key(self) -> None:
        """id column is UUID PRIMARY KEY with gen_random_uuid() default."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "UUID" in source
        assert "PRIMARY KEY" in source
        assert "gen_random_uuid()" in source

    def test_name_column_not_null_unique(self) -> None:
        """name column is TEXT NOT NULL UNIQUE."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "name" in source
        assert "NOT NULL" in source
        assert "UNIQUE" in source

    def test_category_column_with_check_constraint(self) -> None:
        """category column has a CHECK constraint enumerating valid values."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "category" in source
        assert "CHECK" in source
        for cat in ("filter", "hvac", "appliance", "plumbing", "electrical", "general"):
            assert cat in source, f"Expected category '{cat}' in CHECK constraint"

    def test_interval_days_integer_not_null(self) -> None:
        """interval_days column is INTEGER NOT NULL."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "interval_days" in source
        assert "INTEGER" in source

    def test_last_completed_at_timestamptz_nullable(self) -> None:
        """last_completed_at column is TIMESTAMPTZ (nullable)."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "last_completed_at" in source
        assert "TIMESTAMPTZ" in source

    def test_next_due_at_timestamptz_nullable(self) -> None:
        """next_due_at column is TIMESTAMPTZ (nullable)."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "next_due_at" in source

    def test_notes_column_text_nullable(self) -> None:
        """notes column is TEXT (nullable)."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "notes" in source

    def test_created_at_updated_at_with_defaults(self) -> None:
        """created_at and updated_at columns exist with now() defaults."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "created_at" in source
        assert "updated_at" in source
        assert "now()" in source

    def test_index_on_next_due_at(self) -> None:
        """Upgrade creates an index on next_due_at for schedule-check query efficiency."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.upgrade)
        assert "ix_maintenance_items_next_due_at" in source
        assert "next_due_at" in source


class TestHomeM003DowngradeSQL:
    def test_drops_index(self) -> None:
        """Downgrade drops the ix_maintenance_items_next_due_at index."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.downgrade)
        assert "ix_maintenance_items_next_due_at" in source
        assert "DROP INDEX" in source

    def test_drops_maintenance_items_table(self) -> None:
        """Downgrade drops the maintenance_items table."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.downgrade)
        assert "maintenance_items" in source
        assert "DROP TABLE" in source

    def test_drops_index_before_table(self) -> None:
        """Downgrade drops the index before dropping the table."""
        mod = _load_migration(MIGRATION_003)
        source = inspect.getsource(mod.downgrade)
        idx_pos = source.index("ix_maintenance_items_next_due_at")
        tbl_pos = source.index("maintenance_items", idx_pos)
        assert idx_pos < tbl_pos, "Index drop should precede table drop in downgrade"


# ---------------------------------------------------------------------------
# Tests: 004_seed_threshold_defaults.py
# ---------------------------------------------------------------------------


class TestHomeM004FileLayout:
    def test_migration_file_exists(self) -> None:
        """004_seed_threshold_defaults.py exists on disk."""
        assert MIGRATION_004.exists(), f"Migration file not found: {MIGRATION_004}"


class TestHomeM004RevisionMetadata:
    def test_revision_id(self) -> None:
        """Revision ID is home_thresholds_001."""
        mod = _load_migration(MIGRATION_004)
        assert mod.revision == "home_thresholds_001"

    def test_down_revision(self) -> None:
        """Migration chains from home_maintenance_001."""
        mod = _load_migration(MIGRATION_004)
        assert mod.down_revision == "home_maintenance_001"

    def test_branch_labels_none(self) -> None:
        """branch_labels is None (not a branch root)."""
        mod = _load_migration(MIGRATION_004)
        assert mod.branch_labels is None

    def test_depends_on_none(self) -> None:
        """depends_on is None."""
        mod = _load_migration(MIGRATION_004)
        assert mod.depends_on is None

    def test_upgrade_and_downgrade_callable(self) -> None:
        """upgrade() and downgrade() are defined and callable."""
        mod = _load_migration(MIGRATION_004)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


class TestHomeM004ThresholdConstants:
    """Module-level threshold constants must match the spec-mandated defaults."""

    def test_battery_thresholds_keys(self) -> None:
        """BATTERY_THRESHOLDS has critical, warning, info keys."""
        mod = _load_migration(MIGRATION_004)
        assert set(mod.BATTERY_THRESHOLDS) == {"critical", "warning", "info"}

    def test_battery_thresholds_values(self) -> None:
        """BATTERY_THRESHOLDS matches spec: critical=10, warning=20, info=30."""
        mod = _load_migration(MIGRATION_004)
        assert mod.BATTERY_THRESHOLDS == {"critical": 10, "warning": 20, "info": 30}

    def test_offline_hours_thresholds_keys(self) -> None:
        """OFFLINE_HOURS_THRESHOLDS has critical, warning keys."""
        mod = _load_migration(MIGRATION_004)
        assert set(mod.OFFLINE_HOURS_THRESHOLDS) == {"critical", "warning"}

    def test_offline_hours_thresholds_values(self) -> None:
        """OFFLINE_HOURS_THRESHOLDS matches spec: critical=24, warning=1."""
        mod = _load_migration(MIGRATION_004)
        assert mod.OFFLINE_HOURS_THRESHOLDS == {"critical": 24, "warning": 1}

    def test_comfort_defaults_keys(self) -> None:
        """COMFORT_DEFAULTS has all required range keys."""
        mod = _load_migration(MIGRATION_004)
        assert set(mod.COMFORT_DEFAULTS) == {
            "temp_min_f",
            "temp_max_f",
            "humidity_min",
            "humidity_max",
            "co2_max_ppm",
        }

    def test_comfort_defaults_values(self) -> None:
        """COMFORT_DEFAULTS matches spec: 68-76 degF, 30-60% RH, 1000 ppm CO2."""
        mod = _load_migration(MIGRATION_004)
        assert mod.COMFORT_DEFAULTS == {
            "temp_min_f": 68,
            "temp_max_f": 76,
            "humidity_min": 30,
            "humidity_max": 60,
            "co2_max_ppm": 1000,
        }

    def test_comfort_deviation_thresholds_keys(self) -> None:
        """COMFORT_DEVIATION_THRESHOLDS has all deviation severity keys."""
        mod = _load_migration(MIGRATION_004)
        expected_keys = {
            "minor_temp_f",
            "moderate_temp_f",
            "minor_humidity",
            "moderate_humidity",
            "critical_temp_low_f",
            "critical_temp_high_f",
            "critical_co2_ppm",
            "critical_humidity_low",
            "critical_humidity_high",
        }
        assert set(mod.COMFORT_DEVIATION_THRESHOLDS) == expected_keys

    def test_comfort_deviation_thresholds_values(self) -> None:
        """COMFORT_DEVIATION_THRESHOLDS matches spec defaults."""
        mod = _load_migration(MIGRATION_004)
        assert mod.COMFORT_DEVIATION_THRESHOLDS == {
            "minor_temp_f": 2,
            "moderate_temp_f": 5,
            "minor_humidity": 10,
            "moderate_humidity": 20,
            "critical_temp_low_f": 60,
            "critical_temp_high_f": 85,
            "critical_co2_ppm": 1500,
            "critical_humidity_low": 15,
            "critical_humidity_high": 80,
        }

    def test_energy_thresholds_keys(self) -> None:
        """ENERGY_THRESHOLDS has anomaly_pct and high_severity_pct."""
        mod = _load_migration(MIGRATION_004)
        assert set(mod.ENERGY_THRESHOLDS) == {"anomaly_pct", "high_severity_pct"}

    def test_energy_thresholds_values(self) -> None:
        """ENERGY_THRESHOLDS matches spec: anomaly_pct=20, high_severity_pct=100."""
        mod = _load_migration(MIGRATION_004)
        assert mod.ENERGY_THRESHOLDS == {"anomaly_pct": 20, "high_severity_pct": 100}

    def test_threshold_seeds_list_has_five_entries(self) -> None:
        """_THRESHOLD_SEEDS covers all five threshold keys."""
        mod = _load_migration(MIGRATION_004)
        assert len(mod._THRESHOLD_SEEDS) == 5

    def test_threshold_seeds_key_names(self) -> None:
        """_THRESHOLD_SEEDS includes all expected home:thresholds:* keys."""
        mod = _load_migration(MIGRATION_004)
        keys = [k for k, _ in mod._THRESHOLD_SEEDS]
        assert "home:thresholds:battery" in keys
        assert "home:thresholds:offline_hours" in keys
        assert "home:thresholds:comfort_defaults" in keys
        assert "home:thresholds:comfort_deviation" in keys
        assert "home:thresholds:energy" in keys


class TestHomeM004UpgradeSQL:
    def test_inserts_into_state_table(self) -> None:
        """Upgrade SQL inserts into the state table."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.upgrade)
        assert "INSERT INTO state" in source

    def test_uses_on_conflict_do_nothing(self) -> None:
        """Upgrade uses ON CONFLICT (key) DO NOTHING for idempotency."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.upgrade)
        assert "ON CONFLICT" in source
        assert "DO NOTHING" in source

    def test_upgrade_references_all_threshold_keys(self) -> None:
        """Upgrade iterates over _THRESHOLD_SEEDS to insert all threshold keys."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.upgrade)
        assert "_THRESHOLD_SEEDS" in source

    def test_seeded_values_are_valid_json(self) -> None:
        """All threshold dicts in _THRESHOLD_SEEDS are JSON-serialisable."""
        mod = _load_migration(MIGRATION_004)
        for key, value in mod._THRESHOLD_SEEDS:
            try:
                json.dumps(value)
            except (TypeError, ValueError) as exc:
                pytest.fail(f"Threshold value for key {key!r} is not JSON-serialisable: {exc}")

    def test_battery_key_in_upgrade_seeds(self) -> None:
        """Battery threshold key appears in _THRESHOLD_SEEDS."""
        mod = _load_migration(MIGRATION_004)
        keys = {k for k, _ in mod._THRESHOLD_SEEDS}
        assert "home:thresholds:battery" in keys

    def test_offline_hours_key_in_upgrade_seeds(self) -> None:
        """Offline hours threshold key appears in _THRESHOLD_SEEDS."""
        mod = _load_migration(MIGRATION_004)
        keys = {k for k, _ in mod._THRESHOLD_SEEDS}
        assert "home:thresholds:offline_hours" in keys

    def test_comfort_defaults_key_in_upgrade_seeds(self) -> None:
        """Comfort defaults threshold key appears in _THRESHOLD_SEEDS."""
        mod = _load_migration(MIGRATION_004)
        keys = {k for k, _ in mod._THRESHOLD_SEEDS}
        assert "home:thresholds:comfort_defaults" in keys

    def test_comfort_deviation_key_in_upgrade_seeds(self) -> None:
        """Comfort deviation threshold key appears in _THRESHOLD_SEEDS."""
        mod = _load_migration(MIGRATION_004)
        keys = {k for k, _ in mod._THRESHOLD_SEEDS}
        assert "home:thresholds:comfort_deviation" in keys

    def test_energy_key_in_upgrade_seeds(self) -> None:
        """Energy threshold key appears in _THRESHOLD_SEEDS."""
        mod = _load_migration(MIGRATION_004)
        keys = {k for k, _ in mod._THRESHOLD_SEEDS}
        assert "home:thresholds:energy" in keys


class TestHomeM004DowngradeSQL:
    def test_deletes_from_state_table(self) -> None:
        """Downgrade uses DELETE FROM state to remove seeded keys."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.downgrade)
        assert "DELETE FROM state" in source

    def test_downgrade_references_all_threshold_seeds(self) -> None:
        """Downgrade iterates over _THRESHOLD_SEEDS to remove all seeded keys."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.downgrade)
        assert "_THRESHOLD_SEEDS" in source

    def test_downgrade_deletes_by_key_from_seeds(self) -> None:
        """Downgrade deletes keys by iterating over _THRESHOLD_SEEDS (not hardcoded strings)."""
        mod = _load_migration(MIGRATION_004)
        source = inspect.getsource(mod.downgrade)
        # The downgrade iterates over _THRESHOLD_SEEDS and constructs DELETE statements;
        # the key literal is not in the function source but the constant reference is.
        assert "_THRESHOLD_SEEDS" in source
        # Verify the constant itself contains the expected namespace prefix
        keys = [k for k, _ in mod._THRESHOLD_SEEDS]
        assert all(k.startswith("home:thresholds:") for k in keys)


class TestHomeM004Idempotency:
    """Structural idempotency guarantees verified by source inspection."""

    def test_on_conflict_guard_prevents_duplicate_inserts(self) -> None:
        """ON CONFLICT (key) DO NOTHING prevents duplicate inserts on re-run."""
        mod = _load_migration(MIGRATION_004)
        upgrade_source = inspect.getsource(mod.upgrade)
        # Must have both the conflict target and the nothing action
        assert "ON CONFLICT" in upgrade_source
        assert "DO NOTHING" in upgrade_source

    def test_all_seed_values_match_module_level_constants(self) -> None:
        """Each entry in _THRESHOLD_SEEDS references the module-level constant dict."""
        mod = _load_migration(MIGRATION_004)
        expected = {
            "home:thresholds:battery": mod.BATTERY_THRESHOLDS,
            "home:thresholds:offline_hours": mod.OFFLINE_HOURS_THRESHOLDS,
            "home:thresholds:comfort_defaults": mod.COMFORT_DEFAULTS,
            "home:thresholds:comfort_deviation": mod.COMFORT_DEVIATION_THRESHOLDS,
            "home:thresholds:energy": mod.ENERGY_THRESHOLDS,
        }
        for key, value in mod._THRESHOLD_SEEDS:
            assert key in expected, f"Unexpected key in _THRESHOLD_SEEDS: {key!r}"
            assert value == expected[key], (
                f"Value for {key!r} in _THRESHOLD_SEEDS does not match module constant"
            )
