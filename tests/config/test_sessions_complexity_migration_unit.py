"""Unit tests for the core_029 sessions complexity/resolution_source migration."""

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
    return chain_dir / "core_029_sessions_add_complexity_resolution_source.py"


def _load_migration():
    migration_file = _migration_file()
    spec = importlib.util.spec_from_file_location("core_029", migration_file)
    assert spec is not None, "Should be able to load migration spec"
    assert spec.loader is not None, "Should have a loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Migration metadata
# ---------------------------------------------------------------------------


def test_revision_is_core_029():
    module = _load_migration()
    assert module.revision == "core_029"


def test_down_revision_is_core_028():
    module = _load_migration()
    assert module.down_revision == "core_028"


# ---------------------------------------------------------------------------
# upgrade() content assertions
# ---------------------------------------------------------------------------


def test_upgrade_adds_complexity_column():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)
    assert "complexity" in source


def test_upgrade_adds_resolution_source_column():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)
    assert "resolution_source" in source


def test_upgrade_complexity_has_medium_default():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)
    assert "medium" in source


def test_upgrade_resolution_source_has_toml_fallback_default():
    module = _load_migration()
    source = inspect.getsource(module.upgrade)
    assert "toml_fallback" in source


# ---------------------------------------------------------------------------
# downgrade() content assertions
# ---------------------------------------------------------------------------


def test_downgrade_drops_resolution_source_column():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)
    assert "resolution_source" in source


def test_downgrade_drops_complexity_column():
    module = _load_migration()
    source = inspect.getsource(module.downgrade)
    assert "complexity" in source
