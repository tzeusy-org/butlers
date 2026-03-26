"""Regression tests for memory schema tenant/consolidation columns in mem_001 baseline."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_FILE = MODULES_DIR / "memory" / "migrations" / "001_memory_schema.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_001_memory_schema", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mem001_file_exists() -> None:
    assert MIGRATION_FILE.exists(), f"Migration file not found at {MIGRATION_FILE}"


def test_mem001_revision_identifiers() -> None:
    mod = _load_migration()
    assert mod.revision == "mem_001"
    assert mod.down_revision is None


def test_episodes_use_consolidation_attempts_not_retry_count() -> None:
    src = inspect.getsource(_load_migration().upgrade)
    assert "consolidation_attempts" in src
    assert "last_consolidation_error" in src


def test_tenant_lineage_columns_present() -> None:
    src = inspect.getsource(_load_migration().upgrade)
    for col in ("tenant_id", "request_id", "retention_class", "sensitivity"):
        assert col in src
