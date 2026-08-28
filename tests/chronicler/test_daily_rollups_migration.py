"""Unit tests for the chronicler daily_rollups migration (chronicler_019, bu-u30as).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_018 is intact.

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


def test_chronicler_chain_includes_019() -> None:
    """Ensure the migration chain discovery picks up 019_daily_rollups."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None, "Chronicler chain directory not found"
    files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"


def test_lanes_check_constraint_matches_aggregations_lanes() -> None:
    """The CURRENT lane CHECK list must match aggregations.LANES exactly — a
    drift here would let the rollup writer silently reject (or under-constrain) a
    lane the live endpoint recognizes. chronicler_019 created the CHECK; the
    active definer is now chronicler_021 (bu-whhll.14 added the butler_ops lane),
    so this compares against 021's widened list."""
    import importlib.util

    from butlers.chronicler.aggregations import LANES

    path = _MIGRATIONS_DIR / "021_daily_rollup_butler_ops_lane.py"
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_021", path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert set(m._LANES) == set(LANES)
