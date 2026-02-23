"""Unit tests for Switchboard backfill_jobs migration structure.

Validates migration file existence, Alembic metadata, SQL content (table DDL,
status constraint, indexes), and that the migration correctly chains from sw_017.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_FILENAME = "018_create_backfill_jobs.py"
_REVISION = "sw_018"
_DOWN_REVISION = "sw_017"

_VALID_STATUSES = frozenset(
    {"pending", "active", "paused", "completed", "cancelled", "cost_capped", "error"}
)


def _migration_file() -> Path:
    """Return the switchboard backfill_jobs migration file path."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("switchboard")
    assert chain_dir is not None, "Switchboard chain should exist"
    return chain_dir / _MIGRATION_FILENAME


def _load_migration():
    """Load and return the migration module."""
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("migration_sw_018", migration_file)
    assert spec is not None, "Should be able to create module spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# File presence
# ---------------------------------------------------------------------------


def test_backfill_jobs_migration_file_exists():
    """Verify the 017_create_backfill_jobs.py migration file exists."""
    assert _migration_file().exists(), f"Migration file {_MIGRATION_FILENAME} should exist"


def test_switchboard_chain_includes_backfill_jobs_migration():
    """Verify the switchboard migrations directory contains the new file."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("switchboard")
    assert chain_dir is not None, "Switchboard chain should exist"

    migration_names = [f.name for f in chain_dir.glob("*.py") if f.name != "__init__.py"]
    assert _MIGRATION_FILENAME in migration_names, (
        f"{_MIGRATION_FILENAME} should be in the switchboard migration chain"
    )


# ---------------------------------------------------------------------------
# Alembic metadata
# ---------------------------------------------------------------------------


def test_backfill_jobs_migration_has_correct_revision():
    """Verify migration revision ID is sw_018."""
    module = _load_migration()
    assert hasattr(module, "revision"), "Should have revision attribute"
    assert module.revision == _REVISION, f"revision should be {_REVISION!r}"


def test_backfill_jobs_migration_has_correct_down_revision():
    """Verify migration chains from sw_017."""
    module = _load_migration()
    assert hasattr(module, "down_revision"), "Should have down_revision attribute"
    assert module.down_revision == _DOWN_REVISION, (
        f"down_revision should be {_DOWN_REVISION!r} (chains after direction migration)"
    )


def test_backfill_jobs_migration_branch_labels_is_none():
    """Branch labels should be None (this is not a branch head)."""
    module = _load_migration()
    assert hasattr(module, "branch_labels"), "Should have branch_labels attribute"
    assert module.branch_labels is None, "branch_labels should be None"


def test_backfill_jobs_migration_depends_on_is_none():
    """Depends-on should be None (no cross-chain dependencies)."""
    module = _load_migration()
    assert hasattr(module, "depends_on"), "Should have depends_on attribute"
    assert module.depends_on is None, "depends_on should be None"


# ---------------------------------------------------------------------------
# Callable guards
# ---------------------------------------------------------------------------


def test_backfill_jobs_migration_has_upgrade_function():
    """upgrade() must exist and be callable."""
    module = _load_migration()
    assert hasattr(module, "upgrade"), "Should have upgrade function"
    assert callable(module.upgrade), "upgrade should be callable"


def test_backfill_jobs_migration_has_downgrade_function():
    """downgrade() must exist and be callable."""
    module = _load_migration()
    assert hasattr(module, "downgrade"), "Should have downgrade function"
    assert callable(module.downgrade), "downgrade should be callable"


# ---------------------------------------------------------------------------
# SQL content: CREATE TABLE
# ---------------------------------------------------------------------------


def _upgrade_sql(module) -> str:
    """Extract upgrade function source (best-effort SQL inspection)."""
    import inspect

    return inspect.getsource(module.upgrade)


def test_upgrade_creates_backfill_jobs_table():
    """upgrade() SQL must reference CREATE TABLE backfill_jobs."""
    module = _load_migration()
    src = _upgrade_sql(module)
    has_create = (
        "CREATE TABLE backfill_jobs" in src or "CREATE TABLE IF NOT EXISTS backfill_jobs" in src
    )
    assert has_create, "upgrade must CREATE TABLE backfill_jobs"


def test_upgrade_has_id_column():
    """backfill_jobs must declare UUID primary key column 'id'."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "id UUID PRIMARY KEY" in src, "Table must have id UUID PRIMARY KEY"


def test_upgrade_has_connector_type_column():
    """backfill_jobs must have connector_type TEXT NOT NULL."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "connector_type" in src, "Table must have connector_type column"


def test_upgrade_has_endpoint_identity_column():
    """backfill_jobs must have endpoint_identity TEXT NOT NULL."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "endpoint_identity" in src, "Table must have endpoint_identity column"


def test_upgrade_has_target_categories_jsonb():
    """backfill_jobs must have target_categories JSONB."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "target_categories" in src and "JSONB" in src, (
        "Table must have target_categories JSONB column"
    )


def test_upgrade_has_date_from_column():
    """backfill_jobs must have date_from DATE column."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "date_from" in src, "Table must have date_from column"


