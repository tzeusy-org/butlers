"""The goal runner: launches, supervision, Stop and recovery (RFC 0038 D8-D11, D14-D17).

The runner is the only thing that starts goal sessions: leads on admission
(``start_goal``), recovery (``supervise_once``) and escalation; children on a
lead's ``goal.delegate``. Every launch goes through the model-call kernel
(``butlers.core.model_call.call_model``), which owns tier resolution, the
spend and quota gates, failure classification and the one D14 relaunch. The
runner supplies only the attempt: a ledger row, a token, a session-host launch,
and the broker's record of the calls that token made, which is what forbids a
relaunch after a capability call.

Dark: constructed only by trusted control-plane wiring, behind the M6 switch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_OID, UUID, uuid5

from opentelemetry import trace

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.service import Broker
from butlers.conditions import (
    ConditionEvaluator,
    ConditionsUnavailable,
    FireIntent,
    PredicateError,
)
from butlers.conditions.sql import PostgresConditionStore
from butlers.core.goal_labels import Label
from butlers.core.goal_wake import WakeAdmission, WakeResult
from butlers.core.model_call import AttemptOutcome, AttemptSink, CallSpec, Candidate, call_model

from .contracts import (
    Brief,
    GoalEnded,
    HostAtCapacity,
    HostOutcome,
    InvocationBudgetExhausted,
    Launch,
    LaunchContext,
    PromptComposer,
    Role,
    SessionHost,
)
from .delegation import DelegationRequest, parse_delegation
from .ledger import Leaderless, RunnerLedger, Started, WakeClaim, plan_refs
from .models import ModelLadders, next_tier
from .own_conditions import MAX_WAKES, events_for, own_predicate
from .packet import PacketComposer

_ADMIT_ATTEMPTS = 3  # sweeps a fire may fail admission before it is closed as admit_error
logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

Kernel = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Operational tuning; bounds work, never authority (owner decision 5)."""

    lease_seconds: float = 90
    renew_every: float = 30
    lead_max_turns: int = 40
    lead_max_tokens: int = 400_000
    child_max_turns: int = 20
    launch_timeout_s: float = 300
    transaction_timeout: float = 5
    # How long a running goal may sit without a live lead before the sweep
    # resumes it; an in-process successor opens well inside this.
    resume_grace_seconds: float = 90
    # How long a goal may wait for room on the session host, from its first lead
    # the host turned away; the sweep offers the launch again until then.
    busy_defer_seconds: float = 900
    # How long a fire intent may sit unadmitted before the sweep admits it (the step that
    # follows the evaluator's commit normally runs at once), and how long a wake claim holds.
    wake_grace_seconds: float = 30
    wake_claim_seconds: float = 120


@dataclass(frozen=True, slots=True)
class _Plan:
    """One logical invocation the kernel launches (and may relaunch once)."""

    goal_id: UUID
    role: Role
    tier: str
    depth: int
    child_id: UUID | None = None
    brief: Brief | None = None
    summary: bool = False
    relaunch_of: UUID | None = None
    """A sweep-resumed relaunch: it is the failed lead's one relaunch (D14)."""
    relaunch_spent: bool = False
    """Replaces a relaunch the host had no room for: it is that relaunch, so none follows."""
    wake: WakeClaim | None = None
    """The claimed wake intent this lead is the successor of (RFC 0038 S2.2)."""


class _HostedAttempt:
    """The kernel's adapter slot: the session host runs the runtime, not this process."""

    def __init__(self) -> None:
        self.last_process_info: dict[str, Any] | None = None


def _attach_calls(failure: BaseException, calls: list[dict[str, str]]) -> BaseException:
    """Feed the broker's recorded calls to the classifier (D14), never fewer than seen."""
    if calls:
        existing = list(getattr(failure, "tool_calls", None) or [])
        try:
            failure.tool_calls = existing + calls  # type: ignore[attr-defined]
        except AttributeError:
            wrapped = RuntimeError("runtime_failure_after_capability_call")
            wrapped.tool_calls = existing + calls  # type: ignore[attr-defined]
            return wrapped
    return failure


class OwnerFailures(Protocol):
    """The sweep step that tells the owner about a request that failed unannounced."""

    async def sweep(self) -> int: ...


