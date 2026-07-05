"""Unit tests for the chronicler daily_rollups migration (chronicler_019, bu-u30as).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_018 is intact.
- Migration file is ordered after 018_* in the migrations directory.

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
_MIGRATION_FILE = "019_daily_rollups.py"
_EXPECTED_REVISION = "chronicler_019"
_EXPECTED_DOWN_REVISION = "chronicler_018"


def _load_migration():
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_links_onto_018() -> None:
    """chronicler_019 chains directly onto chronicler_018 (revision-chain integrity),
    declares no new branch_labels, and exposes callable upgrade()/downgrade()."""
    m = _load_migration()
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_migration_ordered_after_018() -> None:
    """019_daily_rollups must sort after 018_routines in the directory."""
    files = sorted(f.name for f in _MIGRATIONS_DIR.glob("[0-9]*.py"))
    file_names = [f for f in files if not f.startswith("_")]
    idx_018 = next((i for i, f in enumerate(file_names) if f.startswith("018_")), None)
    idx_019 = next((i for i, f in enumerate(file_names) if f.startswith("019_")), None)
    assert idx_018 is not None, "018_* migration not found"
    assert idx_019 is not None, "019_* migration not found"
    assert idx_019 > idx_018, "019_daily_rollups must sort after 018_routines"


def test_chronicler_chain_includes_019() -> None:
    """Ensure the migration chain discovery picks up 019_daily_rollups."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None, "Chronicler chain directory not found"
    files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"


def test_lanes_check_constraint_matches_aggregations_lanes() -> None:
    """The migration's hardcoded lane CHECK list must match aggregations.LANES
    exactly — a drift here would let the rollup writer silently reject (or
    under-constrain) a lane the live endpoint recognizes."""
    from butlers.chronicler.aggregations import LANES

    m = _load_migration()
    assert set(m._LANES) == set(LANES)
