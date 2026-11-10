"""The goal runner on a migrated database: the bu-u5s4w8 behavior matrix.

The broker, its approvals domain and the sw_047..sw_062 ledger are real (the
broker suite's ``world``). The session host is a fake that holds each launch
until it is terminated or told how the invocation ended; the model catalog's
collaborators are patched in the kernel (``butlers.core.model_call``), which
the runner calls for every launch.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.core.goal_admission import Grant
from butlers.core.goal_waiting import WaitLedger
from butlers.core.model_call import AttemptSink
from butlers.core.model_routing import CeilingStatus, QuotaStatus, SpendRoutingResult
from butlers.goal_runner.contracts import HostOutcome, Launch
from butlers.goal_runner.runner import GoalRunner, RunnerConfig
from butlers.goal_runner.staging import SessionHostBusy
from butlers.goal_runner.tools import GoalTools
from tests.capability_broker.test_broker_ledger import (
    NOTIFY,
    OWNER,
    PAY,
    RULES,
    SEARCH,
    SEND,
    TO_OWNER,
    world,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

SCHEMA = {
    "type": "object",
    "properties": {"found": {"type": "boolean"}},
    "required": ["found"],
    "additionalProperties": False,
}
_ALLOWED = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_EXHAUSTED = QuotaStatus(allowed=False, usage_24h=9, limit_24h=1, usage_30d=0, limit_30d=None)


def _row(name: str) -> tuple[str, str, list[str], UUID, int]:
    return ("codex", f"model-{name}", [], uuid.uuid5(uuid.NAMESPACE_DNS, name), 300)


TIERS = {"workhorse": _row("work-a"), "reasoning": _row("reason-a"), "cheap": _row("cheap-a")}
FALLBACKS = {"workhorse": [_row("work-b")], "cheap": [_row("cheap-b")], "reasoning": []}


def _retryable() -> Exception:
    return RuntimeError("Connection error.")


class Host:
    """Holds each launch until terminated or scripted; records everything."""

    def __init__(self) -> None:
        self.launches: list[Launch] = []
        self.terminated: list[UUID] = []
        self.script: list[Any] = []
        self._ends: dict[UUID, asyncio.Future] = {}

    async def launch(self, launch: Launch) -> HostOutcome:
        self.launches.append(launch)
        behavior = self.script.pop(0) if self.script else None
        if isinstance(behavior, HostOutcome):
            return behavior
        if callable(behavior):
            return await behavior(launch)
        end = self._ends.setdefault(
            launch.invocation_id, asyncio.get_running_loop().create_future()
        )
        return await end

    def end(self, invocation_id: UUID, outcome: HostOutcome | None = None) -> None:
        future = self._ends.setdefault(invocation_id, asyncio.get_running_loop().create_future())
        if not future.done():
            future.set_result(outcome or HostOutcome())

    async def terminate(self, invocation_id: UUID) -> None:
        self.terminated.append(invocation_id)
        self.end(invocation_id, HostOutcome(failure=RuntimeError("terminated")))


class Sink(AttemptSink):
    def __init__(self) -> None:
        super().__init__(MagicMock())
        self.outcomes: list[str] = []
        self.reasons: list[str | None] = []

    async def attempt(self, **fields: Any) -> None:
        self.outcomes.append(fields["outcome"])
        self.reasons.append(fields.get("failure_reason"))


@dataclass
class Harness:
    w: Any
    host: Host
    runner: GoalRunner
    tools: GoalTools
    sink: Sink
    quotas: dict[UUID, QuotaStatus] = field(default_factory=dict)

    async def goal(self, *grants: Grant, **kwargs) -> UUID:
        return await self.w.goal(*grants, **kwargs)

    async def launched(self, count: int) -> Launch:
        await until(lambda: len(self.host.launches) >= count)
        return self.host.launches[count - 1]

    async def lead(self, goal: UUID, count: int = 1) -> Launch:
        self.runner.start_goal(goal)
        return await self.launched(count)

    async def row(self, invocation_id: UUID):
        return await self.w.owner.fetchrow(
            "SELECT * FROM switchboard.invocations WHERE id = $1", invocation_id
        )

    async def goal_status(self, goal: UUID) -> str:
        return await self.w.owner.fetchval(
            "SELECT status FROM switchboard.goals WHERE id = $1", goal
        )

    async def budget(self, goal: UUID):
        return await self.w.owner.fetchrow(
            "SELECT * FROM switchboard.budgets WHERE goal_id = $1", goal
        )

    async def call(self, goal: UUID, token: str, name: str, args: dict) -> dict:
        return await self.tools.call(goal, token, name, args)

    async def delegate(self, goal: UUID, token: str, *caps: Any, **kwargs) -> UUID:
        result = await self.call(goal, token, "goal__delegate", delegation(*caps, **kwargs))
        return UUID(result["child_id"])


async def until(predicate: Callable[[], Any], within: float = 5) -> None:
    async with asyncio.timeout(within):
        while True:
            value = predicate()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return
            await asyncio.sleep(0.01)


def delegation(*caps: Any, tier="cheap", calls=3, tokens=100, wall=600, **extra) -> dict:
    return {
        "brief": {"objective": "Find the receipt", "result_schema": SCHEMA},
        "capabilities": [{"capability": c} if isinstance(c, str) else c for c in caps],
        "model_tier": tier,
        "budget_share": {"calls": calls, "tokens": tokens, "wall_seconds": wall},
        **extra,
    }


async def refused(awaitable) -> Reason:
    with pytest.raises(BrokerRefused) as error:
        await awaitable
    return error.value.reason


def _grant(cap, **kwargs) -> Grant:
    return Grant(cap.identity, **kwargs)


@asynccontextmanager
async def harness(url, *, tiers=None, ladders=None, config=None):
    tiers = TIERS if tiers is None else tiers
    quotas: dict[UUID, QuotaStatus] = {}

    async def resolve(pool, role, tier, **kwargs):
        if "receipt_sink" in kwargs:
            kwargs["receipt_sink"].append(SimpleNamespace(describe=lambda: {"candidates": []}))
        return None if tier not in tiers else (*tiers[tier], tier)

    async def following(pool, role, tier, attempted):
        return next((r for r in FALLBACKS.get(tier, []) if r[3] not in attempted), None)

    async def quota(pool, entry_id):
        return quotas.get(entry_id, _ALLOWED)

    stack = ExitStack()
    for target, mock in (
        (
            "butlers.core.model_call.resolve_model_with_effective_tier",
            AsyncMock(side_effect=resolve),
        ),
        (
            "butlers.goal_runner.models.resolve_model_with_effective_tier",
            AsyncMock(side_effect=resolve),
        ),
        ("butlers.core.model_call.next_same_tier_candidate", AsyncMock(side_effect=following)),
        ("butlers.core.model_call.check_token_quota", AsyncMock(side_effect=quota)),
        (
            "butlers.core.model_call.check_monthly_ceiling",
            AsyncMock(return_value=CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=10.0)),
        ),
        (
            "butlers.core.model_call.apply_spend_routing_rules",
            AsyncMock(side_effect=lambda pool, b, t, resolved, **kw: SpendRoutingResult(resolved)),
        ),
    ):
        stack.enter_context(patch(target, mock))
    with stack:
        async with world(url) as w:
            host, sink = Host(), Sink()
            runner = GoalRunner(
                w.broker,
                host,
                catalog_pool=MagicMock(),
                sink=sink,
                ladders=ladders,
                config=config or RunnerConfig(renew_every=3600, lead_max_tokens=500),
            )
            h = Harness(w, host, runner, GoalTools(w.broker, runner), sink, quotas)
            try:
                yield h
            finally:
                for goal in await w.owner.fetch("SELECT id FROM switchboard.goals"):
                    await runner.stop(goal["id"])
                async with asyncio.timeout(10):
                    await runner.drain()


# -- AC1: launches only through the runner; children never hold lead tools ----


async def test_a_lead_launches_through_the_runner_with_a_token_bound_to_its_row(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        launch = await h.lead(goal)
        row = await h.row(launch.invocation_id)
        assert (row["depth"], row["lead_epoch"], row["status"]) == (0, 0, "running")
        assert (row["model_role"], row["model_tier"], row["model_id"]) == (
            "lead",
            "workhorse",
            "model-work-a",
        )
        assert launch.model == "model-work-a" and launch.runtime == "codex"
        principal = await h.w.broker.authenticate(goal, launch.token)
        assert principal.invocation_id == launch.invocation_id and principal.depth == 0
        assert row["lease_expires_at"] is not None
        assert (await h.budget(goal))["used_invocations"] == 1


async def test_a_duplicate_start_is_a_no_op_that_leaves_the_running_lead(broker_db_url):
    """Admission is at-least-once: a second start_goal never fails or fences the goal."""
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await h.runner.start_goal(goal)  # the duplicate's whole supervision
        assert await h.goal_status(goal) == "running"
        assert (await h.row(lead.invocation_id))["status"] == "running"
        assert len(h.host.launches) == 1 and h.host.terminated == []
        assert (await h.budget(goal))["used_invocations"] == 1
        assert h.sink.outcomes == []  # no model attempt was made for the duplicate


async def test_a_duplicate_that_loses_the_open_race_is_a_no_op(broker_db_url):
    """A start that passed the pre-check but loses the goal-row lock opens nothing."""
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        h.runner._wanted = AsyncMock(return_value=True)  # the pre-check raced the open
        await h.runner.start_goal(goal)
        assert await h.goal_status(goal) == "running"
        assert (await h.row(lead.invocation_id))["status"] == "running"
        assert len(h.host.launches) == 1 and h.host.terminated == []
        assert (await h.budget(goal))["used_invocations"] == 1
        # bu-dhloi1.208: no model ran, so the breaker and evidence never see it.
        assert h.sink.outcomes == ["suppressed"]
        assert h.sink.reasons == ["not_invoked:lead_active"]


async def test_a_child_never_lists_or_calls_a_lead_tool_or_administer(broker_db_url):
    async with harness(broker_db_url) as h:
        grants = (_grant(SEARCH), _grant(RULES))
        goal = await h.goal(*grants)
        lead = await h.lead(goal)
        assert [t.name for t in h.tools.listed(0)] == [
            "goal__delegate",
            "goal__await",
            "goal__stop_child",
            "goal__complete",
            "goal__wait_approval",
            "goal__inbox",
            "goal__escalate",
        ]
        child = await h.delegate(goal, lead.token, SEARCH.identity, RULES.identity)
        child_launch = await h.launched(2)
        assert child_launch.invocation_id == child
        assert [t.name for t in h.tools.listed(1)] == ["goal__return"]
        listed = await h.w.broker.list_tools(goal, child_launch.token)
        assert [c.identity for c in listed] == [SEARCH.identity]
        for name in (
            "goal__delegate",
            "goal__stop_child",
            "goal__complete",
            "goal__wait_approval",
            "goal__inbox",
            "goal__escalate",
        ):
            args = {
                "goal__delegate": delegation(SEARCH.identity),
                "goal__stop_child": {"child_id": str(child)},
                "goal__complete": {"outcome": "completed"},
                "goal__wait_approval": {"effect_id": str(uuid4())},
                "goal__inbox": {},
                "goal__escalate": {},
            }[name]
            reason = await refused(h.call(goal, child_launch.token, name, args))
            assert reason is Reason.CAPABILITY, name
        reason = await refused(h.w.broker.call(goal, child_launch.token, RULES.identity, {}))
        assert reason is Reason.CAPABILITY
        assert len(h.host.launches) == 2  # no grandchild was ever started


async def test_a_lead_takes_its_inbox_through_the_goal_tool(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        assert await h.call(goal, lead.token, "goal__inbox", {}) == {"replies": []}
        reason = await refused(h.call(goal, lead.token, "goal__inbox", {"all": True}))
        assert reason is Reason.POLICY


async def test_the_endpoint_lists_goal_tools_by_depth_and_reserves_the_namespace(broker_db_url):
    from butlers.capability_broker.server import _GrantedTools

    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity)
        child = await h.launched(2)
        provider = _GrantedTools(h.w.broker, h.tools)
        for launch, expected in (
            (lead, {"goal__delegate", "goal__await", SEARCH.identity}),
            (child, {"goal__return", SEARCH.identity}),
        ):
            with patch(
                "butlers.capability_broker.server._credentials",
                return_value=(goal, launch.token),
            ):
                names = {tool.name for tool in await provider._list_tools()}
            assert expected <= names
            assert ("goal__delegate" in names) is (launch is lead)


# -- AC2: D9 delegation ---------------------------------------------------------


async def test_delegation_admits_a_child_with_the_intersected_grant_and_derived_lineage(
    broker_db_url,
):
    async with harness(broker_db_url) as h:
        lead_grant = _grant(SEARCH, resources=(("account", ("a1", "a2")),))
        goal = await h.goal(lead_grant, _grant(RULES))
        lead = await h.lead(goal)
        child = await h.delegate(
            goal,
            lead.token,
            {"capability": SEARCH.identity, "resources": {"account": ["a1"]}},
            RULES.identity,
            calls=2,
            tokens=50,
        )
        launch = await h.launched(2)
        row = await h.row(child)
        assert (row["depth"], row["role"], row["parent_invocation_id"]) == (
            1,
            "child",
            lead.invocation_id,
        )
        assert row["grant_snapshot"] == [
            {"capability": SEARCH.identity, "resources": {"account": ["a1"]}, "park_only": False}
        ]
        assert (row["max_calls"], row["max_tokens"], row["model_tier"]) == (2, 50, "cheap")
        assert row["deadline"] is not None and row["model_id"] == "model-cheap-a"
        assert "Find the receipt" in launch.prompt
        schema = await h.w.owner.fetchval(
            "SELECT result_schema FROM switchboard.goal_child_results WHERE invocation_id = $1",
            child,
        )
        assert schema == SCHEMA
        principal = await h.w.broker.authenticate(goal, launch.token)
        assert principal.depth == 1 and principal.origin == "owner"
        outside = await refused(
            h.w.broker.call(goal, launch.token, SEARCH.identity, {"account": "a2"})
        )
        assert outside is Reason.RESOURCE


async def test_a_non_subset_empty_or_lineage_asserting_request_admits_nothing(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH, resources=(("account", ("a1",)),)))
        lead = await h.lead(goal)
        cases = [
            (delegation(SEND.identity), Reason.CAPABILITY),  # the lead lacks it
            (delegation(SEARCH.identity), Reason.CAPABILITY),  # wider than the lead's scope
            (delegation(), Reason.CAPABILITY),
            (delegation(SEARCH.identity, depth=0), Reason.POLICY),
            (delegation(SEARCH.identity, parent=str(uuid4())), Reason.POLICY),
            (delegation(SEARCH.identity, tier="reasoning"), Reason.POLICY),  # not a child tier
        ]
        for args, reason in cases:
            assert await refused(h.call(goal, lead.token, "goal__delegate", args)) is reason
        children = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 AND depth = 1", goal
        )
        assert children == 0 and len(h.host.launches) == 1


async def test_a_replaced_lead_cannot_delegate(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )
        await h.runner.supervise_once()
        await h.launched(2)
        reason = await refused(
            h.call(goal, old.token, "goal__delegate", delegation(SEARCH.identity))
        )
        assert reason in (Reason.EPOCH, Reason.AUTHENTICATION)


# -- AC3: budgets fail closed and stay atomic -----------------------------------


async def test_children_beyond_concurrency_or_the_remaining_budget_are_refused(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), max_calls=10)
        lead = await h.lead(goal)
        over = await refused(
            h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity, calls=50))
        )
        assert over is Reason.BUDGET
        tokens = await refused(
            h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity, tokens=5000))
        )
        assert tokens is Reason.BUDGET
        await h.delegate(goal, lead.token, SEARCH.identity)
        await h.delegate(goal, lead.token, SEARCH.identity)  # max_children = 2
        third = await refused(
            h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity))
        )
        assert third is Reason.BUDGET
        assert await h.goal_status(goal) == "running"


async def test_concurrent_delegations_racing_the_last_child_slot_admit_exactly_one(
    broker_db_url,
):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), max_calls=20)
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity)  # one of two slots
        outcomes = await asyncio.gather(
            *(
                h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity, calls=1))
                for _ in range(4)
            ),
            return_exceptions=True,
        )
        admitted = [o for o in outcomes if isinstance(o, dict)]
        assert len(admitted) == 1
        assert all(o.reason is Reason.BUDGET for o in outcomes if isinstance(o, BrokerRefused))
        live = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 AND depth = 1", goal
        )
        assert live == 2


async def test_a_childs_share_is_its_own_limit_and_the_goal_runs_on(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), max_calls=10)
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity, calls=1)
        child = await h.launched(2)
        assert (await h.w.broker.call(goal, child.token, SEARCH.identity, {})).status == "returned"
        spent = await refused(h.w.broker.call(goal, child.token, SEARCH.identity, {}))
        assert spent is Reason.BUDGET
        assert await h.goal_status(goal) == "running"
        assert (await h.w.broker.call(goal, lead.token, SEARCH.identity, {})).status == "returned"
        row = await h.row(child.invocation_id)
        assert row["used_calls"] == 1


async def test_concurrent_children_never_overspend_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), max_calls=4)
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity, calls=1)
        await h.delegate(goal, lead.token, SEARCH.identity, calls=1)
        children = [await h.launched(2), await h.launched(3)]
        outcomes = await asyncio.gather(
            *(h.w.broker.call(goal, c.token, SEARCH.identity, {}) for c in children for _ in "ab"),
            return_exceptions=True,
        )
        used = (await h.budget(goal))["used_calls"]
        assert used <= 4 and len(h.w.forwarder.calls) == used - 2  # two delegations charged
        assert sum(not isinstance(o, BaseException) for o in outcomes) == used - 2


async def test_usage_beyond_the_goal_budget_ends_it_and_fences_every_invocation(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity, tokens=100)
        child = await h.launched(2)
        assert await h.runner.record_usage(goal, lead.invocation_id, turns=1, tokens=200) == "ok"
        verdict = await h.runner.record_usage(goal, child.invocation_id, tokens=900)
        assert verdict == "goal_exhausted"
        assert await h.goal_status(goal) == "budget_exhausted"
        for launch in (lead, child):
            row = await h.row(launch.invocation_id)
            assert row["status"] == "stopped" and row["token_hash"] is None
            assert launch.invocation_id in h.host.terminated
        budget = await h.budget(goal)
        assert budget["used_tokens"] <= budget["max_tokens"]


async def test_an_invocation_past_its_turns_ends_alone(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity, tokens=100)
        child = await h.launched(2)
        verdict = await h.runner.record_usage(goal, child.invocation_id, tokens=101)
        assert verdict == "invocation_exhausted"
        row = await h.row(child.invocation_id)
        assert (row["status"], row["end_reason"]) == ("failed", "tokens_exhausted")
        assert await h.goal_status(goal) == "running"
        assert child.invocation_id in h.host.terminated
        spend = await h.runner.record_usage(goal, lead.invocation_id, spend=Decimal("2"))
        assert spend == "goal_exhausted"


async def test_a_settled_runs_host_counts_are_charged_to_it_and_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        usage = {"input_tokens": 30, "output_tokens": 20, "cached_input_tokens": 25}
        h.host.script = [None, HostOutcome(usage=usage)]
        lead = await h.lead(goal)
        before = (await h.budget(goal))["used_tokens"]
        child = await h.delegate(goal, lead.token, SEARCH.identity, tokens=100)
        await until(lambda: _equals(_used_tokens(h, child), 50))  # cached is part of input
        assert (await h.budget(goal))["used_tokens"] == before + 50
        assert await h.goal_status(goal) == "running"


async def test_a_failed_run_that_spent_its_budget_is_never_relaunched(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        # Retryable on its own (no call recorded), but its counts exhaust the lead's tokens.
        usage = {"input_tokens": 400, "output_tokens": 200}
        h.host.script = [HostOutcome(failure=TimeoutError("timeout"), usage=usage)]
        lead = await h.lead(goal)
        await until(lambda: _equals(_status(h, lead.invocation_id), "failed"))
        async with asyncio.timeout(5):  # a relaunch would be held open by the host
            await h.runner.drain()
        assert len(h.host.launches) == 1
        row = await h.row(lead.invocation_id)
        assert row["end_reason"] == "tokens_exhausted"


async def _used_tokens(h: Harness, invocation_id: UUID) -> int:
    return (await h.row(invocation_id))["used_tokens"]


async def _status(h: Harness, invocation_id: UUID) -> str:
    return (await h.row(invocation_id))["status"]


# -- AC4: D11 states, settling and the owner reply -------------------------------


async def test_an_owner_goal_completes_only_when_its_reply_committed(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        reply = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        assert reply.status == "committed"
        result = await h.call(
            goal,
            lead.token,
            "goal__complete",
            {"outcome": "completed", "reply_effect_id": str(reply.effect_id)},
        )
        assert result == {"status": "completed"}
        outcome = await h.w.owner.fetchrow(
            "SELECT * FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
        )
        assert (outcome["final_status"], outcome["delivery_failure"]) == ("completed", None)
        row = await h.row(lead.invocation_id)
        assert (row["status"], row["end_reason"], row["token_hash"]) == (
            "returned",
            "completed",
            None,
        )


async def test_an_owner_goal_without_a_delivered_reply_ends_partial(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, "goal__complete", {"outcome": "completed"})
        assert result == {"status": "partial"}
        failure = await h.w.owner.fetchval(
            "SELECT delivery_failure FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
        )
        assert failure == "reply_missing"
        foreign = await refused(
            h.call(
                goal,
                lead.token,
                "goal__complete",
                {"outcome": "completed", "relied_effects": [str(uuid4())]},
            )
        )
        assert foreign in (Reason.GOAL, Reason.AUTHENTICATION)  # the goal already ended


async def _committed_effect(h: Harness, goal: UUID, lead: Launch, capability: str, **recipient):
    """A committed effect of the goal, as a domain's receipt would leave it."""
    effect_id = uuid4()
    columns = ", ".join(("id", "goal_id", "invocation_id", "capability", "args_digest", "status"))
    extra = "".join(f", {name}" for name in recipient)
    values = ", ".join(f"${i}" for i in range(1, 7 + len(recipient)))
    await h.w.owner.execute(
        f"INSERT INTO switchboard.effects ({columns}{extra}) VALUES ({values})",
        effect_id,
        goal,
        lead.invocation_id,
        capability,
        "0" * 64,
        "committed",
        *recipient.values(),
    )
    return effect_id


