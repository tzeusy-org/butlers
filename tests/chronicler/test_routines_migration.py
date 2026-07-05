"""Unit tests for the chronicler routines migration (chronicler_018, bu-whhll.9).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_017 is intact.
- Migration file is ordered after 017_* in the migrations directory.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "roster" / "chronicler" / "migrations"
)
_MIGRATION_FILE = "018_routines.py"
_EXPECTED_REVISION = "chronicler_018"
_EXPECTED_DOWN_REVISION = "chronicler_017"


def _load_migration():
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_018", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_links_onto_017() -> None:
    """chronicler_018 chains directly onto chronicler_017 (revision-chain integrity),
    declares no new branch_labels, and exposes callable upgrade()/downgrade()."""
    m = _load_migration()
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_migration_ordered_after_017() -> None:
    """018_routines must sort after 017_iea_layer_confidence_evidence in the directory."""
    files = sorted(f.name for f in _MIGRATIONS_DIR.glob("[0-9]*.py"))
    file_names = [f for f in files if not f.startswith("_")]
    idx_017 = next((i for i, f in enumerate(file_names) if f.startswith("017_")), None)
    idx_018 = next((i for i, f in enumerate(file_names) if f.startswith("018_")), None)
    assert idx_017 is not None, "017_* migration not found"
    assert idx_018 is not None, "018_* migration not found"
    assert idx_018 > idx_017, "018_routines must sort after 017_iea_layer_confidence_evidence"


def test_chronicler_chain_includes_018() -> None:
    """Ensure the migration chain discovery picks up 018_routines."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None, "Chronicler chain directory not found"
    files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"
