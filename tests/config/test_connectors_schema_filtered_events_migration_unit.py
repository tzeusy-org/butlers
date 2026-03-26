"""Unit tests for consolidated connectors migration (core_007)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _migration_file() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("core")
    assert chain_dir is not None, "Core chain should exist"
    return chain_dir / "core_007_connectors.py"


def _load_migration():
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("core_007_connectors", migration_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata() -> None:
    mod = _load_migration()
    assert mod.revision == "core_007"
    assert mod.down_revision == "core_006"


def test_upgrade_creates_connectors_filtered_events_partitioned() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "CREATE SCHEMA IF NOT EXISTS connectors" in source
    assert "CREATE TABLE IF NOT EXISTS connectors.filtered_events" in source
    assert "PARTITION BY RANGE (received_at)" in source


def test_upgrade_creates_partition_functions_and_invokes_bootstrap() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert (
        "CREATE OR REPLACE FUNCTION connectors.connectors_filtered_events_ensure_partition"
        in source
    )
    assert "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition" in source
    assert "SELECT connectors.connectors_filtered_events_ensure_partition(now())" in source


def test_upgrade_creates_indexes_and_grants() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "ix_filtered_events_drain" in source
    assert "ix_filtered_events_timeline" in source
    assert "connector_writer" in source


def test_downgrade_drops_connectors_schema() -> None:
    source = inspect.getsource(_load_migration().downgrade)
    assert "DROP SCHEMA IF EXISTS connectors CASCADE" in source
