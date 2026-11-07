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
count, bounded failed-notification count + source availability, an optional
historical audit group, and QA state). This test feeds that SAME fixture through the real backend composition pipeline
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
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api.audit_grouping import build_audit_group_query
from butlers.api.briefing.classify import classify
from butlers.api.routers.dashboard_briefing import _fetch_dashboard_state, _map_board_rows
from butlers.testing.fake_pool import fake_pool
from tests.audit_helpers import clear_audit_log_for_test

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


async def _classify_scenario(scenario: dict, pool: Any) -> str:
    """Run one scenario through the real backend composition pipeline.

    *pool* answers the two pool-backed sources: grouped audit errors and
    ambient-projection liveness.
    """
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
        # No live audit groups or projections; the historical group's exclusion
        # by the 12-hour window is pinned on a real DB below.
        state_class = await _classify_scenario(scenario, fake_pool(fetch=[]))
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


_HISTORICAL = next(s for s in _SCENARIOS if s.get("historical_audit_group"))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_historical_audit_group_stays_out_of_the_live_window(db) -> None:
    """The shared historical-audit scenario holds when its raw audit rows really exist.

    The group's rows are older than the briefing's 12-hour window. A regression
    back to a wider or all-time window would surface them as attention items
    and change the scenario's state_class.
    """
    pool = await db("core")
    await clear_audit_log_for_test(pool)
    group = _HISTORICAL["historical_audit_group"]
    for seen_at in (group["first_seen_at"], group["last_seen_at"]):
        await pool.execute(
            "INSERT INTO public.audit_log (actor, action, result, error, ts)"
            " VALUES ($1, 'session', 'error', $2, $3)",
            group["butlers"][0],
            group["error_summary"],
            datetime.fromisoformat(seen_at),
        )
    # The seed is a real audit group: an unwindowed read groups it.
    assert [r["error_summary"] for r in await pool.fetch(build_audit_group_query())] == [
        group["error_summary"]
    ]

    state_class = await _classify_scenario(_HISTORICAL, pool)

    assert state_class == _HISTORICAL["expect"]["backend_state_class"]
