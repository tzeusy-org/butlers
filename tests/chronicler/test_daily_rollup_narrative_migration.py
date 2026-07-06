"""Unit tests for the chronicler daily-rollup-narrative migration
(chronicler_020, bu-v9y18, telemetry-distillation bead 6).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_019 is intact.
- Migration file is ordered after 019_* in the migrations directory.

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
_MIGRATION_FILE = "020_daily_rollup_narrative.py"
_EXPECTED_REVISION = "chronicler_020"
_EXPECTED_DOWN_REVISION = "chronicler_019"


def _load_migration():
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("chronicler_020", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_links_onto_019() -> None:
    """chronicler_020 chains directly onto chronicler_019 (revision-chain
    integrity), declares no new branch_labels, and exposes callable
    upgrade()/downgrade()."""
    m = _load_migration()
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_migration_ordered_after_019() -> None:
    """020_daily_rollup_narrative must sort after 019_daily_rollups in the directory."""
    files = sorted(f.name for f in _MIGRATIONS_DIR.glob("[0-9]*.py"))
    file_names = [f for f in files if not f.startswith("_")]
    idx_019 = next((i for i, f in enumerate(file_names) if f.startswith("019_")), None)
    idx_020 = next((i for i, f in enumerate(file_names) if f.startswith("020_")), None)
    assert idx_019 is not None, "019_* migration not found"
    assert idx_020 is not None, "020_* migration not found"
    assert idx_020 > idx_019, "020_daily_rollup_narrative must sort after 019_daily_rollups"


def test_chronicler_chain_includes_020() -> None:
    """Ensure the migration chain discovery picks up 020_daily_rollup_narrative."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None, "Chronicler chain directory not found"
    files = sorted(f.name for f in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in files, f"{_MIGRATION_FILE} not in discovered chronicler chain"


def test_upgrade_adds_narrative_to_both_tables() -> None:
    """upgrade() must ALTER both daily_rollups and daily_rollup_flags,
    additive/nullable (no NOT NULL, no default value requiring backfill)."""
    m = _load_migration()

    executed: list[str] = []

    class _FakeOp:
        def execute(self, sql: str) -> None:
            executed.append(sql)

    original_op = m.op
    try:
        m.op = _FakeOp()
        m.upgrade()
    finally:
        m.op = original_op

    assert any(
        "daily_rollups" in sql and "ADD COLUMN IF NOT EXISTS narrative" in sql for sql in executed
    )
    assert any(
        "daily_rollup_flags" in sql and "ADD COLUMN IF NOT EXISTS narrative" in sql
        for sql in executed
    )
    assert not any("NOT NULL" in sql for sql in executed), (
        "narrative columns must be nullable — no backfill required"
    )


def test_downgrade_drops_narrative_from_both_tables() -> None:
    m = _load_migration()

    executed: list[str] = []

    class _FakeOp:
        def execute(self, sql: str) -> None:
            executed.append(sql)

    original_op = m.op
    try:
        m.op = _FakeOp()
        m.downgrade()
    finally:
        m.op = original_op

    assert any(
        "daily_rollups" in sql and "DROP COLUMN IF EXISTS narrative" in sql for sql in executed
    )
    assert any(
        "daily_rollup_flags" in sql and "DROP COLUMN IF EXISTS narrative" in sql for sql in executed
    )