async def test_the_owner_reply_records_its_owner_recipient(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        reply = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        effect = await h.w.effect(reply.effect_id)
        assert effect["recipient_entity"] == OWNER


@pytest.mark.parametrize("kind", ["record", "non_owner"])
async def test_only_an_owner_delivery_can_be_the_owner_reply(broker_db_url, kind):
    """D11: a committed effect that did not reach the owner never completes the goal."""
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        if kind == "record":
            effect = await _committed_effect(h, goal, lead, PAY.identity)
        else:
            effect = await _committed_effect(h, goal, lead, SEND.identity, recipient_entity=uuid4())
        reason = await refused(
            h.call(
                goal,
                lead.token,
                "goal__complete",
                {"outcome": "completed", "reply_effect_id": str(effect)},
            )
        )
        assert reason is Reason.RESOURCE
        assert await h.goal_status(goal) == "running"


async def test_settling_rechecks_that_the_reply_reached_the_owner(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        effect = await _committed_effect(h, goal, lead, SEND.identity, recipient_entity=uuid4())
        # An outcome that bypassed goal.complete's check: resolve must not trust it.
        await h.w.owner.execute(
            "INSERT INTO switchboard.goal_outcomes (goal_id, invocation_id, outcome, "
            "relied_effects, reply_effect_id) VALUES ($1, $2, 'completed', '{}', $3)",
            goal,
            lead.invocation_id,
            effect,
        )
        async with h.w.owner.acquire() as conn, conn.transaction():
            assert await WaitLedger(conn).open_settle(goal, 0) is not None
            await conn.execute(
                "UPDATE switchboard.goals SET status = 'waiting' WHERE id = $1", goal
            )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "partial"
        failure = await h.w.owner.fetchval(
            "SELECT delivery_failure FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
        )
        assert failure == "reply_not_owner_delivery"


async def test_a_completed_goal_settles_until_its_parked_effects_resolve(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEND))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, NOTIFY.identity)
        child = await h.launched(2)
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        assert parked.status == "parked"
        reply = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        result = await h.call(
            goal,
            lead.token,
            "goal__complete",
            {
                "outcome": "completed",
                "reply_effect_id": str(reply.effect_id),
                "relied_effects": [str(parked.effect_id)],
            },
        )
        assert result == {"status": "waiting"}
        assert (await h.row(child.invocation_id))["status"] == "stopped"
        assert child.invocation_id in h.host.terminated
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "waiting"
        await h.w.domain.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", parked.effect_id
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "completed"
        # The settle fire is the envelope's one wake, resolved with no successor epoch.
        envelope = await h.w.owner.fetchrow(
            "SELECT kind, state, consumed_wakes FROM switchboard.wait_envelopes WHERE goal_id = $1",
            goal,
        )
        assert (envelope["kind"], envelope["state"], envelope["consumed_wakes"]) == (
            "settle",
            "fired",
            1,
        )
        assert not await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.wake_intents WHERE goal_id = $1", goal
        )


