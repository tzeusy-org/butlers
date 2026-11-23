"""approvals_023 and the model carry the broker-stamped exposure cause (bu-jv1moa.26)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.core.goal_labels import EXPOSURE_CAUSES
from butlers.modules.approvals.models import PendingAction

_MIGRATION = "butlers.modules.approvals.migrations.023_goal_exposure_cause"


@pytest.mark.unit
def test_approvals_023_follows_the_cancelled_reason_chain():
    migration = importlib.import_module(_MIGRATION)
    assert (migration.revision, migration.down_revision) == ("approvals_023", "approvals_022")


@pytest.mark.unit
def test_the_check_admits_exactly_the_closed_s1_causes(monkeypatch):
    migration = importlib.import_module(_MIGRATION)
    statements: list[str] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
    migration.upgrade()
    [sql] = statements
    assert migration._CAUSES == ", ".join(f"'{c}'" for c in EXPOSURE_CAUSES)
    for cause in EXPOSURE_CAUSES:
        assert f"'{cause}'" in sql
    assert "goal_exposed IS TRUE" in sql  # a cause never rides an unexposed lineage


@pytest.mark.unit
def test_the_model_round_trips_the_cause_and_a_legacy_row_reads_unknown():
    base = {"goal_id": str(uuid4()), "goal_invocation_id": str(uuid4()), "goal_depth": 0}
    row = {"goal_exposed": True, "goal_origin": "third_party", "goal_exposure_cause": "ingress"}
    action = PendingAction.from_row(_pending(**base, **row))
    assert action.to_dict()["goal"]["exposure_cause"] == "ingress"
    assert action.to_dict()["goal"]["rule_possible"] is None  # nothing stamped: unknown
    legacy = PendingAction.from_row(_pending(**base, goal_exposed=True, goal_origin="owner"))
    assert legacy.goal_exposure_cause is None


def _pending(**extra):
    return {
        "id": str(uuid4()),
        "tool_name": "notify",
        "tool_args": {},
        "status": "pending",
        "requested_at": "2026-11-13T00:00:00+00:00",
        **extra,
    }
