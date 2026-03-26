"""Unit tests for the preferences predicates migration (026_preferences_predicates).

Validates that:
- The migration file exists and has correct revision metadata
- All required preference predicates are defined (by domain)
- Each predicate meets the spec's expected_subject_type and is_edge requirements
- The migration uses ON CONFLICT DO NOTHING (idempotent)
- The downgrade removes all seeded predicates
"""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = MEMORY_MODULE_PATH / "migrations"
PREFERENCES_MIGRATION_FILE = MIGRATIONS_DIR / "026_preferences_predicates.py"


def _load_migration(filename: str):
    """Load a migration module by filename from the migrations directory."""
    filepath = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), filepath)
    assert spec is not None, f"Could not load spec for {filepath}"
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPreferencesMigrationFile:
    """Verify the migration file structure and revision metadata."""

    def test_migration_file_exists(self) -> None:
        assert PREFERENCES_MIGRATION_FILE.exists(), (
            f"Expected migration file at {PREFERENCES_MIGRATION_FILE}"
        )

    def test_migration_chain_includes_preferences(self) -> None:
        """026_preferences_predicates.py is present in the migrations directory."""
        files = [p.name for p in MIGRATIONS_DIR.iterdir() if p.suffix == ".py"]
        assert "026_preferences_predicates.py" in files

    def test_revision_identifiers(self) -> None:
        mod = _load_migration("026_preferences_predicates.py")
        assert mod.revision == "mem_026"
        # Must chain from the correct parent
        assert mod.down_revision == "mem_025c"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration("026_preferences_predicates.py")
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))


class TestPreferencesMigrationContent:
    """Validate the SQL content of the migration upgrade and downgrade functions."""

    @pytest.fixture(scope="class")
    def mod(self):
        return _load_migration("026_preferences_predicates.py")

    @pytest.fixture(scope="class")
    def predicate_names(self, mod) -> set[str]:
        return {name for name, _ in mod._PREFERENCES_PREDICATES}

    @pytest.fixture(scope="class")
    def upgrade_source(self, mod) -> str:
        return inspect.getsource(mod.upgrade)

    @pytest.fixture(scope="class")
    def downgrade_source(self, mod) -> str:
        return inspect.getsource(mod.downgrade)

    def test_insert_targets_predicate_registry(self, upgrade_source: str) -> None:
        assert "predicate_registry" in upgrade_source

    def test_is_idempotent_on_conflict_do_nothing(self, upgrade_source: str) -> None:
        """Migration must use ON CONFLICT DO NOTHING for idempotency."""
        assert "ON CONFLICT" in upgrade_source
        assert "DO NOTHING" in upgrade_source

    def test_downgrade_references_predicate_registry(self, downgrade_source: str) -> None:
        assert "predicate_registry" in downgrade_source

    # ------------------------------------------------------------------
    # Travel domain predicates (checked via _PREFERENCES_PREDICATES list)
    # ------------------------------------------------------------------

    def test_travel_flight_seat_seeded(self, predicate_names: set) -> None:
        assert "preferences:travel_flight_seat" in predicate_names

    def test_travel_flight_class_seeded(self, predicate_names: set) -> None:
        assert "preferences:travel_flight_class" in predicate_names

    def test_travel_hotel_type_seeded(self, predicate_names: set) -> None:
        assert "preferences:travel_hotel_type" in predicate_names

    def test_travel_airline_seeded(self, predicate_names: set) -> None:
        assert "preferences:travel_airline" in predicate_names

    def test_travel_meal_seeded(self, predicate_names: set) -> None:
        assert "preferences:travel_meal" in predicate_names

    # ------------------------------------------------------------------
    # Health domain predicates
    # ------------------------------------------------------------------

    def test_health_dietary_restriction_seeded(self, predicate_names: set) -> None:
        assert "preferences:health_dietary_restriction" in predicate_names

    def test_health_dietary_preference_seeded(self, predicate_names: set) -> None:
        assert "preferences:health_dietary_preference" in predicate_names

    def test_health_exercise_preference_seeded(self, predicate_names: set) -> None:
        assert "preferences:health_exercise_preference" in predicate_names

    def test_health_measurement_unit_seeded(self, predicate_names: set) -> None:
        assert "preferences:health_measurement_unit" in predicate_names

    # ------------------------------------------------------------------
    # Finance domain predicates
    # ------------------------------------------------------------------

    def test_finance_currency_seeded(self, predicate_names: set) -> None:
        assert "preferences:finance_currency" in predicate_names

    def test_finance_budget_period_seeded(self, predicate_names: set) -> None:
        assert "preferences:finance_budget_period" in predicate_names

    def test_finance_rounding_seeded(self, predicate_names: set) -> None:
        assert "preferences:finance_rounding" in predicate_names

    # ------------------------------------------------------------------
    # Relationship domain predicates
    # ------------------------------------------------------------------

    def test_relationship_communication_style_seeded(self, predicate_names: set) -> None:
        assert "preferences:relationship_communication_style" in predicate_names

    def test_relationship_contact_frequency_seeded(self, predicate_names: set) -> None:
        assert "preferences:relationship_contact_frequency" in predicate_names

    def test_relationship_birthday_reminder_days_seeded(self, predicate_names: set) -> None:
        assert "preferences:relationship_birthday_reminder_days" in predicate_names

    # ------------------------------------------------------------------
    # Home domain predicates
    # ------------------------------------------------------------------

    def test_home_temperature_unit_seeded(self, predicate_names: set) -> None:
        assert "preferences:home_temperature_unit" in predicate_names

    def test_home_comfort_temperature_seeded(self, predicate_names: set) -> None:
        assert "preferences:home_comfort_temperature" in predicate_names

    def test_home_wake_time_seeded(self, predicate_names: set) -> None:
        assert "preferences:home_wake_time" in predicate_names

    def test_home_sleep_time_seeded(self, predicate_names: set) -> None:
        assert "preferences:home_sleep_time" in predicate_names

    # ------------------------------------------------------------------
    # General domain predicates
    # ------------------------------------------------------------------

    def test_general_communication_style_seeded(self, predicate_names: set) -> None:
        assert "preferences:general_communication_style" in predicate_names

    def test_general_language_seeded(self, predicate_names: set) -> None:
        assert "preferences:general_language" in predicate_names

    def test_general_timezone_seeded(self, predicate_names: set) -> None:
        assert "preferences:general_timezone" in predicate_names

    def test_general_name_seeded(self, predicate_names: set) -> None:
        assert "preferences:general_name" in predicate_names

    # ------------------------------------------------------------------
    # Predicate registry field requirements (inspected via upgrade source)
    # ------------------------------------------------------------------

    def test_expected_subject_type_person_used(self, upgrade_source: str) -> None:
        """upgrade() must reference 'person' for expected_subject_type."""
        assert "'person'" in upgrade_source or '"person"' in upgrade_source

    def test_is_edge_false_used(self, upgrade_source: str) -> None:
        """upgrade() must explicitly set is_edge=false."""
        assert "false" in upgrade_source.lower()