async def test_a_lead_that_exits_without_completing_fails_its_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        h.host.end(lead.invocation_id)
        await until(lambda: h.goal_status(goal))
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        row = await h.row(lead.invocation_id)
        assert row["end_reason"] == "returned_without_outcome" and row["token_hash"] is None


async def _equals(awaitable, expected) -> bool:
    return (await awaitable) == expected


# -- AC5: Stop fences the goal ---------------------------------------------------


async def test_stop_fences_revokes_terminates_and_cancels_parked_effects(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity)
        child = await h.launched(2)
        args = {"entity_id": str(uuid4()), "message": "Hello", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        assert await h.runner.stop(goal) == 2
        assert await h.goal_status(goal) == "stopped"
        tokens = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 "
            "AND token_hash IS NOT NULL",
            goal,
        )
        assert tokens == 0
        assert {lead.invocation_id, child.invocation_id} <= set(h.host.terminated)
        assert (await h.w.effect(parked.effect_id))["status"] == "cancelled"
        assert (await h.w.action(parked.effect_id))["status"] == "cancelled"
        for launch in (lead, child):
            late = await refused(h.w.broker.call(goal, launch.token, SEARCH.identity, {}))
            assert late is Reason.GOAL
        assert await refused(
            h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity))
        ) in (Reason.GOAL, Reason.AUTHENTICATION)
        assert await h.runner.stop(goal) == 0  # idempotent
        await h.runner.drain()
        assert len(h.host.launches) == 2  # nothing relaunched after Stop


