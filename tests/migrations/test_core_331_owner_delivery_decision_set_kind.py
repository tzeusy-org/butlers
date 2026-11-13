"""core_331: the subject-kind widening's chain position, shape and guarded last-schema downgrade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from butlers.testing.migration import load_migration

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_331_owner_delivery_decision_set_kind.py"
)


def _module():
    return load_migration(_PATH, "core_331")


def _run(fn: str, installed: int) -> list[str]:
    module = _module()
    statements: list[str] = []
    op = MagicMock()
    op.execute.side_effect = statements.append
    bind = op.get_bind.return_value

    def answer(sql: str, *args: object) -> MagicMock:
        result = MagicMock()
        if "pg_class version_table" in sql:
            result.scalars.return_value = iter([f"s{i}" for i in range(installed)])
        else:
            result.scalar.return_value = True
        return result

    bind.exec_driver_sql.side_effect = answer
    bind.dialect.identifier_preparer.quote.side_effect = lambda name: name
    with patch.object(module, "op", op):
        getattr(module, fn)()
    return statements


def test_core_331_extends_core_330() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == ("core_331", "core_330")


def test_upgrade_widens_only_the_one_named_kind_check_and_adds_no_table() -> None:
    sql = "\n".join(_run("upgrade", 1))
    assert "DROP CONSTRAINT IF EXISTS owner_delivery_subjects_kind_check" in sql
    assert "'approval', 'goal_answer', 'pending_ask', 'decision_set'" in sql
    assert "CREATE TABLE" not in sql and "GRANT" not in sql


def test_downgrade_refuses_while_decision_set_subjects_exist_then_narrows() -> None:
    sql = _run("downgrade", 1)
    refusal = next(s for s in sql if "RAISE EXCEPTION" in s)
    assert "kind = 'decision_set'" in refusal
    assert "'approval', 'goal_answer', 'pending_ask')" in sql[-1]
    assert "decision_set" not in sql[-1]


def test_only_the_last_schema_narrows_the_shared_constraint() -> None:
    assert not any("DROP CONSTRAINT" in s for s in _run("downgrade", 2))
    assert any("DROP CONSTRAINT" in s for s in _run("downgrade", 1))
