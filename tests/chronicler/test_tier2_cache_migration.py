"""Unit tests for the chronicler tier2_cache migration (chronicler_004).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_003 is intact.
- Migration file is ordered after 003_* in the migrations directory.

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
_MIGRATION_FILE = "004_tier2_cache.py"
_EXPECTED_REVISION = "chronicler_004"
_EXPECTED_DOWN_REVISION = "chronicler_003"


def _load_migration():
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_004", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_links_onto_003() -> None:
    """chronicler_004 chains directly onto chronicler_003 (revision-chain integrity),
    declares no new branch_labels, and exposes callable upgrade()/downgrade()."""
    m = _load_migration()
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_migration_ordered_after_003() -> None:
    """004_tier2_cache must sort after 003_restrict_chronicler_grants in the directory."""
    files = sorted(f.name for f in _MIGRATIONS_DIR.glob("[0-9]*.py"))
    file_names = [f for f in files if not f.startswith("_")]
    idx_003 = next((i for i, f in enumerate(file_names) if f.startswith("003_")), None)
    idx_004 = next((i for i, f in enumerate(file_names) if f.startswith("004_")), None)
    assert idx_003 is not None, "003_* migration not found"
    assert idx_004 is not None, "004_* migration not found"
    assert idx_004 > idx_003, "004_tier2_cache must sort after 003_restrict_chronicler_grants"


def test_chronicler_chain_includes_004() -> None:
    """Ensure the migration chain discovery picks up 004_tier2_cache."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None, "Chronicler chain directory not found"
    files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"
