"""Unit tests for the switchboard message_inbox partition migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Generic migration contract checks (file existence, metadata, callable guards, chain
# membership) for this migration are covered canonically in test_migration_contract.py.


def _migration_file() -> Path:
    """Return the switchboard message_inbox partition migration file path."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("switchboard")
    assert chain_dir is not None, "Switchboard chain should exist"
    return chain_dir / "008_partition_message_inbox_lifecycle.py"


def _load_migration():
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("migration_008", migration_file)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_defines_partitioned_lifecycle_schema():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "PARTITION BY RANGE (received_at)" in source
    assert "request_context JSONB" in source
    assert "raw_payload JSONB" in source
    assert "normalized_text TEXT" in source
    assert "decomposition_output JSONB" in source
    assert "dispatch_outcomes JSONB" in source
    assert "lifecycle_state TEXT" in source
    assert "schema_version TEXT" in source
    assert "processing_metadata JSONB" in source


def test_upgrade_defines_partition_automation_and_default_retention():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "switchboard_message_inbox_ensure_partition" in source
    assert "switchboard_message_inbox_drop_expired_partitions" in source
    assert "INTERVAL '1 month'" in source


def test_upgrade_defines_recent_and_source_indexes():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)

    assert "ix_message_inbox_recent_received_at" in source
    assert "ix_message_inbox_ctx_source_channel_received_at" in source
    assert "ix_message_inbox_ctx_source_sender_received_at" in source


def test_downgrade_reconstructs_legacy_sw007_shape():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)

    assert "CREATE TABLE message_inbox" in source
    assert "source_channel TEXT NOT NULL" in source
    assert "sender_id TEXT NOT NULL" in source
    assert "raw_content TEXT NOT NULL" in source
    assert "routing_results JSONB" in source
    assert "source_endpoint_identity TEXT NOT NULL" in source
    assert "source_sender_identity TEXT NOT NULL" in source
    assert "dedupe_key TEXT" in source
    assert "dedupe_strategy TEXT NOT NULL" in source
    assert "DROP FUNCTION IF EXISTS switchboard_message_inbox_drop_expired_partitions" in source
