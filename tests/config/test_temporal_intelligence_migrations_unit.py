"""Unit tests for temporal intelligence Alembic migrations.

Tests validate migration metadata correctness, schema structure, index presence,
check constraints, and downgrade completeness — all without requiring a live
database connection. Uses source-inspection style following the patterns in
test_finance_migration_unit.py and test_migration_contract.py.

Covers:
  - core_043: scheduled_tasks deadline columns (task 1.1)

Note: event_chains (task 1.2) is covered by core_013_event_chains.py (already
on main, tested elsewhere). seasonal_periods (task 1.3) is covered by
core_041_seasonal_periods.py (already on main, tested elsewhere).
delivery_preferences and deferred_notifications (tasks 1.4, 1.5) are covered
by core_012_temporal_intelligence.py and tested elsewhere.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"

_MIGRATION_043 = "core_043_deadline_columns.py"


def _load_migration(filename: str):
    """Dynamically load a migration module by filename."""
    migration_path = VERSIONS_DIR / filename
    assert migration_path.exists(), f"Migration file not found: {migration_path}"

    module_name = filename.removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    assert spec is not None, f"Could not load spec for {filename}"
    assert spec.loader is not None, f"No loader for {filename}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# core_043: scheduled_tasks deadline columns
# =============================================================================


class TestCore043RevisionMetadata:
    """Revision metadata checks for core_043."""

    def _mod(self):
        return _load_migration(_MIGRATION_043)

    def test_revision_id(self):
        assert self._mod().revision == "core_043"

    def test_down_revision(self):
        assert self._mod().down_revision == "core_042"

    def test_branch_labels_are_none(self):
        assert self._mod().branch_labels is None

    def test_depends_on_is_none(self):
        assert self._mod().depends_on is None

    def test_upgrade_callable(self):
        assert callable(self._mod().upgrade)

    def test_downgrade_callable(self):
        assert callable(self._mod().downgrade)


class TestCore043TaskTypeColumn:
    """Structural checks for the task_type column."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_043).upgrade)

    def test_task_type_column_add_if_not_exists(self):
        src = self._src()
        assert "ADD COLUMN IF NOT EXISTS task_type TEXT" in src

    def test_task_type_default_cron(self):
        assert "DEFAULT 'cron'" in self._src()

    def test_task_type_check_constraint_present(self):
        src = self._src()
        assert "CHECK (task_type IN ('cron', 'deadline'))" in src


class TestCore043DeadlineColumns:
    """Structural checks for target_date, lead_time_days, alert_thresholds."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_043).upgrade)

    def test_target_date_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS target_date DATE" in self._src()

    def test_lead_time_days_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS lead_time_days INTEGER" in self._src()

    def test_alert_thresholds_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS alert_thresholds JSONB" in self._src()

    def test_deadline_status_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS deadline_status TEXT" in self._src()

    def test_deadline_status_check_constraint_present(self):
        src = self._src()
        assert "pending" in src
        assert "alerted" in src
        assert "escalated" in src
        assert "completed" in src
        assert "expired" in src

    def test_fired_thresholds_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS fired_thresholds JSONB" in self._src()

    def test_depends_on_column_add_if_not_exists(self):
        assert "ADD COLUMN IF NOT EXISTS depends_on JSONB" in self._src()


class TestCore043Indexes:
    """Index checks for core_043."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_043).upgrade)

    def test_deadline_status_index_created(self):
        assert "idx_scheduled_tasks_deadline_status" in self._src()

    def test_deadline_status_index_if_not_exists(self):
        assert "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_deadline_status" in self._src()

    def test_deadline_status_index_partial_on_deadline_enabled(self):
        src = self._src()
        assert "task_type = 'deadline'" in src
        assert "enabled = true" in src


class TestCore043Downgrade:
    """Downgrade completeness checks for core_043."""

    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_043).downgrade)

    def test_downgrade_drops_index(self):
        assert "DROP INDEX IF EXISTS idx_scheduled_tasks_deadline_status" in self._src()

    def test_downgrade_drops_all_columns(self):
        src = self._src()
        for col in (
            "depends_on",
            "fired_thresholds",
            "deadline_status",
            "alert_thresholds",
            "lead_time_days",
            "target_date",
            "task_type",
        ):
            assert f"DROP COLUMN IF EXISTS {col}" in src, f"{col} not dropped in downgrade"

    def test_all_drop_column_use_if_exists(self):
        src = self._src()
        drop_count = src.count("DROP COLUMN")
        if_exists_count = src.count("DROP COLUMN IF EXISTS")
        assert drop_count == if_exists_count, (
            f"All DROP COLUMN must use IF EXISTS: {drop_count} DROP COLUMN, "
            f"{if_exists_count} with IF EXISTS"
        )


# =============================================================================
# Cross-cutting checks
# =============================================================================


class TestCrossCuttingMigrationConventions:
    """Conventions that must hold for the deadline columns migration."""

    def _upgrade_src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_043).upgrade)

    def test_migration_file_exists(self):
        assert (VERSIONS_DIR / _MIGRATION_043).exists()

    def test_upgrade_is_callable(self):
        assert callable(_load_migration(_MIGRATION_043).upgrade)

    def test_downgrade_is_callable(self):
        assert callable(_load_migration(_MIGRATION_043).downgrade)

    def test_migration_discoverable_via_chain(self):
        from butlers.migrations import _resolve_chain_dir

        chain_dir = _resolve_chain_dir("core")
        assert chain_dir is not None
        assert (chain_dir / _MIGRATION_043).exists()