async def test_stop_racing_delegations_leaves_no_running_child(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), max_calls=20)
        lead = await h.lead(goal)
        await asyncio.gather(
            h.runner.stop(goal),
            *(
                h.call(goal, lead.token, "goal__delegate", delegation(SEARCH.identity, calls=1))
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        await h.runner.drain()
        live = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 "
            "AND status IN ('launching', 'running')",
            goal,
        )
        assert live == 0 and await h.goal_status(goal) == "stopped"


async def test_stop_after_completion_stops_a_waiting_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY))
        lead = await h.lead(goal)
        args = {"entity_id": str(uuid4()), "message": "Hello", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        await h.call(goal, lead.token, "goal__complete", {"outcome": "partial"})
        assert await h.goal_status(goal) == "waiting"
        await h.runner.stop(goal)
        assert await h.goal_status(goal) == "stopped"
        assert (await h.w.effect(parked.effect_id))["status"] == "cancelled"
        # The settle wait closes with the goal; Stop leaves no open envelope behind.
        states = await h.w.owner.fetch(
            "SELECT kind, state FROM switchboard.wait_envelopes WHERE goal_id = $1", goal
        )
        assert [(r["kind"], r["state"]) for r in states] == [("settle", "cancelled")]


# -- AC6: lease recovery and runtime-failure relaunch (D14) ----------------------


async def test_a_lapsed_lead_is_replaced_at_the_next_epoch_and_its_children_report_to_it(
    broker_db_url,
):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH), _grant(SEND))
        old = await h.lead(goal)
        child = await h.delegate(goal, old.token, SEARCH.identity)
        child_launch = await h.launched(2)
        reply = await h.w.broker.call(goal, old.token, SEND.identity, TO_OWNER)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )
        await h.runner.supervise_once()
        new = await h.launched(3)
        old_row, new_row = await h.row(old.invocation_id), await h.row(new.invocation_id)
        assert (old_row["status"], old_row["end_reason"]) == ("timed_out", "lease_lapsed")
        assert (new_row["lead_epoch"], new_row["status"]) == (1, "running")
        assert old.invocation_id in h.host.terminated
        assert await h.goal_status(goal) == "running"
        # The replacement starts with the effect ledger and the goal's children.
        assert str(reply.effect_id) in new.prompt and str(child) in new.prompt
        # The child keeps running and reports to the new lead.
        assert (await h.row(child))["status"] == "running"
        await h.w.broker.call(goal, child_launch.token, SEARCH.identity, {})
        awaited = await h.call(goal, new.token, "goal__await", {"child_id": str(child)})
        assert awaited["status"] == "running"
        # Recovery drew from the same budget; nothing was replayed.
        assert (await h.budget(goal))["used_invocations"] == 3
        effects = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.effects WHERE goal_id = $1", goal
        )
        assert effects == 1


