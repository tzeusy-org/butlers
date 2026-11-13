"""core_332: the subject-members mirror's chain position, grants, guards and refusing downgrade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from butlers.testing.migration import load_migration

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_332_owner_delivery_subject_members.py"
)


def _module():
    return load_migration(_PATH, "core_332")


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


def test_core_332_extends_core_331() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == ("core_332", "core_331")


def test_upgrade_starts_from_nothing_then_grants_only_switchboard_select_and_insert() -> None:
    sql = "\n".join(_run("upgrade", 1))
    assert "REVOKE ALL PRIVILEGES ON TABLE public.owner_delivery_subject_members FROM PUBLIC" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.owner_delivery_subject_members" in sql
    assert "TO butler_switchboard_rw" in sql
    assert "UPDATE ON" not in sql and "DELETE ON" not in sql and "ALL ON" not in sql


def test_upgrade_enables_the_own_schema_policy_and_the_append_only_guards() -> None:
    sql = "\n".join(_run("upgrade", 1))
    assert "ENABLE ROW LEVEL SECURITY" in sql and "CREATE POLICY" in sql
    assert "BEFORE UPDATE OR DELETE" in sql and "BEFORE TRUNCATE" in sql
    assert sql.count("FOR EACH STATEMENT") == 2


def test_the_held_function_is_pinned_and_executable_by_runtime_roles_only() -> None:
    sql = "\n".join(_run("upgrade", 1))
    assert "SECURITY DEFINER SET search_path = pg_catalog, pg_temp" in sql
    assert "REVOKE ALL PRIVILEGES ON FUNCTION" in sql and "GRANT EXECUTE ON FUNCTION" in sql


def test_downgrade_refuses_while_members_exist_then_drops() -> None:
    sql = _run("downgrade", 1)
    refusal = next(s for s in sql if "RAISE EXCEPTION" in s)
    assert "EXISTS (SELECT 1 FROM public.owner_delivery_subject_members)" in refusal
    assert any("DROP TABLE" in s for s in sql)


def test_only_the_last_schema_drops_the_shared_objects() -> None:
    assert not any("DROP TABLE" in s for s in _run("downgrade", 2))
    assert any("DROP TABLE" in s for s in _run("downgrade", 1))