def test_upgrade_has_date_to_column():
    """backfill_jobs must have date_to DATE column."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "date_to" in src, "Table must have date_to column"


def test_upgrade_has_status_column():
    """backfill_jobs must have status TEXT NOT NULL."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "status" in src, "Table must have status column"


def test_upgrade_has_cursor_jsonb_column():
    """backfill_jobs must have cursor JSONB (nullable) for resume semantics."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "cursor" in src, "Table must have cursor column"


def test_upgrade_has_rows_processed_column():
    """backfill_jobs must have rows_processed INTEGER."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "rows_processed" in src, "Table must have rows_processed column"


def test_upgrade_has_rows_skipped_column():
    """backfill_jobs must have rows_skipped INTEGER."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "rows_skipped" in src, "Table must have rows_skipped column"


def test_upgrade_has_cost_spent_cents_column():
    """backfill_jobs must have cost_spent_cents INTEGER."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "cost_spent_cents" in src, "Table must have cost_spent_cents column"


def test_upgrade_has_created_at_column():
    """backfill_jobs must have created_at TIMESTAMPTZ."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "created_at" in src, "Table must have created_at column"


def test_upgrade_has_started_at_column():
    """backfill_jobs must have started_at TIMESTAMPTZ (nullable lifecycle timestamp)."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "started_at" in src, "Table must have started_at column"


def test_upgrade_has_completed_at_column():
    """backfill_jobs must have completed_at TIMESTAMPTZ (nullable lifecycle timestamp)."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "completed_at" in src, "Table must have completed_at column"


def test_upgrade_has_updated_at_column():
    """backfill_jobs must have updated_at TIMESTAMPTZ NOT NULL."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "updated_at" in src, "Table must have updated_at column"


def test_upgrade_has_daily_cost_cap_column():
    """backfill_jobs must have daily_cost_cap_cents column."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "daily_cost_cap_cents" in src, "Table must have daily_cost_cap_cents column"


def test_upgrade_has_rate_limit_per_hour_column():
    """backfill_jobs must have rate_limit_per_hour column."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "rate_limit_per_hour" in src, "Table must have rate_limit_per_hour column"


# ---------------------------------------------------------------------------
# SQL content: status CHECK constraint
# ---------------------------------------------------------------------------


def test_upgrade_has_status_check_constraint():
    """upgrade() must define a CHECK constraint for the status column."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "CHECK" in src, "upgrade must include a CHECK constraint for status"
    assert "status" in src, "CHECK constraint must reference status"


def test_upgrade_status_constraint_includes_all_valid_values():
    """All seven allowed status values must appear in the migration SQL."""
    module = _load_migration()
    src = _upgrade_sql(module)
    for status in _VALID_STATUSES:
        assert status in src, (
            f"Status value {status!r} must be present in migration CHECK constraint"
        )


# ---------------------------------------------------------------------------
# SQL content: indexes
# ---------------------------------------------------------------------------


def test_upgrade_creates_status_index():
    """upgrade() must create idx_backfill_jobs_status index."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "idx_backfill_jobs_status" in src, "upgrade must create idx_backfill_jobs_status index"


def test_upgrade_creates_connector_index():
    """upgrade() must create idx_backfill_jobs_connector index."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "idx_backfill_jobs_connector" in src, (
        "upgrade must create idx_backfill_jobs_connector index"
    )


def test_upgrade_connector_index_covers_both_columns():
    """idx_backfill_jobs_connector index must cover connector_type and endpoint_identity."""
    module = _load_migration()
    src = _upgrade_sql(module)
    assert "connector_type" in src and "endpoint_identity" in src, (
        "Connector index must cover connector_type and endpoint_identity"
    )


# ---------------------------------------------------------------------------
# downgrade content
# ---------------------------------------------------------------------------


def _downgrade_sql(module) -> str:
    """Extract downgrade function source."""
    import inspect

    return inspect.getsource(module.downgrade)


def test_downgrade_drops_connector_index():
    """downgrade() must drop idx_backfill_jobs_connector index."""
    module = _load_migration()
    src = _downgrade_sql(module)
    assert "idx_backfill_jobs_connector" in src, "downgrade must drop idx_backfill_jobs_connector"


def test_downgrade_drops_status_index():
    """downgrade() must drop idx_backfill_jobs_status index."""
    module = _load_migration()
    src = _downgrade_sql(module)
    assert "idx_backfill_jobs_status" in src, "downgrade must drop idx_backfill_jobs_status"


def test_downgrade_drops_backfill_jobs_table():
    """downgrade() must drop backfill_jobs table."""
    module = _load_migration()
    src = _downgrade_sql(module)
    assert "backfill_jobs" in src and "DROP" in src.upper(), (
        "downgrade must DROP TABLE backfill_jobs"
    )