class GoalRunner:
    def __init__(
        self,
        broker: Broker,
        host: SessionHost,
        *,
        catalog_pool: Any,
        sink: AttemptSink | None = None,
        ladders: ModelLadders | None = None,
        composer: PromptComposer | None = None,
        config: RunnerConfig | None = None,
        kernel: Kernel = call_model,
        owner_failures: OwnerFailures | None = None,
    ):
        self.broker = broker
        self.host = host
        self.catalog_pool = catalog_pool
        self.sink = sink if sink is not None else AttemptSink(catalog_pool)
        self.ladders = ladders or ModelLadders()
        self.composer = composer or PacketComposer()
        self.config = config or RunnerConfig()
        self.kernel = kernel
        self.owner_failures = owner_failures
        self._tasks: set[asyncio.Task] = set()
        self._admit_failures: dict[UUID, int] = {}

    # -- plumbing -------------------------------------------------------------

    @asynccontextmanager
    async def _txn(self):
        async with asyncio.timeout(self.config.transaction_timeout):
            async with self.broker.pool.acquire() as connection, connection.transaction():
                yield RunnerLedger(connection)

    def _spawn(self, plan: _Plan) -> asyncio.Task:
        task = asyncio.create_task(self._supervise(plan))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Wait for every supervised invocation (tests and shutdown).

        A task leaves ``_tasks`` through its ``discard`` done-callback, which runs
        on a later loop iteration than the one it finished in. ``gather`` of
        tasks that are all done completes without yielding, so drain removes the
        tasks it has waited for itself rather than waiting on that callback
        (bu-dhloi1.227: otherwise it re-gathers them forever and never yields).
        Tasks spawned meanwhile are waited for on the next round.
        """
        while waiting := tuple(self._tasks):
            await asyncio.gather(*waiting, return_exceptions=True)
            self._tasks.difference_update(waiting)

    async def _terminate(self, invocation_id: UUID) -> None:
        try:
            await self.host.terminate(invocation_id)
        except Exception:
            logger.warning("goal runner terminate failed invocation=%s", invocation_id)

    async def _terminate_all(self, invocation_ids: list[UUID]) -> None:
        for invocation_id in invocation_ids:
            await self._terminate(invocation_id)

    def _terminate_later(self, invocation_id: UUID) -> None:
        """Terminate a session from inside its own tool call, after the reply."""
        task = asyncio.create_task(self._terminate(invocation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- launches -------------------------------------------------------------

    def start_goal(self, goal_id: UUID, role: Role = "lead") -> asyncio.Task:
        """Launch an admitted goal's lead on the first tier of its role's ladder."""
        if role == "child":
            raise ValueError("a goal starts with a lead")
        return self._spawn(_Plan(goal_id, role, self.ladders.first(role), 0))

    async def _open(self, plan: _Plan, attempts: list[UUID], candidate: Candidate) -> Started:
        lease = self.config.lease_seconds
        async with self._txn() as ledger:
            if plan.depth == 1:
                if not attempts:
                    assert plan.child_id is not None
                    return await ledger.start_child(
                        plan.goal_id, plan.child_id, candidate.model_id, lease
                    )
                return await ledger.relaunch_child(
                    plan.goal_id, attempts[-1], candidate.model_id, lease
                )
            wake = None if attempts else plan.wake
            catalog = None
            if wake is not None:
                # A wake is narrowed by current policy, read in the launch's transaction. An
                # unreadable catalog opens nothing: the intent is marked failed and the sweep
                # retries it until the envelope deadline, rather than failing the goal.
                try:
                    catalog = await self.broker._live_catalog.load(ledger.connection)
                except BrokerRefused as refused:
                    if refused.reason is not Reason.UNAVAILABLE:
                        raise
                    raise GoalEnded("catalog_unavailable") from None
            return await ledger.open_lead(
                plan.goal_id,
                role=plan.role,
                tier=plan.tier,
                model_id=candidate.model_id,
                max_turns=self.config.lead_max_turns,
                max_tokens=self.config.lead_max_tokens,
                lease_seconds=lease,
                relaunch_of=attempts[-1] if attempts else plan.relaunch_of,
                wake=wake,
                catalog=catalog,
            )

    async def _wanted(self, plan: _Plan) -> bool:
        """Whether the plan still has an invocation to open (a pre-check)."""
        async with self._txn() as ledger:
            if plan.depth == 1:
                assert plan.child_id is not None
                return await ledger.child_wanted(plan.goal_id, plan.child_id)
            return await ledger.lead_wanted(plan.goal_id)

    async def _supervise(self, plan: _Plan) -> None:
        """Launch the plan's invocation through the kernel and act on how it ended.

        A plan whose goal is no longer runnable, or whose lead slot is already
        held (a duplicate start, or a recovery or escalation that lost a race),
        opens nothing and ends nothing: only a failure of an invocation this
        plan opened can end the goal.
        """
        if not await self._wanted(plan):
            logger.info("goal runner launch not needed goal=%s role=%s", plan.goal_id, plan.role)
            return
        attempts: list[UUID] = []
        unopened: list[str] = []

        async def attempt(adapter: _HostedAttempt, candidate: Candidate) -> AttemptOutcome:
            try:
                started = await self._open(plan, attempts, candidate)
            except GoalEnded as refused:
                unopened.append(str(refused))
                raise
            attempts.append(started.invocation_id)
            adapter.last_process_info = {"runtime_type": candidate.runtime_type}
            outcome = await self._launch(plan, started, candidate)
            if isinstance(outcome.failure, HostAtCapacity):
                # Nothing ran: no failure to count, nothing to charge, no relaunch.
                async with self._txn() as ledger:
                    await ledger.defer(plan.goal_id, started.invocation_id)
                raise outcome.failure
            if outcome.usage:
                # The host's settled counts (D10), charged before the relaunch decision,
                # so a run that spent the budget is never relaunched.
                await self.record_usage(
                    plan.goal_id, started.invocation_id, tokens=_host_tokens(outcome.usage)
                )
            async with self._txn() as ledger:
                status = await ledger.status_of(plan.goal_id, started.invocation_id)
                if outcome.failure is None or status not in ("launching", "running"):
                    # A clean exit, or one the ledger already ended (goal.return,
                    # goal.complete, Stop, escalation, a lapsed lease): no relaunch.
                    return AttemptOutcome("", outcome.usage)
                await ledger.end(plan.goal_id, started.invocation_id, "failed", "runtime_failure")
                calls = await ledger.recorded_calls(plan.goal_id, started.invocation_id)
            raise _attach_calls(outcome.failure, calls)

        async def adapter_for(candidate: Candidate) -> _HostedAttempt:
            return _HostedAttempt()

        spec = CallSpec(
            butler_name=plan.role,  # D16: the role is the resolution key.
            tier=plan.tier,
            exact_tier=True,
            # A sweep-resumed relaunch already is the failed lead's one relaunch.
            max_relaunches=0 if plan.relaunch_of is not None or plan.relaunch_spent else 1,
            redact_failures=True,
            usage_purpose="goal",
            caller="goal_runner",
        )
        try:
            await self.kernel(
                self.catalog_pool, spec, attempt, adapter_for=adapter_for, sink=self.sink
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if unopened:
                # The open was refused under the goal lock: nothing of this
                # plan is live, and the goal belongs to whoever holds it.
                if plan.wake is not None:
                    async with self._txn() as ledger:
                        await ledger.fail_wake(plan.goal_id, plan.wake.intent_id)
                logger.info(
                    "goal runner launch not opened goal=%s role=%s reason=%s",
                    plan.goal_id,
                    plan.role,
                    unopened[-1],
                )
                return
            if isinstance(error, HostAtCapacity):
                # The invocation ended launch_failed; a lead's goal stays running
                # for the sweep to launch again (bounded), a child's is its lead's.
                logger.info("goal runner launch deferred goal=%s role=%s", plan.goal_id, plan.role)
                return
            await self._after_failure(plan, attempts, error)
            return
        await self._after_exit(plan, attempts[-1])

    async def _launch(self, plan: _Plan, started: Started, candidate: Candidate) -> HostOutcome:
        summary = None
        async with self._txn() as ledger:
            if plan.summary:
                summary = await ledger.summary(plan.goal_id, started.invocation_id)
            facts = await ledger.launch_facts(plan.goal_id, started.invocation_id)
        context = LaunchContext(
            goal_id=plan.goal_id,
            invocation_id=started.invocation_id,
            role=plan.role,
            depth=started.depth,
            epoch=started.epoch,
            origin=started.origin,
            exposed=started.exposed,
            brief=plan.brief,
            ledger=summary,
            facts=facts,
        )
        launch = Launch(
            goal_id=plan.goal_id,
            invocation_id=started.invocation_id,
            token=started.token,
            runtime=candidate.runtime_type,
            model=candidate.model_id,
            prompt=self.composer.compose(context),
            timeout_s=min(self.config.launch_timeout_s, candidate.session_timeout_s or 300),
        )
        renewal = asyncio.create_task(self._renew(plan.goal_id, started.invocation_id))
        with _tracer.start_as_current_span(
            "goal.invocation", record_exception=False, set_status_on_exception=False
        ) as span:
            # D17: identifiers from the invocation record; never arguments or results.
            span.set_attribute("goal_id", str(plan.goal_id))
            span.set_attribute("invocation_id", str(started.invocation_id))
            span.set_attribute("depth", started.depth)
            if started.depth == 0:
                span.set_attribute("epoch", started.epoch)
            try:
                return await self.host.launch(launch)
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # the host reports; a raise is still a failure
                return HostOutcome(failure=failure)
            finally:
                renewal.cancel()

    async def _renew(self, goal_id: UUID, invocation_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.config.renew_every)
            try:
                async with self._txn() as ledger:
                    if not await ledger.renew(goal_id, invocation_id, self.config.lease_seconds):
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                # A lease that cannot be renewed lapses; recovery takes over (D14).
                logger.warning("goal runner lease renewal failed invocation=%s", invocation_id)

    async def _after_failure(self, plan: _Plan, attempts: list[UUID], error: Exception) -> None:
        """No (further) relaunch: the invocation stays failed (D14).

        A child's failure is the lead's to decide. A lead's ends the goal
        ``failed`` with its ledger intact, or ``budget_exhausted`` when the
        invocation budget had no room for it.
        """
        logger.info(
            "goal runner invocation ended goal=%s role=%s attempts=%d error=%s",
            plan.goal_id,
            plan.role,
            len(attempts),
            type(error).__name__,
        )
        async with self._txn() as ledger:
            if plan.depth == 1:
                if not attempts and plan.child_id is not None:
                    await ledger.end(plan.goal_id, plan.child_id, "failed", "launch_failed")
                return
            if isinstance(error, InvocationBudgetExhausted):
                fenced = await ledger.end_goal(plan.goal_id, "budget_exhausted", "budget_exhausted")
            else:
                fenced = await ledger.end_goal(plan.goal_id, "failed", "goal_ended")
        await self._terminate_all(fenced)

    async def _after_exit(self, plan: _Plan, invocation_id: UUID) -> None:
        """A clean exit the ledger had not ended: the session left without its tool.

        A child that never called ``goal.return`` failed; a lead that never
        called ``goal.complete`` (or escalated) leaves its goal ``failed``.
        """
        fenced: list[UUID] = []
        async with self._txn() as ledger:
            if plan.depth == 1:
                await ledger.end(plan.goal_id, invocation_id, "failed", "returned_without_outcome")
                return
            if await ledger.end(
                plan.goal_id, invocation_id, "returned", "returned_without_outcome"
            ):
                fenced = await ledger.end_goal(plan.goal_id, "failed", "goal_ended")
        await self._terminate_all(fenced)

    # -- goal tools (served by GoalTools) -------------------------------------

    async def delegate(self, goal_id: UUID, token: str, arguments: Any) -> UUID:
        """``goal.delegate`` (D9): admit the child, then launch it in the background."""
        request: DelegationRequest = parse_delegation(arguments)
        if request.model_tier not in self.ladders.child_tiers:
            raise BrokerRefused(Reason.POLICY)
        with _tracer.start_as_current_span(
            "goal.delegate", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                catalog = await self.broker._live_catalog.load(broker_ledger.connection)
                child = await RunnerLedger(broker_ledger.connection).delegate(
                    principal, catalog, request, max_turns=self.config.child_max_turns
                )
            if isinstance(child, BrokerRefused):
                raise child
            span.set_attribute("child_invocation_id", str(child))
        self._spawn(
            _Plan(goal_id, "child", request.model_tier, 1, child_id=child, brief=request.brief)
        )
        return child

    async def stop_child(self, goal_id: UUID, token: str, child_id: Any) -> UUID:
        async with self.broker._transaction() as broker_ledger:
            principal = await broker_ledger.authenticate(goal_id, token)
            ledger = RunnerLedger(broker_ledger.connection)
            if principal.depth != 0:
                raise BrokerRefused(Reason.CAPABILITY)
            exhausted = await broker_ledger.charge_call(principal)
            stopped = (
                None if exhausted is not None else await ledger.stop_child(principal, child_id)
            )
        if exhausted is not None:
            raise exhausted
        assert stopped is not None
        await self._terminate(stopped)
        return stopped

    async def complete(
        self,
        goal_id: UUID,
        token: str,
        outcome: Any,
        relied: Any,
        reply: Any,
    ) -> str:
        """``goal.complete`` (D11, D12): record the outcome; the goal settles."""
        if outcome not in ("completed", "partial", "failed"):
            raise BrokerRefused(Reason.POLICY)
        relied_ids = _uuid_list(relied)
        reply_id = None if reply is None else _uuid(reply)
        with _tracer.start_as_current_span(
            "goal.complete", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                ledger = RunnerLedger(broker_ledger.connection)
                children = await ledger.complete(principal, outcome, relied_ids, reply_id)
                if isinstance(children, BrokerRefused):
                    status = ""
                else:
                    status = await ledger.goal_status(goal_id) or ""
            if isinstance(children, BrokerRefused):
                raise children
        await self._terminate_all(children)
        self._terminate_later(principal.invocation_id)
        return status

    async def escalate(self, goal_id: UUID, token: str) -> str:
        """``goal.escalate`` (D16): hand the goal to a lead on the next ladder tier.

        Refused, with the current lead keeping the goal, when no higher tier of
        the role's ladder qualifies or the invocation budget has no room.
        """
        with _tracer.start_as_current_span(
            "goal.escalate", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                if principal.depth != 0:
                    raise BrokerRefused(Reason.CAPABILITY)
                ledger = RunnerLedger(broker_ledger.connection)
                role, tier = await ledger.model_of(principal)
                exhausted = await broker_ledger.charge_call(principal)
            if exhausted is not None:
                raise exhausted
            target = None
            if tier is not None:
                target = await next_tier(self.catalog_pool, self.ladders, role, tier)
            if target is None:
                raise BrokerRefused(Reason.POLICY)
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                ledger = RunnerLedger(broker_ledger.connection)
                if not await ledger.has_invocation_room(goal_id):
                    raise BrokerRefused(Reason.BUDGET)
                await ledger.escalate(principal)
            span.set_attribute("tier", target)
        self._spawn(_Plan(goal_id, role, target, 0, summary=True))
        self._terminate_later(principal.invocation_id)
        return target

    async def goal_return(self, goal_id: UUID, token: str, value: Any) -> None:
        """``goal.return`` (D9): the broker records the result; the child's session ends."""
        principal = await self.broker.authenticate(goal_id, token)
        await self.broker.goal_return(goal_id, token, value)
        self._terminate_later(principal.invocation_id)

    # -- Stop, supervision and recovery (D14) ---------------------------------

    async def stop(self, goal_id: UUID) -> int:
        """Owner Stop: fence in one transaction, terminate, cancel parked effects."""
        stopped, terminated = await self.stop_goal(goal_id)
        return terminated if stopped else 0

    async def stop_goal(self, goal_id: UUID) -> tuple[bool, int]:
        """Owner Stop; ``(whether this call stopped the goal, live invocations ended)``."""
        with _tracer.start_as_current_span(
            "goal.stop", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self._txn() as ledger:
                stopped, live = await ledger.stop(goal_id)
            await self._terminate_all(live)
            if stopped:
                await self._cancel_parked(goal_id)
            return stopped, len(live)

    async def abandon(self) -> None:
        """Shutdown: cancel every supervised invocation's supervision, then wait for it.

        The invocations are not terminated here: their leases stop renewing and
        lapse, and the next runner's sweep fences and replaces them (D14).
        """
        for task in tuple(self._tasks):
            task.cancel()
        await self.drain()

    async def _cancel_parked(self, goal_id: UUID) -> None:
        try:
            await self.broker.cancel_parked(goal_id)
        except BrokerRefused:
            # A stopped goal never executes an approval (begin_approved); the
            # cancellation itself is retried by the next sweep.
            logger.warning("goal runner parked-effect cancel unavailable goal=%s", goal_id)

    async def admit_wake(self, fire: FireIntent) -> WakeResult:
        """Complete a condition fire: its successor epoch, or a terminal refusal receipt.

        The evaluator's fire intent is already committed; this is the second committed step.
        Called by whoever consumes fires, and by the sweep for any it missed.
        """
        async with self._txn() as ledger:
            return await WakeAdmission(ledger.connection).admit(fire)

    async def wait_approval(self, goal_id: UUID, token: str, effect_id: Any) -> str:
        """``goal.wait_approval`` (bu-dhloi1.204): the lead ends; the goal waits on one approval."""
        return await self._wait_on(goal_id, token, effect_id, "wait_approval")

    async def wait_owner(self, goal_id: UUID, token: str, effect_id: Any) -> str:
        """``goal.wait_owner`` (S2.2): the lead ends; the goal waits for the owner's answer."""
        return await self._wait_on(goal_id, token, effect_id, "wait_owner")

    async def wait_until(
        self, goal_id: UUID, token: str, predicate: Any, deadline: Any, max_wakes: Any
    ) -> str:
        """``goal.wait_until`` (S2.2): the lead ends; the goal waits on its own conditions.

        Returns ``woken`` when the predicate already held (its one successor is admitted) and
        ``waiting`` otherwise. A predicate outside the goal-own language, a deadline that is not
        an aware timestamp or a wake count outside ``1..MAX_WAKES`` is refused POLICY.
        """
        try:
            canonical = own_predicate(predicate)
        except PredicateError:
            raise BrokerRefused(Reason.POLICY) from None
        until = _aware(deadline)
        if type(max_wakes) is not int or not 1 <= max_wakes <= MAX_WAKES:
            raise BrokerRefused(Reason.POLICY)
        with _tracer.start_as_current_span(
            "goal.wait_until", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                ledger = RunnerLedger(broker_ledger.connection)
                waited = await ledger.wait_until(principal, canonical, until, max_wakes)
            if isinstance(waited, BrokerRefused):
                raise waited
            children, woken = waited
            span.set_attribute("woken", woken)
        await self._terminate_all(children)
        self._terminate_later(principal.invocation_id)
        return "woken" if woken else "waiting"

    async def plan(self, goal_id: UUID, token: str, premise: Any, evidence: Any, steps: Any) -> int:
        """``goal.plan`` (RFC 0038 S2): record the lead's plan as the goal's next version."""
        refs = (plan_refs(premise), plan_refs(evidence), plan_refs(steps))
        with _tracer.start_as_current_span(
            "goal.plan", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                version = await RunnerLedger(broker_ledger.connection).plan(principal, *refs)
            if isinstance(version, BrokerRefused):
                raise version
            span.set_attribute("plan_version", version)
            return version

    async def _wait_on(self, goal_id: UUID, token: str, effect_id: Any, tool: str) -> str:
        effect = _uuid(effect_id)
        with _tracer.start_as_current_span(
            f"goal.{tool}", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self.broker._transaction() as broker_ledger:
                principal = await broker_ledger.authenticate(goal_id, token)
                span.set_attribute("invocation_id", str(principal.invocation_id))
                ledger = RunnerLedger(broker_ledger.connection)
                if tool == "wait_approval":
                    children = await ledger.wait_approval(principal, effect)
                else:
                    children = await ledger.wait_owner(principal, effect)
                status = "" if isinstance(children, BrokerRefused) else "waiting"
            if isinstance(children, BrokerRefused):
                raise children
        await self._terminate_all(children)
        self._terminate_later(principal.invocation_id)
        return status

    async def _expire_waits(self) -> None:
        """End each waiting goal whose envelope expired without firing; none waits forever."""
        async with self._txn() as ledger:
            expired = await ledger.expired_waits()
        for goal_id in expired:
            try:
                async with self._txn() as ledger:
                    await ledger.expire_wait(goal_id)
            except Exception:
                logger.exception("goal runner wait expiry failed goal=%s", goal_id)

    async def _resolve_approvals(self) -> None:
        """Continue or end each goal whose waited-on effect has settled (RFC 0038 S2.2).

        An approved effect that ran wakes one successor summary lead through the same wake
        admission a condition fire uses; one the owner or domain ended (rejected, expired,
        cancelled) ends the goal ``partial`` and launches nothing. A wake that cannot be
        admitted (refused for any reason but Stop, or mapped to no envelope generation) ends
        the goal ``failed``: nothing may loop silently until the deadline.
        """
        async with self._txn() as ledger:
            goals = await ledger.approval_waits()
        for goal_id in goals:
            try:
                await self._resolve_approval(goal_id)
            except Exception:
                logger.exception("goal runner approval resolution failed goal=%s", goal_id)

    async def _resolve_approval(self, goal_id: UUID) -> None:
        try:
            await self.broker.sync_parked(goal_id)
        except BrokerRefused:
            logger.warning("goal runner parked sync unavailable goal=%s", goal_id)
        async with self._txn() as ledger:
            decided = await ledger.decided_approvals(goal_id)
        if decided is None:
            return
        envelope_id, effect_id, status, instance_id = decided
        if status in ("rejected", "expired", "cancelled"):
            async with self._txn() as ledger:
                await ledger.end_approval_wait(goal_id, envelope_id, "partial")
            return
        key = f"approval:{envelope_id}:{effect_id}:{status}"
        fire = FireIntent(
            fire_key=uuid5(NAMESPACE_OID, key),
            instance_id=instance_id or f"approval:{effect_id}",
            generation=1,
            event_id=uuid5(NAMESPACE_OID, f"event:{key}"),
            reason=f"approval:{status}",
            fired_at=datetime.now(UTC),
            snapshot_generation=0,
            cursors={},
            label=Label.owner(),
        )
        result = await self.admit_wake(fire)
        if result.state == "unmapped":
            # The envelope's generation names no instance this fire maps to: nothing can ever
            # wake it, so it ends rather than waiting for its deadline.
            logger.error(
                "goal runner approval wake unmapped goal=%s envelope=%s instance=%s",
                goal_id,
                envelope_id,
                fire.instance_id,
            )
        if result.state == "unmapped" or (
            result.state == "not_admitted" and result.reason != "stopped"
        ):
            async with self._txn() as ledger:
                await ledger.end_approval_wait(goal_id, envelope_id, "failed")

    async def _launch_wakes(self) -> None:
        async with self._txn() as ledger:
            await ledger.lapse_wakes()
            due = await ledger.wakes_due()
        for goal_id, intent_id in due:
            try:
                async with self._txn() as ledger:
                    claim = await ledger.claim_wake(
                        goal_id, intent_id, "goal-runner", self.config.wake_claim_seconds
                    )
            except Exception:
                logger.exception("goal runner wake claim failed intent=%s", intent_id)
                continue
            if claim is not None:
                self._spawn(
                    _Plan(goal_id, "lead", self.ladders.first("lead"), 0, summary=True, wake=claim)
                )

    async def supervise_once(self) -> None:
        """One sweep: unlaunched admissions, lapsed leases, deadlines, ended and settling goals."""
        async with self._txn() as ledger:
            unlaunched = await ledger.unlaunched(self.config.resume_grace_seconds)
        for goal_id in unlaunched:
            # A duplicate of a start still in flight is a no-op (lead_wanted).
            self.start_goal(goal_id)
        async with self._txn() as ledger:
            lapsed = await ledger.lapsed()
        for goal_id, invocation_id in lapsed:
            async with self._txn() as ledger:
                expired = await ledger.expire(goal_id, invocation_id)
            if expired is None:
                continue
            await self._terminate(invocation_id)
            if expired.recover:
                with _tracer.start_as_current_span(
                    "goal.recover", record_exception=False, set_status_on_exception=False
                ) as span:
                    span.set_attribute("goal_id", str(goal_id))
                    span.set_attribute("invocation_id", str(invocation_id))
                tier = expired.tier or self.ladders.first(expired.role)
                self._spawn(_Plan(goal_id, expired.role, tier, 0, summary=True))
        async with self._txn() as ledger:
            overdue = await ledger.overdue()
        for goal_id, invocation_id in overdue:
            async with self._txn() as ledger:
                ended = await ledger.end(goal_id, invocation_id, "timed_out", "deadline")
            if ended:
                await self._terminate(invocation_id)
        async with self._txn() as ledger:
            fence = await ledger.goals_to_fence()
        for goal_id, status in fence:
            async with self._txn() as ledger:
                if status:
                    ended = await ledger.end_goal(goal_id, status, "budget_exhausted")
                else:
                    ended = await ledger.fence_ended(goal_id)
            await self._terminate_all(ended)
        async with self._txn() as ledger:
            leaderless = await ledger.leaderless(self.config.resume_grace_seconds)
        for item in leaderless:
            await self._resume(item)
        async with self._txn() as ledger:
            uncancelled = await ledger.stopped_with_parked()
        for goal_id in uncancelled:
            await self._cancel_parked(goal_id)
        async with self._txn() as ledger:
            settling = await ledger.settling()
        for goal_id in settling:
            try:
                await self.broker.sync_parked(goal_id)
            except BrokerRefused:
                logger.warning("goal runner parked sync unavailable goal=%s", goal_id)
            async with self._txn() as ledger:
                await ledger.resolve(goal_id)
        await self._expire_waits()
        # Wakes last: a fire that keeps failing must never starve the stages above.
        await self._resolve_approvals()
        await self._admit_fires()
        await self._launch_wakes()
        await self._report_owner_failures()

    async def _report_owner_failures(self) -> None:
        """Tell the owner about requests that failed without any delivery; never raises."""
        if self.owner_failures is None:
            return
        try:
            await self.owner_failures.sweep()
        except Exception:
            logger.exception("goal runner owner failure report failed")

    async def feed_conditions(self) -> None:
        """Feed each waiting goal's own decisions, settled effects and unread owner replies to its
        condition instance.

        RFC 0038 S2.2 / S4 (bu-dhloi1.305 PR2). For every waiting goal with an open conditions
        envelope, the domain's decisions are synced onto its parked effects first (as the
        approval wait does), then the goal's own events (``events_for``) are evaluated against
        the envelope's current instance, and a fire is admitted as the wait's wake at once (the
        ``_admit_fires`` sweep completes any that fails here).

        Dark: ``supervise_once`` does not call this. A feed-triggered wake is third-party
        sourced, and its lead would hold the envelope's full waiting grant; under the
        bu-dhloi1.305 ruling it is wired live only once PR5 narrows such wakes by label.
        """
        async with self._txn() as ledger:
            waits = await ledger.condition_waits()
        evaluator = ConditionEvaluator(PostgresConditionStore(self.broker.pool))
        for goal_id, instance_id in waits:
            try:
                await self.broker.sync_parked(goal_id)
            except BrokerRefused:
                logger.warning("goal runner parked sync unavailable goal=%s", goal_id)
            async with self._txn() as ledger:
                effects = await ledger.own_effects(goal_id)
                replies = await ledger.own_replies(goal_id)
            try:
                outcome = await evaluator.evaluate(
                    instance_id, events_for(effects, replies), now=datetime.now(UTC)
                )
            except KeyError:
                continue  # a generation id the evaluator never registered
            except ConditionsUnavailable:
                logger.warning("goal runner conditions feed unavailable goal=%s", goal_id)
                continue
            if outcome.intent is not None:
                await self._admit_guarded(outcome.intent)

    async def _admit_fires(self) -> None:
        """Admit the fires the evaluator committed that no wake intent answers yet.

        One fire's failure is logged and counted, never raised: a fire that fails
        ``_ADMIT_ATTEMPTS`` sweeps in a row is closed with a terminal ``admit_error`` receipt.
        """
        async with self._txn() as ledger:
            fires = await WakeAdmission(ledger.connection).unadmitted(
                self.config.wake_grace_seconds
            )
        for fire in fires:
            await self._admit_guarded(fire)

    async def _admit_guarded(self, fire: FireIntent) -> WakeResult | None:
        try:
            result = await self.admit_wake(fire)
        except Exception:
            failures = self._admit_failures.get(fire.fire_key, 0) + 1
            self._admit_failures[fire.fire_key] = failures
            logger.exception("goal runner wake admission failed fire=%s", fire.fire_key)
            if failures >= _ADMIT_ATTEMPTS:
                try:
                    async with self._txn() as ledger:
                        await WakeAdmission(ledger.connection).abandon(fire)
                    del self._admit_failures[fire.fire_key]
                except Exception:
                    logger.exception("goal runner could not close fire=%s", fire.fire_key)
            return None
        self._admit_failures.pop(fire.fire_key, None)
        return result

    async def _resume(self, item: Leaderless) -> None:
        """Finish what a runner that died between ending a lead and its successor began (D14).

        A lapsed lead is replaced at the next epoch; an escalated one by a lead
        on the next ladder tier. A lead that failed at runtime gets its one
        relaunch only if it was not itself a relaunch and the broker recorded no
        call of its token. Anything else ends the goal as the runner would have.
        """
        tier = item.tier or self.ladders.first(item.role)
        plan: _Plan | None = None
        if item.end_reason == "lease_lapsed":
            plan = _Plan(item.goal_id, item.role, tier, 0, summary=True)
        elif item.end_reason == "launch_failed":
            # The host had no room: offer the same lead again until the bound.
            waited = item.deferred_seconds or 0.0
            if waited <= self.config.busy_defer_seconds:
                plan = _Plan(
                    item.goal_id,
                    item.role,
                    tier,
                    0,
                    summary=True,
                    relaunch_spent=item.relaunch_spent,
                )
        elif item.end_reason == "escalated":
            target = await next_tier(self.catalog_pool, self.ladders, item.role, tier)
            if target is not None:
                plan = _Plan(item.goal_id, item.role, target, 0, summary=True)
        elif item.end_reason == "runtime_failure" and item.relaunch_of is None:
            async with self._txn() as ledger:
                calls = await ledger.recorded_calls(item.goal_id, item.lead_id)
            if not calls:
                plan = _Plan(
                    item.goal_id, item.role, tier, 0, summary=True, relaunch_of=item.lead_id
                )
        with _tracer.start_as_current_span(
            "goal.resume", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(item.goal_id))
            span.set_attribute("invocation_id", str(item.lead_id))
            if plan is not None:
                span.set_attribute("tier", plan.tier)
                self._spawn(plan)
                return
            exhausted = item.end_reason in ("turns_exhausted", "tokens_exhausted")
            async with self._txn() as ledger:
                fenced = await ledger.end_leaderless(
                    item.goal_id,
                    item.lead_id,
                    "budget_exhausted" if exhausted else "failed",
                    "budget_exhausted" if exhausted else "goal_ended",
                )
        await self._terminate_all(fenced)

    async def record_usage(
        self,
        goal_id: UUID,
        invocation_id: UUID,
        *,
        turns: int = 0,
        tokens: int = 0,
        spend: Decimal = Decimal(0),
    ) -> str:
        """Meter usage relayed from the runtime between tool calls (D10)."""
        async with self._txn() as ledger:
            verdict, ended = await ledger.meter(
                goal_id, invocation_id, turns=turns, tokens=tokens, spend=spend
            )
        await self._terminate_all(ended)
        return verdict


def _host_tokens(usage: Mapping[str, Any]) -> int:
    """Tokens a settled run used: input plus output. Cached input is part of input."""
    return sum(
        value
        for key in ("input_tokens", "output_tokens")
        if type(value := usage.get(key)) is int and value >= 0
    )


def _uuid(value: Any) -> UUID:
    try:
        parsed = UUID(value) if type(value) is str else None
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        raise BrokerRefused(Reason.POLICY)
    return parsed


def _aware(value: Any) -> datetime:
    """An ISO 8601 timestamp with its offset; a naive or unparseable one is POLICY."""
    try:
        parsed = datetime.fromisoformat(value) if type(value) is str else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() is None:
        raise BrokerRefused(Reason.POLICY)
    return parsed


def _uuid_list(value: Any) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if type(value) is not list or len(value) > 256:
        raise BrokerRefused(Reason.POLICY)
    return tuple(_uuid(item) for item in value)
