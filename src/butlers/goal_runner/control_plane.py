"""The goal runtime in the control plane: admission, supervision and Stop (RFC 0038 M6).

``GoalRuntime`` is the one object trusted control-plane wiring builds around a
``GoalRunner``. It joins the three seams the runner leaves open:

* admission to launch: an admitted goal's lead starts as soon as admission
  commits; a goal whose start never ran (the process died in between) is started
  by the next sweep (``GoalRunner.supervise_once``);
* supervision: ``supervise_once`` runs on a fixed period for as long as the
  runtime is started, so lease lapse, deadlines, fencing, parked-cancellation
  retry and settling never depend on a launch being in flight;
* owner Stop: ``stop`` is the owner surface's single entry to ``GoalRunner.stop``.

Dark: the switchboard daemon starts a runtime only when trusted construction
gives it one, behind ``GoalRuntimeSwitch`` (default off) until the cutover.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.core.goal_admission import AdmissionResult, GoalAdmission, SourceRef

from .runner import GoalRunner

logger = logging.getLogger(__name__)

StopOutcome = Literal["stopped", "not_running"]


@dataclass(frozen=True, slots=True)
class GoalRuntimeSwitch:
    """The M6 switch for the goal runtime. Off until the cutover release."""

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class StopResult:
    """What an owner Stop did.

    ``stopped``: this call fenced the goal (its tokens are revoked, its live
    invocations were sent a terminate, parked effects are being cancelled).
    ``not_running``: the goal was already terminal or does not exist; its status
    is unchanged, though anything of it still live is fenced. ``terminated``
    counts the live invocations this call ended.
    """

    outcome: StopOutcome
    terminated: int


class GoalRuntime:
    """Admission, periodic supervision and owner Stop around one ``GoalRunner``."""

    def __init__(
        self,
        admission: GoalAdmission,
        runner: GoalRunner,
        *,
        switch: GoalRuntimeSwitch | None = None,
        supervise_every: float = 15.0,
    ) -> None:
        if not supervise_every > 0:
            raise ValueError("supervise_every must be positive")
        self.admission = admission
        self.runner = runner
        self.switch = switch or GoalRuntimeSwitch()
        self.supervise_every = supervise_every
        self._supervisor: asyncio.Task[None] | None = None

    # -- admission ----------------------------------------------------------

    async def admit(self, source: SourceRef, attempt_id: UUID) -> AdmissionResult:
        """Admit a submission and start the admitted goal's lead.

        Admission is idempotent per attempt, so a retried submission returns the
        same decision and starts the goal again; a duplicate start opens
        nothing. A ``joined`` reply is delivered to its goal's inbox by
        admission itself and starts nothing here.
        """
        if not self.switch.enabled:
            raise RuntimeError("goal runtime is switched off")
        result = await self.admission.admit_submission(source, attempt_id)
        if result.result == "admitted" and result.goal_id is not None:
            self.runner.start_goal(result.goal_id)
        return result

    # -- owner Stop ---------------------------------------------------------

    async def stop(self, goal_id: UUID) -> StopResult:
        """Owner Stop (D14): fence, revoke, terminate and cancel parked effects.

        Committed effects are never reported as undone: Stop ends what is live
        and cancels what is parked; the effects ledger keeps every receipt.
        """
        try:
            stopped, terminated = await self.runner.stop_goal(goal_id)
        except BrokerRefused as refused:
            if refused.reason is not Reason.GOAL:
                raise
            return StopResult("not_running", 0)  # no such goal
        return StopResult("stopped" if stopped else "not_running", terminated)

    # -- supervision --------------------------------------------------------

    def start(self) -> None:
        """Start the periodic supervision sweep (idempotent; refused when switched off)."""
        if not self.switch.enabled:
            raise RuntimeError("goal runtime is switched off")
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._supervisor = asyncio.create_task(
            self._supervise_forever(), name="goal-runtime-supervisor"
        )

    async def _supervise_forever(self) -> None:
        while True:
            try:
                await self.runner.supervise_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One failed sweep (a database blip) must not end supervision;
                # the next sweep re-reads everything it needs from the ledger.
                logger.warning("goal runtime supervision sweep failed", exc_info=True)
            await asyncio.sleep(self.supervise_every)

    async def close(self) -> None:
        """Stop supervising and abandon in-flight supervision (shutdown).

        Invocations are not terminated: their leases lapse and the next
        runtime's sweep fences and recovers them (D14).
        """
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        await self.runner.abandon()
