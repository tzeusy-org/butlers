"""`butlers public-mcp disable|enable <domain|all>` (REQ-dashboard-settings-console-001, D16).

Argument handling and the credentials the command uses, against a fake engine. The real table
write runs against PG17 in ``tests/integration/test_public_mcp_controls.py``.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from butlers.cli import cli
from butlers.public_mcp_controls import CONTROL_DOMAINS

PARAMS = {
    "host": "db.invalid",
    "port": 5432,
    "user": "migration",
    "password": "synthetic-pw",
    "ssl": None,
}


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Engine:
    def __init__(self, urls, calls, *, write_row, fail=False):
        self.calls = calls
        self.write_row = write_row
        self.fail = fail
        urls.append(self)
        self.disposed = False

    @contextmanager
    def begin(self):
        if self.fail:
            raise OperationalError("select secret statement", {"password": "x"}, Exception("boom"))
        yield self

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, dict(params)))
        if sql.lstrip().startswith("INSERT"):
            return _Result(self.write_row)
        return _Result({"state": params.get("state", "disabled")})

    def dispose(self):
        self.disposed = True


@pytest.fixture
def engines(monkeypatch):
    seen: list[_Engine] = []
    calls: list[tuple[str, dict]] = []
    holder = {"write_row": {"state": "x"}, "fail": False, "url": []}

    def create(url, **kwargs):
        holder["url"].append(url)
        return _Engine(seen, calls, write_row=holder["write_row"], fail=holder["fail"])

    monkeypatch.setattr("butlers.db.db_params_from_env", lambda: PARAMS)
    monkeypatch.setattr("sqlalchemy.create_engine", create)
    return holder, calls, seen


def test_disable_writes_one_row_as_the_cli_actor_on_the_migration_login(engines):
    holder, calls, seen = engines
    result = CliRunner().invoke(cli, ["public-mcp", "disable", "finance"])
    assert result.exit_code == 0, result.output
    assert holder["url"] == ["postgresql://migration:synthetic-pw@db.invalid:5432/butlers"]
    assert len(calls) == 1
    sql, params = calls[0]
    assert "mcp_surface.controls" in sql
    assert params == {"domain": "finance", "state": "disabled", "actor": "owner:cli"}
    assert "finance: disabled" in result.output
    assert seen[0].disposed


def test_enable_sets_enabled(engines):
    _, calls, _ = engines
    result = CliRunner().invoke(cli, ["public-mcp", "enable", "all", "--database", "primary_test"])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["state"] == "enabled" and calls[0][1]["domain"] == "all"
    assert "all: enabled" in result.output


def test_an_unchanged_switch_is_reported_as_unchanged(engines):
    holder, calls, _ = engines
    holder["write_row"] = None
    result = CliRunner().invoke(cli, ["public-mcp", "disable", "health"])
    assert result.exit_code == 0
    assert "already disabled" in result.output
    assert len(calls) == 2


def test_the_output_never_claims_immediate_effect_and_names_the_break_glass(engines):
    result = CliRunner().invoke(cli, ["public-mcp", "disable", "time"])
    assert "within 15 minutes" in result.output
    assert "butlers-public-mcp-switches" in result.output and "disabled_domains" in result.output
    assert "scale" in result.output
    assert "immediately" not in result.output.replace("immediate effect", "")


@pytest.mark.parametrize("command", ["disable", "enable"])
@pytest.mark.parametrize("domain", ["nonsense", "ALL", "finance,health", ""])
def test_an_unknown_domain_is_refused_before_any_connection(command, domain, monkeypatch):
    def no_db(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr("butlers.db.db_params_from_env", no_db)
    monkeypatch.setattr("sqlalchemy.create_engine", no_db)
    result = CliRunner().invoke(cli, ["public-mcp", command, domain])
    assert result.exit_code == 2


def test_a_domain_is_required(engines):
    assert CliRunner().invoke(cli, ["public-mcp", "disable"]).exit_code == 2


def test_every_control_domain_is_accepted(engines):
    for domain in CONTROL_DOMAINS:
        assert CliRunner().invoke(cli, ["public-mcp", "disable", domain]).exit_code == 0


def test_a_database_failure_is_closed_and_leaks_nothing(engines):
    holder, _, _ = engines
    holder["fail"] = True
    result = CliRunner().invoke(cli, ["public-mcp", "disable", "finance"])
    assert result.exit_code == 1
    assert "refused" in result.output
    assert "secret" not in result.output and "synthetic-pw" not in result.output
