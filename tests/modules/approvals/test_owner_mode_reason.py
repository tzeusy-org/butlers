"""approvals_024 admits ``owner_mode`` as a delivery retry reason (bu-dhloi1.22.1)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from butlers.core.approval_delivery_worker import SAFE_REASON_CODES

pytestmark = pytest.mark.unit

_MIGRATION = "butlers.modules.approvals.migrations.024_owner_mode_reason"


def _sql(monkeypatch: pytest.MonkeyPatch, direction: str) -> str:
    migration = importlib.import_module(_MIGRATION)
    statements: list[str] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
    getattr(migration, direction)()
    [sql] = statements
    return sql


def test_approvals_024_follows_the_goal_exposure_cause_chain() -> None:
    migration = importlib.import_module(_MIGRATION)
    assert (migration.revision, migration.down_revision) == ("approvals_024", "approvals_023")


def test_owner_mode_is_in_the_closed_vocabulary() -> None:
    assert "owner_mode" in SAFE_REASON_CODES


def test_upgrade_admits_the_code_in_both_checks_and_keeps_every_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _sql(monkeypatch, "upgrade")
    assert sql.count("'owner_mode'") == 2
    for code in SAFE_REASON_CODES - {"owner_mode"}:
        assert sql.count(f"'{code}'") == 2, code


def test_downgrade_refuses_while_a_row_carries_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "downgrade")
    assert "RAISE EXCEPTION" in sql and "last_reason_code = 'owner_mode'" in sql
    assert "reason_code = 'owner_mode'" in sql
    assert "'owner_mode'" in sql and sql.count("IN (") == 2
