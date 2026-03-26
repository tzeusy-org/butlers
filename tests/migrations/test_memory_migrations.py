"""Tests for current memory module migration chain (mem_001 -> mem_002)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MIGRATION_DIR = MODULES_DIR / "memory" / "migrations"
MIGRATION_FILE_001 = MIGRATION_DIR / "001_memory_schema.py"
MIGRATION_FILE_002 = MIGRATION_DIR / "002_seed_predicates.py"


def _load(filename: str):
    path = MIGRATION_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_files_exist() -> None:
    assert MIGRATION_FILE_001.exists()
    assert MIGRATION_FILE_002.exists()


def test_revision_chain() -> None:
    m1 = _load("001_memory_schema.py")
    m2 = _load("002_seed_predicates.py")
    assert m1.revision == "mem_001"
    assert m1.down_revision is None
    assert m2.revision == "mem_002"
    assert m2.down_revision == "mem_001"


def test_mem001_creates_core_tables() -> None:
    src = inspect.getsource(_load("001_memory_schema.py").upgrade)
    for table in (
        "episodes",
        "facts",
        "rules",
        "memory_links",
        "memory_events",
        "predicate_registry",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src


def test_mem002_seeds_predicates_idempotently() -> None:
    mod = _load("002_seed_predicates.py")
    src = inspect.getsource(mod.upgrade) + "\n" + inspect.getsource(mod._insert_predicate)
    assert "INSERT INTO predicate_registry" in src
    assert "ON CONFLICT (name) DO NOTHING" in src
