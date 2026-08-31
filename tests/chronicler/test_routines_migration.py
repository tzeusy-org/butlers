"""Unit tests for the chronicler routines migration (chronicler_018, bu-whhll.9).

Covers:
- Revision metadata is correct (revision ID, down_revision, branch_labels).
- upgrade() and downgrade() are callable.
- Chain link to chronicler_017 is intact.

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
