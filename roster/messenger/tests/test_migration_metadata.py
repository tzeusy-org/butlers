"""Unit tests for messenger migration metadata correctness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _messenger_migration_dir() -> Path:
    """Return the messenger migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("messenger")
    assert chain_dir is not None, "Messenger chain should exist"
    return chain_dir


def _load_migration(filename: str):
    """Load a migration module by filename from the messenger chain."""
    migration_path = _messenger_migration_dir() / filename
    assert migration_path.exists(), f"Missing migration file: {filename}"

    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_migration_files() -> list[Path]:
    """Return a sorted list of all messenger migration files."""
    migration_dir = _messenger_migration_dir()
    return sorted(p for p in migration_dir.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("migration_file", _get_migration_files(), ids=lambda p: p.name)
def test_migration_branch_label(migration_file: Path) -> None:
    """Only the branch root should have branch_labels=('messenger',)."""
    module = _load_migration(migration_file.name)
    if module.revision == "msg_001":
        assert module.branch_labels == ("messenger",), (
            f"{migration_file.name} should have branch_labels=('messenger',)"
        )
    else:
        assert module.branch_labels is None, f"{migration_file.name} should have branch_labels=None"


def test_msg_001_is_branch_root():
    """msg_001 must be the branch root with no down_revision."""
    module = _load_migration("msg_001_create_delivery_tables.py")
    assert module.revision == "msg_001", "First migration should have revision 'msg_001'"
    assert module.down_revision is None, "Branch root should have down_revision=None"
    assert module.branch_labels == ("messenger",), "Branch root should declare branch_labels"
    assert module.depends_on is None, "Branch root should have depends_on=None"


def test_migration_files_exist():
    """Verify expected migration files exist."""
    migration_dir = _messenger_migration_dir()
    expected_files = [
        "msg_001_create_delivery_tables.py",
    ]

    for filename in expected_files:
        assert (migration_dir / filename).exists(), f"Missing expected file: {filename}"
