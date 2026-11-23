"""``butlers db transfer-ownership``: dry run by default, refusals exit non-zero (bu-dss7he.6 S3)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.db_authority.ownership import Membership, TransferPlan

pytestmark = pytest.mark.unit

_PLAN = TransferPlan(
    source="butlers",
    target="butlers_migrator",
    owned=("table public.example",),
    memberships=(Membership("butler_general_rw", inherit=True, set=True, admin=False),),
    acl_not_copied=("schema general",),
    refusals=(),
)
_ARGS = ("db", "transfer-ownership", "--from-login", "butlers", "--to-login", "butlers_migrator")


def _run(monkeypatch: pytest.MonkeyPatch, *args: str, plan: TransferPlan = _PLAN):
    transfer = MagicMock(return_value=plan)
    monkeypatch.setattr(
        "butlers.db.db_params_from_env",
        lambda: {"host": "h", "port": 5432, "user": "u", "password": "p"},
    )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda url: MagicMock())
    monkeypatch.setattr(
        "butlers.db_authority.ownership.read_transfer_plan", lambda conn, s, t: plan
    )
    monkeypatch.setattr("butlers.db_authority.ownership.transfer_ownership", transfer)
    return CliRunner().invoke(cli, [*_ARGS, *args]), transfer


def test_default_is_a_dry_run_listing_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    result, transfer = _run(monkeypatch)
    assert result.exit_code == 0, result.output
    transfer.assert_not_called()
    assert "OWNED table public.example" in result.output
    assert "MEMBERSHIP butler_general_rw INHERIT TRUE, SET TRUE, ADMIN FALSE" in result.output
    assert "ACL-NOT-COPIED schema general" in result.output
    assert "dry run" in result.output


def test_execute_transfers(monkeypatch: pytest.MonkeyPatch) -> None:
    result, transfer = _run(monkeypatch, "--execute")
    assert result.exit_code == 0, result.output
    transfer.assert_called_once()
    assert transfer.call_args.args[1:] == ("butlers", "butlers_migrator")
    assert "1 owned object(s) transferred" in result.output


def test_refusals_exit_non_zero_and_never_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = TransferPlan(
        "butlers", "butlers", (), (), (), ("source and target are the same login",)
    )
    result, transfer = _run(monkeypatch, "--execute", plan=refused)
    assert result.exit_code != 0
    transfer.assert_not_called()
    assert "REFUSED source and target are the same login" in result.output