async def test_await_tells_a_child_that_never_ran_from_one_that_failed(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        ended = {}
        for count, reason in enumerate(("launch_failed", "runtime_failure"), start=2):
            child = await h.delegate(goal, lead.token, SEARCH.identity)
            await h.launched(count)
            async with h.runner._txn() as ledger:
                await ledger.end(goal, child, "failed", reason)
            h.host.end(child)
            ended[reason] = child
        running = await h.delegate(goal, lead.token, SEARCH.identity)
        for reason, child in ended.items():
            awaited = await h.call(goal, lead.token, "goal__await", {"child_id": str(child)})
            assert (awaited["status"], awaited["end_reason"]) == ("failed", reason)
        awaited = await h.call(goal, lead.token, "goal__await", {"child_id": str(running)})
        assert awaited["end_reason"] is None  # still live: no end yet


async def test_a_retryable_failure_with_no_call_relaunches_once_at_the_next_epoch(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [HostOutcome(failure=_retryable())]
        first = await h.lead(goal)
        second = await h.launched(2)
        assert second.model == "model-work-b"  # the same tier's fallback model
        first_row, second_row = await h.row(first.invocation_id), await h.row(second.invocation_id)
        assert (first_row["status"], first_row["end_reason"]) == ("failed", "runtime_failure")
        assert (second_row["lead_epoch"], second_row["relaunch_of"]) == (1, first.invocation_id)
        assert (await h.budget(goal))["used_invocations"] == 2
        assert h.sink.outcomes == ["runtime_failure"]


async def test_a_failure_after_a_capability_call_never_relaunches(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))

        async def call_then_fail(launch: Launch) -> HostOutcome:
            await h.w.broker.call(goal, launch.token, SEARCH.identity, {})
            return HostOutcome(failure=_retryable())

        h.host.script = [call_then_fail]
        lead = await h.lead(goal)
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        await h.runner.drain()
        assert len(h.host.launches) == 1
        row = await h.row(lead.invocation_id)
        assert (row["status"], row["end_reason"], row["used_calls"]) == (
            "failed",
            "runtime_failure",
            1,
        )


async def test_a_refused_call_also_forbids_the_relaunch(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))

        async def refused_then_fail(launch: Launch) -> HostOutcome:
            with pytest.raises(BrokerRefused):
                await h.w.broker.call(goal, launch.token, SEND.identity, TO_OWNER)
            return HostOutcome(failure=_retryable())

        h.host.script = [refused_then_fail]
        await h.lead(goal)
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        await h.runner.drain()
        assert len(h.host.launches) == 1


