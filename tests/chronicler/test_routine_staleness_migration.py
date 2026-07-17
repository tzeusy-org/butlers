"""Unit coverage for chronicler_022 routine-staleness migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "roster" / "chronicler" / "migrations"
)
_MIGRATION_FILE = "022_routine_staleness.py"


def _load_migration():
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_routine_staleness_migration_chains_from_current_head() -> None:
    migration = _load_migration()

    assert migration.revision == "chronicler_022"
    assert migration.down_revision == "chronicler_021"
    assert migration.branch_labels is None
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_routine_staleness_migration_is_discoverable_after_021() -> None:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None
    filenames = sorted(path.name for path in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in filenames
    assert filenames.index(_MIGRATION_FILE) > next(
        index for index, filename in enumerate(filenames) if filename.startswith("021_")
    )
