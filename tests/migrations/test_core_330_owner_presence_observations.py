"""core_330: the presence feed's chain position, definer shape and last-schema downgrade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from butlers.testing.migration import load_migration

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_330_owner_presence_observations.py"
)


def _module():
    return load_migration(_PATH, "core_330")


def test_core_330_extends_core_329() -> None:
    module = _module()
    assert (module.revision, module.down_revision) == ("core_330", "core_329")


def test_the_function_is_a_pinned_schema_qualified_definer_with_no_caller_check() -> None:
    sql = _module()._FUNCTION
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    # Inside a definer function current_user is the definer, so a caller check would refuse all.
    assert "current_user" not in sql and "session_user" not in sql
    for relation in ("connectors.owntracks_points", "public.owner_places"):
        assert relation in sql
    returned = sql.split("RETURNS TABLE (")[1].split(")")[0]
    assert [c.split()[0] for c in returned.split(",")] == [
        "observed_at",
        "accuracy_m",
        "place_ref",
        "side",
        "ssid_match",
    ]


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


def test_upgrade_replaces_revokes_public_and_grants_home_only() -> None:
    statements = _run("upgrade", 1)
    assert any(
        "CREATE OR REPLACE FUNCTION public.owner_presence_observations" in s for s in statements
    )
    assert any("REVOKE ALL ON FUNCTION" in s and "FROM PUBLIC" in s for s in statements)
    grants = [s for s in statements if "GRANT EXECUTE" in s]
    assert len(grants) == 1 and "butler_home_rw" in grants[0]


def test_only_the_last_schema_drops_the_shared_function() -> None:
    assert not any("DROP FUNCTION" in s for s in _run("downgrade", 2))
    assert any("DROP FUNCTION" in s for s in _run("downgrade", 1))
