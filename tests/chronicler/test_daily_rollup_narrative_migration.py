"""Unit tests for the chronicler daily-rollup-narrative migration
(chronicler_020, bu-v9y18, telemetry-distillation bead 6).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_019 is intact.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "roster" / "chronicler" / "migrations"
)
_MIGRATION_FILE = "020_daily_rollup_narrative.py"
_EXPECTED_REVISION = "chronicler_020"
_EXPECTED_DOWN_REVISION = "chronicler_019"


def test_revision_chain_links_onto_019() -> None:
    """chronicler_020 chains directly onto chronicler_019 (revision-chain
    integrity), declares no new branch_labels, and exposes callable
    upgrade()/downgrade()."""
    m = load_migration(_MIGRATIONS_DIR / _MIGRATION_FILE, "chronicler_020")
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def test_upgrade_adds_narrative_to_both_tables() -> None:
    """upgrade() must ALTER both daily_rollups and daily_rollup_flags,
    additive/nullable (no NOT NULL, no default value requiring backfill)."""
    m = load_migration(_MIGRATIONS_DIR / _MIGRATION_FILE, "chronicler_020")

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
    m = load_migration(_MIGRATIONS_DIR / _MIGRATION_FILE, "chronicler_020")

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
