"""core_359 (bu-opskk2): the shared breaker_resets DDL is serialized and runs once.

The core chain runs once per butler schema over the one shared ``public.breaker_resets`` table.
The downgrade used to DELETE refusal rows (a row lock) and then ALTER the table (ACCESS
EXCLUSIVE) in every schema with no advisory lock, so two concurrent chains deadlocked on the
table lock (merge_group run 45126468351). No Docker needed: the module's ``op`` is faked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_359_breaker_reset_cause.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("core_359_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _run(direction: str, *, applied: bool = False, installations: int = 1) -> list[str]:
    migration = _load()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    fake_op.get_bind.return_value.exec_driver_sql.return_value.scalar.return_value = applied
    with (
        patch.object(migration, "op", fake_op),
        patch.object(migration, "_installation_count", return_value=installations),
    ):
        getattr(migration, direction)()
    return statements


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_the_advisory_lock_is_the_first_statement(direction: str) -> None:
    statements = _run(direction)
    assert "pg_advisory_xact_lock" in statements[0]


def test_an_upgrade_that_finds_the_change_applied_issues_no_alter() -> None:
    statements = _run("upgrade", applied=True)
    assert len(statements) == 1
    assert not any("ALTER TABLE" in s for s in statements)


def test_the_first_upgrade_still_adds_the_columns() -> None:
    statements = _run("upgrade", applied=False)
    assert any("ADD COLUMN IF NOT EXISTS outcome" in s for s in statements)


def test_an_earlier_schemas_downgrade_touches_no_table() -> None:
    statements = _run("downgrade", installations=2)
    assert len(statements) == 1


def test_the_last_schemas_downgrade_deletes_refusals_then_drops() -> None:
    statements = _run("downgrade", installations=1)
    joined = "\n".join(statements)
    assert "DELETE FROM public.breaker_resets" in joined
    assert "DROP COLUMN IF EXISTS outcome" in joined
