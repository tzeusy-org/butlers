"""``_restore_column_acls`` re-grants dropped column privileges faithfully (bu-n0tiyf)."""

from __future__ import annotations

import pytest

from butlers.db_authority import apply as apply_mod
from butlers.db_authority.apply import ColumnAcl, _restore_column_acls

pytestmark = pytest.mark.unit


class _Preparer:
    def quote(self, name: str) -> str:
        return f'"{name}"'


class _Dialect:
    identifier_preparer = _Preparer()


class _Result:
    def scalar_one(self) -> str:
        return "migrator"


class _Conn:
    dialect = _Dialect()

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement):  # noqa: ANN001
        sql = str(statement)
        self.statements.append(sql)
        return _Result()


def _acl(column: str = "pruned_at", **overrides) -> ColumnAcl:
    fields = {
        "relation": "qa_exports",
        "column": column,
        "role": "butler_qa_rw",
        "privilege": "UPDATE",
        "grantable": False,
        "grantor": "migrator",
    } | overrides
    return ColumnAcl(**fields)


def _restore(monkeypatch: pytest.MonkeyPatch, before, after=frozenset()) -> list[str]:
    monkeypatch.setattr(apply_mod, "column_acls", lambda conn: frozenset(after))
    conn = _Conn()
    _restore_column_acls(conn, frozenset(before))
    return conn.statements


def test_nothing_dropped_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _restore(monkeypatch, {_acl()}, after={_acl()}) == []


def test_restores_dropped_columns_grouped_per_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _restore(monkeypatch, {_acl("b"), _acl("a")})
    assert statements[-1] == (
        'GRANT UPDATE ("a", "b") ON TABLE public."qa_exports" TO "butler_qa_rw"'
    )
    assert len(statements) == 2  # the current_user probe plus one grouped GRANT


def test_keeps_the_grant_option(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _restore(monkeypatch, {_acl("a", grantable=True), _acl("b")})
    grants = [s for s in statements if s.startswith("GRANT")]
    assert len(grants) == 2
    assert sum(s.endswith("WITH GRANT OPTION") for s in grants) == 1
    assert next(s for s in grants if s.endswith("WITH GRANT OPTION")).count('"a"') == 1


def test_restores_under_the_original_grantor(monkeypatch: pytest.MonkeyPatch) -> None:
    statements = _restore(monkeypatch, {_acl(grantor="schema_owner")})
    assert statements[-3:] == [
        'SET LOCAL ROLE "schema_owner"',
        'GRANT UPDATE ("pruned_at") ON TABLE public."qa_exports" TO "butler_qa_rw"',
        "RESET ROLE",
    ]


def test_own_grants_do_not_switch_role(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not any("ROLE" in s for s in _restore(monkeypatch, {_acl()}))
