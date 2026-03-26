"""Unit tests for next-month partition behavior in core_007 connectors migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _migration_file() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("core")
    assert chain_dir is not None
    return chain_dir / "core_007_connectors.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_007_connectors", _migration_file())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata() -> None:
    module = _load_migration()
    assert module.revision == "core_007"
    assert module.down_revision == "core_006"


def test_upgrade_function_defines_next_month_partition_logic() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "next_start" in source
    assert "next_end" in source
    assert "next_name" in source
    assert "CREATE TABLE IF NOT EXISTS connectors.%I" in source


def test_upgrade_bootstrap_calls_partition_function() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "connectors.connectors_filtered_events_ensure_partition(now())" in source


def test_downgrade_drops_partition_functions() -> None:
    source = inspect.getsource(_load_migration().downgrade)
    assert "connectors_filtered_events_ensure_partition(TIMESTAMPTZ)" in source
    assert "connectors.connectors_filtered_events_ensure_partition(TIMESTAMPTZ)" in source
