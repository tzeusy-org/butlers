"""Unit tests for relationship migration metadata correctness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _relationship_migration_dir() -> Path:
    """Return the relationship migration chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("relationship")
    assert chain_dir is not None, "Relationship chain should exist"
    return chain_dir


def _load_migration(filename: str):
    """Load a migration module by filename from the relationship chain."""
    migration_path = _relationship_migration_dir() / filename
    assert migration_path.exists(), f"Missing migration file: {filename}"

    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("filename", "expected_revision"),
    [
        ("rel_004_notes_rework.py", "rel_004"),
        ("rel_005_reminders_rework.py", "rel_005"),
    ],
)
def test_rel_004_and_rel_005_have_no_branch_labels(filename: str, expected_revision: str) -> None:
    """Non-root relationship migrations must not redeclare branch labels."""
    module = _load_migration(filename)
    assert module.revision == expected_revision
    assert module.branch_labels is None, f"{filename} should have branch_labels=None"


def test_only_rel_001_declares_relationship_branch_label() -> None:
    """Only the branch root should have branch_labels=('relationship',)."""
    migration_dir = _relationship_migration_dir()
    migration_files = sorted(p for p in migration_dir.glob("*.py") if p.name != "__init__.py")

    for migration_file in migration_files:
        module = _load_migration(migration_file.name)
        if module.revision == "rel_001":
            assert module.branch_labels == ("relationship",), (
                f"{migration_file.name} should have branch_labels=('relationship',)"
            )
        else:
            assert module.branch_labels is None, (
                f"{migration_file.name} should have branch_labels=None"
            )
