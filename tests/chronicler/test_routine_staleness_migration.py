"""Unit coverage for chronicler_022 routine-staleness migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "roster" / "chronicler" / "migrations"
)
_MIGRATION_FILE = "022_routine_staleness.py"


def test_routine_staleness_migration_chains_from_current_head() -> None:
    migration = load_migration(_MIGRATIONS_DIR / _MIGRATION_FILE, "chronicler_022")

    assert migration.revision == "chronicler_022"
    assert migration.down_revision == "chronicler_021"
    assert migration.branch_labels is None
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_routine_staleness_migration_is_discoverable_after_021() -> None:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("chronicler")
    assert chain_dir is not None
    filenames = sorted(path.name for path in chain_dir.glob("[0-9]*.py"))
    assert _MIGRATION_FILE in filenames
    assert filenames.index(_MIGRATION_FILE) > next(
        index for index, filename in enumerate(filenames) if filename.startswith("021_")
    )
