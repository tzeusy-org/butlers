"""Unit tests for consolidated preference predicate seeding in mem_002."""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = MEMORY_MODULE_PATH / "migrations"
SEED_MIGRATION_FILE = MIGRATIONS_DIR / "002_seed_predicates.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_002_seed_predicates", SEED_MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPreferencesMigrationFile:
    def test_migration_file_exists(self) -> None:
        assert SEED_MIGRATION_FILE.exists()

    def test_revision_identifiers(self) -> None:
        mod = _load_migration()
        assert mod.revision == "mem_002"
        assert mod.down_revision == "mem_001"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration()
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))


class TestPreferencesMigrationContent:
    def test_required_preferences_predicates_present(self) -> None:
        src = inspect.getsource(_load_migration().upgrade)
        required = {
            "preferences:travel_flight_seat",
            "preferences:travel_flight_class",
            "preferences:travel_hotel_type",
            "preferences:travel_airline",
            "preferences:travel_meal",
            "preferences:health_dietary_restriction",
            "preferences:health_dietary_preference",
            "preferences:health_exercise_preference",
            "preferences:health_measurement_unit",
            "preferences:finance_currency",
            "preferences:finance_budget_period",
            "preferences:finance_rounding",
            "preferences:relationship_communication_style",
            "preferences:relationship_contact_frequency",
            "preferences:relationship_birthday_reminder_days",
            "preferences:home_temperature_unit",
            "preferences:home_comfort_temperature",
            "preferences:home_wake_time",
            "preferences:home_sleep_time",
            "preferences:general_communication_style",
            "preferences:general_language",
            "preferences:general_timezone",
            "preferences:general_name",
        }
        missing = sorted(name for name in required if name not in src)
        assert not missing, f"Missing predicates in mem_002 upgrade source: {missing}"

    def test_insert_is_conflict_safe(self) -> None:
        mod = _load_migration()
        src = inspect.getsource(mod._insert_predicate)
        assert "INSERT INTO predicate_registry" in src
        assert "ON CONFLICT (name) DO NOTHING" in src

    def test_downgrade_deletes_from_predicate_registry(self) -> None:
        src = inspect.getsource(_load_migration().downgrade)
        assert "DELETE FROM predicate_registry" in src
