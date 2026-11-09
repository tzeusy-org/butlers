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
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from opentelemetry import trace

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.service import Broker
from butlers.core.model_call import AttemptOutcome, AttemptSink, CallSpec, Candidate, call_model

from .contracts import (
    Brief,
    GoalEnded,
    HostOutcome,
    InvocationBudgetExhausted,
    Launch,
    LaunchContext,
    MinimalComposer,
    PromptComposer,
    Role,
    SessionHost,
)
from .delegation import DelegationRequest, parse_delegation
from .ledger import Leaderless, RunnerLedger, Started
from .models import ModelLadders, next_tier

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
    ):
        self.broker = broker
        self.host = host
        self.catalog_pool = catalog_pool
        self.sink = sink if sink is not None else AttemptSink(catalog_pool)
        self.ladders = ladders or ModelLadders()
        self.composer = composer or MinimalComposer()
        self.config = config or RunnerConfig()
        self.kernel = kernel
        self._tasks: set[asyncio.Task] = set()

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
        """Wait for every supervised invocation (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

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
            return await ledger.open_lead(
                plan.goal_id,
                role=plan.role,
                tier=plan.tier,
                model_id=candidate.model_id,
                max_turns=self.config.lead_max_turns,
                max_tokens=self.config.lead_max_tokens,
                lease_seconds=lease,
                relaunch_of=attempts[-1] if attempts else plan.relaunch_of,
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
            max_relaunches=0 if plan.relaunch_of is not None else 1,
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
                logger.info(
                    "goal runner launch not opened goal=%s role=%s reason=%s",
                    plan.goal_id,
                    plan.role,
                    unopened[-1],
                )
                return
            await self._after_failure(plan, attempts, error)
            return
        await self._after_exit(plan, attempts[-1])

    async def _launch(self, plan: _Plan, started: Started, candidate: Candidate) -> HostOutcome:
        summary = None
        if plan.summary:
            async with self._txn() as ledger:
                summary = await ledger.summary(plan.goal_id)
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
        with _tracer.start_as_current_span(
            "goal.stop", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            async with self._txn() as ledger:
                stopped, live = await ledger.stop(goal_id)
            await self._terminate_all(live)
            if stopped:
                await self._cancel_parked(goal_id)
            return len(live) if stopped else 0

    async def _cancel_parked(self, goal_id: UUID) -> None:
        try:
            await self.broker.cancel_parked(goal_id)
        except BrokerRefused:
            # A stopped goal never executes an approval (begin_approved); the
            # cancellation itself is retried by the next sweep.
            logger.warning("goal runner parked-effect cancel unavailable goal=%s", goal_id)

    async def supervise_once(self) -> None:
        """One sweep: lapsed leases, deadlines, ended goals, settling goals."""
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


def _uuid(value: Any) -> UUID:
    try:
        parsed = UUID(value) if type(value) is str else None
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        raise BrokerRefused(Reason.POLICY)
    return parsed


def _uuid_list(value: Any) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if type(value) is not list or len(value) > 256:
        raise BrokerRefused(Reason.POLICY)
    return tuple(_uuid(item) for item in value)
