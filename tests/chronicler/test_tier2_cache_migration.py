"""Unit tests for the chronicler tier2_cache migration (chronicler_004).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_003 is intact.

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
_MIGRATION_FILE = "004_tier2_cache.py"
_EXPECTED_REVISION = "chronicler_004"
_EXPECTED_DOWN_REVISION = "chronicler_003"


def test_revision_chain_links_onto_003() -> None:
    """chronicler_004 chains directly onto chronicler_003 (revision-chain integrity),
    declares no new branch_labels, and exposes callable upgrade()/downgrade()."""
    m = load_migration(_MIGRATIONS_DIR / _MIGRATION_FILE, "chronicler_004")
    assert m.revision == _EXPECTED_REVISION
    assert m.down_revision == _EXPECTED_DOWN_REVISION
    assert m.branch_labels is None
    assert callable(m.upgrade)
    assert callable(m.downgrade)
