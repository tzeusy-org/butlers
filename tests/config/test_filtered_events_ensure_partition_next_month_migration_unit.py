"""Unit tests for the core_028 filtered_events ensure_partition next-month migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Generic migration contract checks (file existence, metadata, callable guards, chain
# membership) for this migration are covered canonically in test_migration_contract.py.


def _migration_file() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("core")
    assert chain_dir is not None, "Core chain should exist"
    return chain_dir / "core_028_filtered_events_ensure_partition_next_month.py"


def _load_migration():
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("core_028", migration_file)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Migration metadata
# ---------------------------------------------------------------------------


def test_revision_is_core_028():
    module = _load_migration()
    assert module.revision == "core_028"


def test_down_revision_is_core_027():
    module = _load_migration()
    assert module.down_revision == "core_027"


# ---------------------------------------------------------------------------
# upgrade() content assertions
# ---------------------------------------------------------------------------


def test_upgrade_replaces_ensure_partition_function():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition" in source


def test_upgrade_creates_current_month_partition():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "date_trunc('month', reference_ts)" in source
    assert "INTERVAL '1 month'" in source
    assert "filtered_events_" in source  # partition name prefix


def test_upgrade_creates_next_month_partition_proactively():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    # Must have next_start/next_end variables for the second partition
    assert "next_start" in source
    assert "next_end" in source
    assert "next_name" in source


def test_upgrade_both_partitions_use_connectors_schema():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    # Both CREATE TABLE statements target the connectors schema
    assert "connectors.filtered_events" in source


def test_upgrade_function_returns_current_partition_name():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    # Function must still return the current (not next) partition name
    assert "RETURN partition_name" in source


def test_upgrade_calls_ensure_partition_after_definition():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    # Ensure the function is called to create partitions at migration time
    assert "connectors_filtered_events_ensure_partition(now())" in source


def test_upgrade_function_remains_idempotent():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    # Both CREATE TABLE statements use IF NOT EXISTS
    assert source.count("CREATE TABLE IF NOT EXISTS") >= 2


# ---------------------------------------------------------------------------
# downgrade() content assertions
# ---------------------------------------------------------------------------


def test_downgrade_restores_single_month_function():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)

    assert "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition" in source


def test_downgrade_single_month_only():
    """The downgrade version must NOT have next_start / next_end variables."""
    module = _load_migration()
    source = inspect.getsource(module.downgrade)

    assert "next_start" not in source
    assert "next_end" not in source