async def test_only_one_relaunch_is_ever_made(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [HostOutcome(failure=_retryable()), HostOutcome(failure=_retryable())]
        await h.lead(goal)
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        await h.runner.drain()
        assert len(h.host.launches) == 2
        assert (await h.budget(goal))["used_invocations"] == 2


async def test_a_failed_child_relaunches_as_a_new_child_with_its_share(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [None, HostOutcome(failure=_retryable())]
        lead = await h.lead(goal)
        first = await h.delegate(goal, lead.token, SEARCH.identity, calls=2)
        relaunch = await h.launched(3)
        row = await h.row(relaunch.invocation_id)
        assert row["relaunch_of"] == first and row["parent_invocation_id"] == lead.invocation_id
        assert (row["max_calls"], row["depth"], row["model_id"]) == (2, 1, "model-cheap-b")
        assert (await h.row(first))["status"] == "failed"
        assert await h.goal_status(goal) == "running"
        returned = await h.call(goal, relaunch.token, "goal__return", {"result": {"found": True}})
        assert returned == {"status": "returned"}
        awaited = await h.call(
            goal, lead.token, "goal__await", {"child_id": str(relaunch.invocation_id)}
        )
        assert awaited["status"] == "returned" and awaited["result"] == {"found": True}


async def test_an_exhausted_invocation_budget_ends_the_goal_budget_exhausted(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        await h.w.owner.execute(
            "UPDATE switchboard.budgets SET max_invocations = 1 WHERE goal_id = $1", goal
        )
        h.host.script = [HostOutcome(failure=_retryable())]
        await h.lead(goal)
        await until(lambda: _equals(h.goal_status(goal), "budget_exhausted"))
        await h.runner.drain()
        # The model failure counts; the relaunch the budget refused never ran one.
        assert h.sink.outcomes == ["runtime_failure", "suppressed"]
        assert h.sink.reasons[-1] == "not_invoked:invocation_budget_exhausted"


# -- D14: a runner that dies between ending a lead and launching its successor ----

_RESUMING = RunnerConfig(renew_every=3600, lead_max_tokens=500, resume_grace_seconds=0)


async def _crash_after(h: Harness, lead: Launch, step) -> None:
    """Commit what the runner commits before ``_spawn``, then lose the process.

    The old session ends with nothing for its supervisor to do: the ledger
    already ended it, as a restarted runner would find it.
    """
    async with h.runner._txn() as ledger:
        await step(ledger)
    h.host.end(lead.invocation_id)
    await until(lambda: len(h.runner._tasks) == 0)


async def test_a_goal_left_admitted_by_a_dead_runner_gets_its_replacement(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )
        await _crash_after(h, old, lambda ledger: ledger.expire(goal, old.invocation_id))
        assert await h.goal_status(goal) == "admitted" and len(h.host.launches) == 1
        await h.runner.supervise_once()
        new = await h.launched(2)
        row = await h.row(new.invocation_id)
        assert (row["lead_epoch"], row["model_tier"], row["relaunch_of"]) == (1, "workhorse", None)
        assert await h.goal_status(goal) == "running"
        await h.runner.supervise_once()  # a live lead: nothing more to resume
        assert len(h.host.launches) == 2


async def test_a_goal_whose_escalation_outlived_its_runner_resumes_on_the_next_tier(
    broker_db_url,
):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)

        async def escalate(ledger):
            principal = await ledger.broker.authenticate(goal, old.token)
            await ledger.escalate(principal)

        await _crash_after(h, old, escalate)
        assert await h.goal_status(goal) == "running" and len(h.host.launches) == 1
        await h.runner.supervise_once()
        new = await h.launched(2)
        assert new.model == "model-reason-a"
        assert (await h.row(new.invocation_id))["model_tier"] == "reasoning"


async def test_a_runtime_failure_without_calls_gets_its_one_relaunch_from_the_sweep(
    broker_db_url,
):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await _crash_after(
            h, old, lambda ledger: ledger.end(goal, old.invocation_id, "failed", "runtime_failure")
        )
        h.host.script = [HostOutcome(failure=_retryable())]  # the relaunch fails too
        await h.runner.supervise_once()
        relaunch = await h.launched(2)
        assert (await h.row(relaunch.invocation_id))["relaunch_of"] == old.invocation_id
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        await h.runner.supervise_once()
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 2  # one relaunch, never a second


async def test_a_runtime_failure_after_a_call_ends_the_goal_from_the_sweep(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await h.w.broker.call(goal, old.token, SEARCH.identity, {})
        await _crash_after(
            h, old, lambda ledger: ledger.end(goal, old.invocation_id, "failed", "runtime_failure")
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "failed"
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 1


async def test_a_lead_past_its_turns_ends_its_goal_budget_exhausted(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await _crash_after(
            h, old, lambda ledger: ledger.end(goal, old.invocation_id, "failed", "turns_exhausted")
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "budget_exhausted"
        assert len(h.host.launches) == 1


# -- a session host at its concurrency bound defers a launch (bu-dhloi1.265) -----


def _busy() -> HostOutcome:
    return HostOutcome(failure=SessionHostBusy("session_host_busy"))


async def _deferred(h: Harness, launch: Launch) -> None:
    """The busy launch's supervision is over: its invocation ended, nothing else."""
    await until(lambda: _equals(_status(h, launch.invocation_id), "failed"))
    await until(lambda: len(h.runner._tasks) == 0)


async def test_a_lead_the_host_had_no_room_for_leaves_its_goal_for_the_sweep(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [_busy()]
        busy = await h.lead(goal)
        await _deferred(h, busy)
        row = await h.row(busy.invocation_id)
        assert (row["end_reason"], row["token_hash"]) == ("launch_failed", None)
        assert await h.goal_status(goal) == "running"  # not failed: resumable
        budget = await h.budget(goal)
        assert (budget["used_invocations"], budget["used_tokens"]) == (0, 0)  # nothing charged
        assert h.sink.outcomes == ["suppressed"]  # never a runtime failure, never relaunched
        assert len(h.host.launches) == 1
        await h.runner.supervise_once()  # the host has room now
        lead = await h.launched(2)
        row = await h.row(lead.invocation_id)
        assert (row["status"], row["lead_epoch"], row["model_tier"]) == ("running", 1, "workhorse")
        assert (await h.budget(goal))["used_invocations"] == 1


async def test_a_lead_deferred_past_its_bound_ends_its_goal(broker_db_url):
    config = RunnerConfig(
        renew_every=3600, lead_max_tokens=500, resume_grace_seconds=0, busy_defer_seconds=0
    )
    async with harness(broker_db_url, config=config) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [_busy(), _busy()]
        busy = await h.lead(goal)
        await _deferred(h, busy)
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "failed"
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 1


async def test_deferral_is_bounded_from_the_first_busy_lead_not_the_last(broker_db_url):
    config = RunnerConfig(
        renew_every=3600, lead_max_tokens=500, resume_grace_seconds=0, busy_defer_seconds=60
    )
    async with harness(broker_db_url, config=config) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [_busy(), _busy()]
        first = await h.lead(goal)
        await _deferred(h, first)
        await h.runner.supervise_once()
        second = await h.launched(2)
        await _deferred(h, second)
        # The goal has waited on the host since its first busy lead; age that one.
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET created_at = now() - interval '61 seconds' "
            "WHERE id = $1",
            first.invocation_id,
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "failed"
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 2


async def test_a_busy_relaunch_is_replaced_but_never_earns_a_second_relaunch(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        # The lead fails (no call): its one relaunch finds the host busy.
        h.host.script = [HostOutcome(failure=_retryable()), _busy()]
        first = await h.lead(goal)
        relaunch = await h.launched(2)
        await _deferred(h, relaunch)
        assert (await h.row(relaunch.invocation_id))["relaunch_of"] == first.invocation_id
        assert await h.goal_status(goal) == "running"
        h.host.script = [HostOutcome(failure=_retryable())]  # the replacement fails too
        await h.runner.supervise_once()
        await h.launched(3)
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        await h.runner.supervise_once()
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 3  # the relaunch ran once; nothing after it


async def test_stop_during_a_deferral_wins(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [_busy()]
        busy = await h.lead(goal)
        await _deferred(h, busy)
        await h.runner.stop(goal)
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "stopped"
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 1


async def test_stop_while_the_host_is_still_being_offered_the_launch_wins(broker_db_url):
    async with harness(broker_db_url, config=_RESUMING) as h:
        goal = await h.goal(_grant(SEARCH))

        async def stopped_while_busy(launch: Launch) -> HostOutcome:
            await h.runner.stop(goal)
            return _busy()

        h.host.script = [stopped_while_busy]
        busy = await h.lead(goal)
        await until(lambda: len(h.runner._tasks) == 0)
        row = await h.row(busy.invocation_id)
        assert (row["status"], row["end_reason"]) == ("stopped", "stopped")
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "stopped"
        await until(lambda: len(h.runner._tasks) == 0)
        assert len(h.host.launches) == 1


async def test_a_child_the_host_had_no_room_for_never_fails_its_lead(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        h.host.script = [None, _busy()]
        lead = await h.lead(goal)
        child = await h.delegate(goal, lead.token, SEARCH.identity, calls=2)
        await until(lambda: _equals(_status(h, child), "failed"))
        assert (await h.row(child))["end_reason"] == "launch_failed"
        assert (await h.row(lead.invocation_id))["status"] == "running"
        assert await h.goal_status(goal) == "running"
        assert (await h.budget(goal))["used_invocations"] == 1  # the child drew nothing
        assert len(h.host.launches) == 2  # no relaunch of a launch that never ran
        awaited = await h.call(goal, lead.token, "goal__await", {"child_id": str(child)})
        assert awaited["status"] == "failed"


async def test_a_leaderless_goal_inside_the_grace_is_left_to_its_runner(broker_db_url):
    async with harness(broker_db_url) as h:  # default grace
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await _crash_after(
            h, old, lambda ledger: ledger.end(goal, old.invocation_id, "failed", "runtime_failure")
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "running" and len(h.host.launches) == 1


# -- AC7: escalation climbs a finite ladder (D16) --------------------------------


async def test_escalation_hands_the_goal_to_the_next_tier_under_the_same_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, "goal__escalate", {})
        assert result == {"status": "escalated", "tier": "reasoning"}
        new = await h.launched(2)
        assert new.model == "model-reason-a"
        row = await h.row(new.invocation_id)
        assert (row["lead_epoch"], row["model_tier"]) == (1, "reasoning")
        assert (await h.row(lead.invocation_id))["status"] == "escalated"
        await until(lambda: lead.invocation_id in h.host.terminated)
        assert await h.w.owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
        assert (await h.budget(goal))["used_invocations"] == 2
        top = await refused(h.call(goal, new.token, "goal__escalate", {}))
        assert top is Reason.POLICY  # the ladder is finite
        assert (await h.row(new.invocation_id))["status"] == "running"


async def test_escalation_with_no_qualifying_higher_tier_keeps_the_current_lead(broker_db_url):
    tiers = {"workhorse": TIERS["workhorse"], "cheap": TIERS["cheap"]}
    async with harness(broker_db_url, tiers=tiers) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        assert await refused(h.call(goal, lead.token, "goal__escalate", {})) is Reason.POLICY
        assert (await h.row(lead.invocation_id))["status"] == "running"
        assert len(h.host.launches) == 1


async def test_escalation_onto_an_exhausted_provider_ends_and_nothing_waits(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        h.quotas[TIERS["reasoning"][3]] = _EXHAUSTED
        await h.call(goal, lead.token, "goal__escalate", {})
        await until(lambda: _equals(h.goal_status(goal), "failed"))
        live = await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.invocations WHERE goal_id = $1 "
            "AND status IN ('launching', 'running')",
            goal,
        )
        assert live == 0 and len(h.host.launches) == 1


async def test_a_monitor_starts_on_the_cheapest_rung(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        h.runner.start_goal(goal, role="monitor")
        launch = await h.launched(1)
        row = await h.row(launch.invocation_id)
        assert (row["model_role"], row["model_tier"], launch.model) == (
            "monitor",
            "cheap",
            "model-cheap-a",
        )


# -- AC9: telemetry carries the D17 identifiers only -----------------------------


async def test_spans_carry_goal_invocation_depth_and_lead_epoch_only(broker_db_url, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("butlers.goal_runner.runner._tracer", provider.get_tracer("t"))
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, SEARCH.identity)
        await h.launched(2)
        await h.runner.stop(goal)
        await h.runner.drain()
    spans = {}
    for span in exporter.get_finished_spans():
        spans.setdefault(span.name, []).append(dict(span.attributes))
    invocations = spans["goal.invocation"]
    lead_span = next(a for a in invocations if a["depth"] == 0)
    child_span = next(a for a in invocations if a["depth"] == 1)
    assert set(lead_span) == {"goal_id", "invocation_id", "depth", "epoch"}
    assert set(child_span) == {"goal_id", "invocation_id", "depth"}
    assert lead_span["invocation_id"] == str(lead.invocation_id)
    allowed = {"goal_id", "invocation_id", "child_invocation_id", "depth", "epoch", "tier"}
    for attributes in (a for values in spans.values() for a in values):
        assert set(attributes) <= allowed
    assert "Find the receipt" not in repr(spans)
