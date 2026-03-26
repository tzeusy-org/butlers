"""Unit tests for sessions complexity fields in core_001 foundation migration."""

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
    return chain_dir / "core_001_foundation.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_001_foundation", _migration_file())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata() -> None:
    module = _load_migration()
    assert module.revision == "core_001"


def test_sessions_include_complexity_and_resolution_source_defaults() -> None:
    source = inspect.getsource(_load_migration()._create_core_tables)
    assert "complexity TEXT DEFAULT 'medium'" in source
    assert "resolution_source TEXT DEFAULT 'toml_fallback'" in source


def test_downgrade_drops_sessions_table() -> None:
    source = inspect.getsource(_load_migration().downgrade)
    assert "DROP TABLE IF EXISTS sessions" in source