class TestPreferencesMigrationModule:
    """Verify the migration module constants are consistent with the spec."""

    def test_all_required_predicates_in_module_list(self) -> None:
        """The _PREFERENCES_PREDICATES list contains all spec-required predicates."""
        mod = _load_migration("026_preferences_predicates.py")
        names = {name for name, _ in mod._PREFERENCES_PREDICATES}

        required = {
            # Travel
            "preferences:travel_flight_seat",
            "preferences:travel_flight_class",
            "preferences:travel_hotel_type",
            "preferences:travel_airline",
            "preferences:travel_meal",
            # Health
            "preferences:health_dietary_restriction",
            "preferences:health_dietary_preference",
            "preferences:health_exercise_preference",
            "preferences:health_measurement_unit",
            # Finance
            "preferences:finance_currency",
            "preferences:finance_budget_period",
            "preferences:finance_rounding",
            # Relationship
            "preferences:relationship_communication_style",
            "preferences:relationship_contact_frequency",
            "preferences:relationship_birthday_reminder_days",
            # Home
            "preferences:home_temperature_unit",
            "preferences:home_comfort_temperature",
            "preferences:home_wake_time",
            "preferences:home_sleep_time",
            # General
            "preferences:general_communication_style",
            "preferences:general_language",
            "preferences:general_timezone",
            "preferences:general_name",
        }

        missing = required - names
        assert not missing, f"Missing predicates in migration: {sorted(missing)}"

    def test_all_predicates_use_preferences_namespace(self) -> None:
        """Every predicate in the list starts with 'preferences:'."""
        mod = _load_migration("026_preferences_predicates.py")
        for name, _ in mod._PREFERENCES_PREDICATES:
            assert name.startswith("preferences:"), (
                f"Predicate {name!r} does not use 'preferences:' namespace"
            )

    def test_all_predicates_have_non_empty_description(self) -> None:
        """Every predicate has a non-empty description."""
        mod = _load_migration("026_preferences_predicates.py")
        for name, desc in mod._PREFERENCES_PREDICATES:
            assert desc.strip(), f"Predicate {name!r} has empty description"

    def test_downgrade_references_module_predicates(self) -> None:
        """downgrade() must use the same _PREFERENCES_PREDICATES list as upgrade().

        Because both functions use the module-level constant, a single check
        that _PREFERENCES_PREDICATES is referenced by name in the downgrade source
        is sufficient to ensure both are in sync.
        """
        mod = _load_migration("026_preferences_predicates.py")
        downgrade_src = inspect.getsource(mod.downgrade)
        assert "_PREFERENCES_PREDICATES" in downgrade_src

    def test_downgrade_deletes_from_predicate_registry(self) -> None:
        """downgrade() must execute a DELETE against predicate_registry."""
        mod = _load_migration("026_preferences_predicates.py")
        downgrade_src = inspect.getsource(mod.downgrade)
        assert "DELETE" in downgrade_src.upper()
        assert "predicate_registry" in downgrade_src
