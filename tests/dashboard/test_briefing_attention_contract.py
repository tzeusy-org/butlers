"""Cross-surface consistency test (bu-gcz9e.2): headline state_class implies
attention-row-count/severity bounds, pinned from SHARED fixtures.

The epic's core complaint (bu-gcz9e): the briefing headline and the Overview
page's attention list were computed from disjoint definitions of "needs you"
-- the headline could say "busy" while the attention list rendered
"Nothing waiting.", or vice versa. bu-gcz9e.1 rewrote the headline to
classify from the same board/approvals/notifications/QA sources the
Overview page renders; bu-gcz9e.3 severity-sorted the attention list and
added the QA-breaker/notifications-degraded rows. This test pins the
resulting cross-surface contract so a future change to either side that lets
them drift apart fails a test, not a support ticket.

Fixture format: each named scenario in
``frontend/src/components/overview/__fixtures__/attention-contract-scenarios.json``
describes ONE raw dashboard-state fixture (board rows, approvals pending
count, failed-notification count + source availability, QA state). This test
feeds that SAME fixture through the real backend composition pipeline
(``_map_board_rows`` for board rows, then ``_fetch_dashboard_state`` for the
full five-source composition, then ``classify``) and asserts
``state_class == expect.backend_state_class``.

The companion frontend test,
``frontend/src/components/overview/model.contract.test.ts``, feeds the exact
same named scenarios through ``deriveOverviewTriageModel`` and asserts the
resulting ``attentionRows`` satisfy the SAME scenario's row-count/severity
bounds. Two independent runtimes, one shared fixture file, one contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api.briefing.classify import classify
from butlers.api.routers.dashboard_briefing import _fetch_dashboard_state, _map_board_rows

pytestmark = pytest.mark.unit

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "overview"
    / "__fixtures__"
    / "attention-contract-scenarios.json"
)


def _load_scenarios() -> list[dict]:
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data["scenarios"]


_SCENARIOS = _load_scenarios()


def _board_row(name: str, activity: str) -> SimpleNamespace:
    """A minimal stand-in for a BoardRow -- only the fields _map_board_rows reads.

    Mirrors test_briefing.py::_board_row (kept local here so this contract
    file has no import-order dependency on another test module's private
    helpers).
    """
    return SimpleNamespace(
        name=name,
        type="butler",
        activity=activity,
        eligibility="active",
        last_heartbeat_at="2026-05-13T15:59:00+00:00",
        quarantine_reason=None,
    )


def _make_pool() -> AsyncMock:
    """A switchboard pool that answers the owner-assertion/audit queries
    with empty results -- this contract intentionally excludes the
    audit-derived source (already covered by TestAuditDerivedAttentionItems
    in test_briefing.py); every scenario here supplies zero audit rows.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


async def _classify_scenario(scenario: dict) -> str:
    """Run one scenario through the real backend composition pipeline."""
    rows = [_board_row(r["name"], r["activity"]) for r in scenario["board_rows"]]

    if scenario["board_source_error"]:
        board = ([], [], True)
    else:
        attention_items, butler_statuses = _map_board_rows(rows, registry_source_error=False)
        board = (attention_items, butler_statuses, False)

    approvals = (scenario["approvals_pending"], False)
    notifications = (
        scenario["failed_notifications"],
        not scenario["notifications_source_available"],
    )
    qa = (scenario["qa"], False)

    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    pool = _make_pool()

    with (
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_board_state",
            new=AsyncMock(return_value=board),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_approvals_state",
            new=AsyncMock(return_value=approvals),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_notifications_state",
            new=AsyncMock(return_value=notifications),
        ),
        patch(
            "butlers.api.routers.dashboard_briefing._fetch_qa_state",
            new=AsyncMock(return_value=qa),
        ),
    ):
        state = await _fetch_dashboard_state(
            pool,
            now,
            db=MagicMock(),  # not touched: _fetch_board_state is patched above
            configs=[],
            mgr=MagicMock(),
            pricing=MagicMock(),
        )

    return classify(state)


class TestAttentionContractBackend:
    """Each scenario's fixture drives the real classification pipeline."""

    @pytest.mark.parametrize("scenario", _SCENARIOS, ids=[s["name"] for s in _SCENARIOS])
    async def test_scenario_classifies_as_expected(self, scenario: dict):
        state_class = await _classify_scenario(scenario)
        assert state_class == scenario["expect"]["backend_state_class"], (
            f"scenario {scenario['name']!r}: expected state_class "
            f"{scenario['expect']['backend_state_class']!r}, got {state_class!r}"
        )

    def test_fixture_file_is_nonempty(self):
        """A silently-empty fixture file would make every parametrized test
        above vacuously pass -- guard against that."""
        assert len(_SCENARIOS) >= 5

    def test_every_scenario_has_a_unique_name(self):
        names = [s["name"] for s in _SCENARIOS]
        assert len(names) == len(set(names))
