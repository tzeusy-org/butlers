"""Deployment waits for sustained semantic readiness (REQ-dashboard-api-064/065)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from butlers.core.deploy_readiness import (
    FAILURE_CATEGORIES,
    EvidenceSample,
    SustainedReadinessGate,
    SustainedReadinessPolicy,
    await_sustained_readiness,
    read_ready,
    resolve_target,
)
from butlers.core.qa.patrol_provenance import QaPatrolContract

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
POLICY = SustainedReadinessPolicy(patrol_interval_s=600.0, longest_ttl_s=300.0)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _sample(ready: bool | None = True, *, span=None, patrols=(), failing=()) -> EvidenceSample:
    return EvidenceSample(ready, tuple(failing), span, tuple(patrols))


def test_timeout_is_derived_from_cadence_and_shorter_overrides_are_refused() -> None:
    assert POLICY.effective_timeout_s == 35 * 60  # 2 x 10 min cadence + 5 min TTL + 10 min
    assert SustainedReadinessPolicy(600.0, 300.0, timeout_s=3600.0).effective_timeout_s == 3600
    with pytest.raises(ValueError, match="below the derived minimum"):
        SustainedReadinessPolicy(600.0, 300.0, timeout_s=34 * 60)
    with pytest.raises(ValueError, match="below the derived minimum"):
        SustainedReadinessPolicy(1200.0, 300.0, timeout_s=35 * 60)  # slower QA cadence


def test_target_derives_from_roster_and_refuses_missing_qa_contract() -> None:
    contract = QaPatrolContract(("infra_state",), 10)
    roster = [
        SimpleNamespace(name="qa", liveness_ttl_seconds=300, qa_patrol_contract=contract),
        SimpleNamespace(name="health", liveness_ttl_seconds=420, qa_patrol_contract=None),
    ]
    target = resolve_target(roster)
    assert target.expected == ("health", "qa")
    assert target.qa_digest == contract.digest
    assert target.policy.longest_ttl_s == 420.0
    assert target.policy.effective_timeout_s == 2 * 600 + 420 + 600
    with pytest.raises(ValueError, match="QA patrol contract"):
        resolve_target(roster[1:])


def test_post_restart_green_window_and_rereads_of_one_cycle_cannot_complete() -> None:
    """Five minutes of green /ready that re-reads one observer cycle and one
    patrol (the post-restart false-green incident) stays pending, then fails."""
    gate = SustainedReadinessGate(POLICY, T0, started_at=0.0)
    span = (_at(10), _at(20))
    for now in range(0, 2101, 15):
        done = gate.observe(_sample(span=span, patrols=[("p1", _at(30))] * 2), float(now))
        assert done is False
    assert gate.observer_cycles == 1 and gate.qa_patrols == 1
    assert gate.expired(2100.0)
    receipt = gate.receipt(2100.0)
    assert receipt.passed is False
    assert receipt.rollback == "operator_review"
    assert receipt.failing_categories == ("observer_cycles", "qa_patrol_cycles")


def test_distinct_cycles_and_sustained_readiness_complete_with_a_bounded_receipt() -> None:
    gate = SustainedReadinessGate(POLICY, T0, started_at=0.0)
    # Pre-window observations/patrols and a same-time duplicate patrol never count.
    assert not gate.observe(_sample(span=(_at(-5), _at(9)), patrols=[("old", _at(-1))]), 0.0)
    assert not gate.observe(_sample(span=(_at(160), _at(170)), patrols=[("p1", _at(100))]), 200.0)
    # Second observer cycle advanced past the first cycle's newest observation;
    # second distinct patrol completed strictly later. TTL not yet exceeded.
    patrols = [("p1", _at(100)), ("p2", _at(100)), ("p3", _at(700))]
    assert not gate.observe(_sample(span=(_at(320), _at(330)), patrols=patrols), 300.0)
    assert gate.observer_cycles == 2 and gate.qa_patrols == 2
    assert gate.observe(_sample(span=(_at(320), _at(330)), patrols=patrols), 301.0)
    receipt = gate.receipt(301.0)
    assert receipt.passed and receipt.rollback == "not_required"
    assert receipt.failing_categories == ()
    payload = json.loads(receipt.to_json())
    assert set(payload) == {
        "result", "rollback", "failing_categories", "observer_cycles",
        "qa_patrols", "sustained_s", "elapsed_s", "timeout_s",
    }  # fmt: skip
    assert payload["result"] == "success"


def test_a_false_sample_restarts_the_sustained_window_and_reports_fixed_categories() -> None:
    gate = SustainedReadinessGate(POLICY, T0, started_at=0.0)
    gate.observe(_sample(span=(_at(1), _at(2)), patrols=[("p1", _at(10))]), 0.0)
    gate.observe(_sample(span=(_at(3), _at(4)), patrols=[("p2", _at(20))]), 100.0)
    gate.observe(_sample(False, failing=["fleet"]), 250.0)
    assert not gate.observe(_sample(span=(_at(5), _at(6))), 400.0)  # was 400s green before
    assert gate.observe(_sample(span=(_at(5), _at(6))), 701.0)
    assert gate.receipt(701.0).passed

    failing = SustainedReadinessGate(POLICY, T0, started_at=0.0)
    failing.observe(_sample(False, failing=["route_canary", "fleet"], span=None), 2100.0)
    receipt = failing.receipt(2100.0)
    assert receipt.passed is False and receipt.rollback == "recommended"
    assert set(receipt.failing_categories) <= set(FAILURE_CATEGORIES)
    assert receipt.failing_categories[:2] == ("fleet", "route_canary")


async def test_ready_body_is_reduced_to_fixed_check_names() -> None:
    bodies = {
        "/ok": (200, {"ready": True}),
        "/no": (503, {"ready": False, "checks": {"fleet": False, "qa_patrol": True,
                                                 "health@10.0.0.1": False}}),
        "/odd": (200, {"ready": False}),
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = bodies[request.url.path]
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await read_ready(client, "http://api/ok") == (True, ())
        ready, failing = await read_ready(client, "http://api/no")
        assert ready is False and "health@10.0.0.1" not in failing
        assert "fleet" in failing and "qa_patrol" not in failing
        assert await read_ready(client, "http://api/odd") == (None, ())


@pytest.mark.parametrize("stall", [None, "clock", "fleet_facts", "patrols"])
async def test_wait_times_out_without_success_when_evidence_never_advances(stall) -> None:
    """End-to-end loop: /ready stays green, nothing advances, the deadline fails it.

    A database read that never resolves (asyncpg pools carry no command timeout)
    is bounded and counts as unavailable evidence instead of hanging the wait.
    """

    async def never() -> None:
        await asyncio.Event().wait()

    class Pool:
        async def fetchval(self, _sql):
            if stall == "clock":
                await never()
            return T0

        async def fetch(self, _sql, *_args):
            if stall == "patrols":
                await never()
            return []

        def acquire(self):
            if stall == "fleet_facts":
                return StalledConnection()
            raise ConnectionError("role view unavailable")

    class StalledConnection:
        async def __aenter__(self):
            await never()

        async def __aexit__(self, *_exc):
            return False

    clock = SimpleNamespace(now=0.0)

    async def sleep(seconds: float) -> None:
        clock.now += seconds

    target = resolve_target(
        [SimpleNamespace(name="qa", liveness_ttl_seconds=300,
                         qa_patrol_contract=QaPatrolContract(("infra_state",), 10))],
        poll_interval_s=300.0,
    )  # fmt: skip
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"ready": True}))
    receipt = await asyncio.wait_for(
        await_sustained_readiness(
            target,
            Pool(),
            client_factory=lambda: httpx.AsyncClient(transport=transport),
            clock=lambda: clock.now,
            sleep=sleep,
            read_timeout_s=0.01,
        ),
        timeout=5.0,
    )
    assert receipt.passed is False
    assert receipt.timeout_s == 35 * 60 and receipt.elapsed_s >= receipt.timeout_s
    assert receipt.rollback == ("recommended" if stall == "clock" else "operator_review")
    assert "evidence_unavailable" in receipt.failing_categories
