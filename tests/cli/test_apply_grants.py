"""``butlers db apply-grants``: dry run is the default and changes nothing (bu-dss7he.6 S2b)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.db_authority.apply import GrantChange

pytestmark = pytest.mark.unit

_CHANGE = GrantChange("REVOKE", "ambient_projections", "butler_general_rw", ("DELETE",))


def _run(monkeypatch: pytest.MonkeyPatch, *args: str):
    applied = MagicMock(return_value=[_CHANGE])
    monkeypatch.setattr(
        "butlers.db.db_params_from_env",
        lambda: {"host": "h", "port": 5432, "user": "u", "password": "p"},
    )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda url: MagicMock())
    monkeypatch.setattr("butlers.db_authority.catalog.read_public_catalog", lambda conn: None)
    monkeypatch.setattr("butlers.db_authority.apply.plan_public_grants", lambda catalog: [_CHANGE])
    monkeypatch.setattr("butlers.db_authority.apply.apply_public_grants", applied)
    return CliRunner().invoke(cli, ["db", "apply-grants", *args]), applied


def test_default_is_a_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    result, applied = _run(monkeypatch)
    assert result.exit_code == 0, result.output
    applied.assert_not_called()
    assert "REVOKE DELETE ON ambient_projections butler_general_rw" in result.output
    assert "dry run" in result.output


def test_execute_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    result, applied = _run(monkeypatch, "--execute")
    assert result.exit_code == 0, result.output
    applied.assert_called_once()
    assert "1 grant change(s) applied" in result.output
