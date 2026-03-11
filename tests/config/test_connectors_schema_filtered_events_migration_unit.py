"""Unit tests for the core_026 connectors schema / filtered_events migration."""

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
    return chain_dir / "core_026_connectors_schema_filtered_events.py"


def _load_migration():
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("core_026", migration_file)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# upgrade() content assertions
# ---------------------------------------------------------------------------


def test_upgrade_creates_connectors_schema():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "CREATE SCHEMA IF NOT EXISTS connectors" in source


def test_upgrade_creates_filtered_events_table_partitioned():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "connectors.filtered_events" in source
    assert "PARTITION BY RANGE (received_at)" in source


def test_upgrade_filtered_events_has_required_columns():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    required_columns = [
        "id",
        "received_at",
        "connector_type",
        "endpoint_identity",
        "external_message_id",
        "source_channel",
        "sender_identity",
        "subject_or_preview",
        "filter_reason",
        "status",
        "full_payload",
        "error_detail",
        "replay_requested_at",
        "replay_completed_at",
        "created_at",
    ]
    for col in required_columns:
        assert col in source, f"Column {col!r} should appear in upgrade() source"


def test_upgrade_filtered_events_full_payload_is_jsonb():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "full_payload" in source
    assert "JSONB" in source


def test_upgrade_filtered_events_status_has_default():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "DEFAULT 'filtered'" in source


def test_upgrade_creates_partition_management_function():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "connectors_filtered_events_ensure_partition" in source
    assert "INTERVAL '1 month'" in source
    assert "filtered_events_" in source  # partition name prefix


def test_upgrade_creates_drain_and_timeline_indexes():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "ix_filtered_events_drain" in source
    assert "ix_filtered_events_timeline" in source
    # Drain index must cover connector_type, endpoint_identity, status, received_at
    assert "connector_type, endpoint_identity, status, received_at DESC" in source


def test_upgrade_ensures_current_and_next_month_partitions():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "connectors_filtered_events_ensure_partition(now())" in source
    assert "connectors_filtered_events_ensure_partition(now() + INTERVAL '1 month')" in source


def test_upgrade_grants_connector_writer_role():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "connector_writer" in source
    assert "GRANT USAGE" in source
    assert "GRANT SELECT" in source


# ---------------------------------------------------------------------------
# downgrade() content assertions
# ---------------------------------------------------------------------------


def test_downgrade_drops_connectors_schema():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)

    assert "DROP SCHEMA IF EXISTS connectors CASCADE" in source


def test_downgrade_drops_partition_function():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)

    assert "DROP FUNCTION IF EXISTS connectors_filtered_events_ensure_partition" in source
