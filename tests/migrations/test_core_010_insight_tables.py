"""Unit tests for core_010_insight_tables migration.

Verifies:
1. Migration file exists and is importable.
2. Revision metadata is correct (ID, chain linkage).
3. upgrade() SQL covers all four tables with correct columns and constraints.
4. insight_settings default row is inserted idempotently.
5. downgrade() removes all four tables.

These are pure-unit tests — they inspect source code without executing SQL.
No Docker / PostgreSQL container is required.

Issue: bu-z4ek
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
MIGRATION_FILE = VERSIONS_DIR / "core_010_insight_tables.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_migration():
    """Dynamically load the core_010 migration module."""
    spec = importlib.util.spec_from_file_location("core_010_insight_tables", MIGRATION_FILE)
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
        assert MIGRATION_FILE.exists(), f"Migration file not found: {MIGRATION_FILE}"

    def test_migration_file_is_python(self) -> None:
        assert MIGRATION_FILE.suffix == ".py"


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id(self) -> None:
        mod = _load_migration()
        assert mod.revision == "core_010"

    def test_down_revision(self) -> None:
        """Must chain from core_009."""
        mod = _load_migration()
        assert mod.down_revision == "core_009"

    def test_branch_labels_are_none(self) -> None:
        """Inherits core branch; no new branch label."""
        mod = _load_migration()
        assert mod.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        mod = _load_migration()
        assert mod.depends_on is None

    def test_upgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self) -> None:
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — table creation
# ---------------------------------------------------------------------------


class TestUpgradeCreatesAllTables:
    def test_creates_insight_candidates(self) -> None:
        src = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS public.insight_candidates" in src

    def test_creates_insight_cooldowns(self) -> None:
        src = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS public.insight_cooldowns" in src

    def test_creates_insight_engagement(self) -> None:
        src = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS public.insight_engagement" in src

    def test_creates_insight_settings(self) -> None:
        src = inspect.getsource(_load_migration().upgrade)
        assert "CREATE TABLE IF NOT EXISTS public.insight_settings" in src


# ---------------------------------------------------------------------------
# upgrade() — insight_candidates columns and constraints
# ---------------------------------------------------------------------------


class TestInsightCandidatesSchema:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_has_uuid_pk(self) -> None:
        assert "id" in self._src
        assert "gen_random_uuid()" in self._src

    def test_has_origin_butler(self) -> None:
        assert "origin_butler" in self._src

    def test_has_priority(self) -> None:
        assert "priority" in self._src

    def test_has_category(self) -> None:
        assert "category" in self._src

    def test_has_dedup_key(self) -> None:
        assert "dedup_key" in self._src

    def test_has_cooldown_days(self) -> None:
        assert "cooldown_days" in self._src

    def test_has_expires_at(self) -> None:
        assert "expires_at" in self._src

    def test_has_message(self) -> None:
        assert "message" in self._src

    def test_has_channel(self) -> None:
        assert "channel" in self._src

    def test_has_metadata_jsonb(self) -> None:
        assert "metadata" in self._src
        assert "JSONB" in self._src

    def test_has_status_with_default_pending(self) -> None:
        assert "status" in self._src
        assert "'pending'" in self._src

    def test_has_delivered_at(self) -> None:
        assert "delivered_at" in self._src

    def test_priority_check_constraint(self) -> None:
        """Priority must be enforced as 1-100 via a CHECK constraint."""
        assert "chk_insight_candidates_priority" in self._src
        assert "priority BETWEEN 1 AND 100" in self._src

    def test_status_check_constraint(self) -> None:
        """Only valid status values are allowed."""
        assert "chk_insight_candidates_status" in self._src
        assert "pending" in self._src
        assert "delivered" in self._src
        assert "expired" in self._src
        assert "filtered" in self._src

    def test_dedup_key_nonempty_constraint(self) -> None:
        assert "chk_insight_candidates_dedup_key_nonempty" in self._src
        assert "dedup_key <> ''" in self._src

    def test_message_nonempty_constraint(self) -> None:
        assert "chk_insight_candidates_message_nonempty" in self._src
        assert "message <> ''" in self._src

    def test_has_pending_priority_index(self) -> None:
        assert "idx_insight_candidates_status_priority" in self._src

    def test_has_expires_at_index(self) -> None:
        assert "idx_insight_candidates_expires_at" in self._src

    def test_has_dedup_key_index(self) -> None:
        assert "idx_insight_candidates_dedup_key" in self._src


# ---------------------------------------------------------------------------
# upgrade() — insight_cooldowns columns
# ---------------------------------------------------------------------------


class TestInsightCooldownsSchema:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_has_dedup_key_pk(self) -> None:
        """dedup_key is the primary key."""
        assert "dedup_key" in self._src
        assert "TEXT PRIMARY KEY" in self._src

    def test_has_cooldown_until(self) -> None:
        assert "cooldown_until" in self._src

    def test_has_cooldown_until_index(self) -> None:
        assert "idx_insight_cooldowns_until" in self._src

    def test_dedup_key_nonempty_constraint(self) -> None:
        assert "chk_insight_cooldowns_dedup_key_nonempty" in self._src


# ---------------------------------------------------------------------------
# upgrade() — insight_engagement columns
# ---------------------------------------------------------------------------


class TestInsightEngagementSchema:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_has_insight_id_fk(self) -> None:
        """FK to public.insight_candidates."""
        assert "insight_id" in self._src
        assert "REFERENCES public.insight_candidates(id)" in self._src

    def test_fk_has_on_delete_cascade(self) -> None:
        assert "ON DELETE CASCADE" in self._src

    def test_has_delivered_at(self) -> None:
        assert "delivered_at" in self._src

    def test_has_engaged_boolean(self) -> None:
        assert "engaged" in self._src
        assert "BOOLEAN" in self._src
        assert "DEFAULT FALSE" in self._src

    def test_has_delivered_at_index(self) -> None:
        assert "idx_insight_engagement_delivered_at" in self._src


# ---------------------------------------------------------------------------
# upgrade() — insight_settings columns and default row
# ---------------------------------------------------------------------------


class TestInsightSettingsSchema:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().upgrade)

    def test_has_id_integer_pk(self) -> None:
        assert "id" in self._src
        assert "INTEGER PRIMARY KEY" in self._src

    def test_single_row_constraint(self) -> None:
        """id must be 1, enforcing single-row design."""
        assert "chk_insight_settings_single_row" in self._src
        assert "id = 1" in self._src

    def test_has_verbosity_with_default_minimal(self) -> None:
        assert "verbosity" in self._src
        assert "'minimal'" in self._src

    def test_verbosity_check_constraint(self) -> None:
        assert "chk_insight_settings_verbosity" in self._src
        # All four valid presets must be present.
        for preset in ("off", "minimal", "normal", "verbose"):
            assert preset in self._src

    def test_has_custom_budget(self) -> None:
        assert "custom_budget" in self._src

    def test_custom_budget_check_constraint(self) -> None:
        """Budget range 1-10."""
        assert "chk_insight_settings_custom_budget" in self._src
        assert "custom_budget BETWEEN 1 AND 10" in self._src

    def test_has_quiet_start(self) -> None:
        assert "quiet_start" in self._src

    def test_has_quiet_end(self) -> None:
        assert "quiet_end" in self._src

    def test_has_quiet_timezone(self) -> None:
        assert "quiet_timezone" in self._src

    def test_quiet_start_range_constraint(self) -> None:
        assert "chk_insight_settings_quiet_start" in self._src
        assert "quiet_start BETWEEN 0 AND 23" in self._src

    def test_quiet_end_range_constraint(self) -> None:
        assert "chk_insight_settings_quiet_end" in self._src
        assert "quiet_end BETWEEN 0 AND 23" in self._src

    def test_has_updated_at(self) -> None:
        assert "updated_at" in self._src

    def test_default_row_is_inserted(self) -> None:
        """Default settings row must be seeded."""
        assert "INSERT INTO public.insight_settings" in self._src
        assert "1, 'minimal'" in self._src

    def test_default_row_is_idempotent(self) -> None:
        """Seed must use ON CONFLICT to be idempotent."""
        assert "ON CONFLICT (id) DO NOTHING" in self._src


# ---------------------------------------------------------------------------
# downgrade() — removes all four tables
# ---------------------------------------------------------------------------


class TestDowngrade:
    @pytest.fixture(autouse=True)
    def _src(self) -> None:
        self._src = inspect.getsource(_load_migration().downgrade)

    def test_drops_insight_engagement(self) -> None:
        assert "DROP TABLE IF EXISTS public.insight_engagement" in self._src

    def test_drops_insight_cooldowns(self) -> None:
        assert "DROP TABLE IF EXISTS public.insight_cooldowns" in self._src

    def test_drops_insight_candidates(self) -> None:
        assert "DROP TABLE IF EXISTS public.insight_candidates" in self._src

    def test_drops_insight_settings(self) -> None:
        assert "DROP TABLE IF EXISTS public.insight_settings" in self._src

    def test_uses_cascade(self) -> None:
        """CASCADE ensures FK-dependent objects are also removed."""
        assert "CASCADE" in self._src

    def test_engagement_dropped_before_candidates(self) -> None:
        """engagement has a FK to candidates so must be dropped first."""
        engagement_pos = self._src.index("insight_engagement")
        candidates_pos = self._src.index("insight_candidates")
        assert engagement_pos < candidates_pos, (
            "insight_engagement must be dropped before insight_candidates"
        )
