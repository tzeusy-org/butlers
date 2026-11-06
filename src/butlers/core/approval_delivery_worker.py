"""Fenced, notification-only approval delivery recovery loop.

The core daemon owns this task's lifecycle while the Approvals module owns the
schema-local repository and deterministic renderer.  The worker deliberately
receives no approval operations or executor capability.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

HandoffClass = Literal["confirmed", "safe_retry", "ambiguous"]

# Versioned closed reason vocabulary (REQ-approval-delivery-intent-recovery-002).
# Durable rows, API projections, metric labels and structured logs share it.
SAFE_REASON_CODES: frozenset[str] = frozenset(
    {
        "quiet_hours",
        "owner_recipient_unavailable",
        "callback_secret_unavailable",
        "transport_unavailable",
        "provider_preflight_failed",
        "provider_outcome_unknown",
        "action_approved",
        "action_rejected",
        "action_expired",
        "action_abandoned",
        "action_cancelled",
        "defer_rescheduled",
        "cohort_empty",
    }
)

ObservationStage = Literal["scan", "claim", "handoff"]
# Closed outcome vocabulary per stage. A ``handoff`` outcome is recorded only
# after the repository write it names has committed; refusals and lease loss
# are their own outcomes, never a success.
OBSERVATION_OUTCOMES: dict[str, frozenset[str]] = {
    "scan": frozenset({"idle", "failed"}),
    "claim": frozenset({"send", "reconcile"}),
    "handoff": frozenset(
        {
            "confirmed",
            "safe_retry",
            "ambiguous",
            "subject_unavailable",
            "start_refused",
            "write_refused",
            "lease_lost",
        }
    ),
}

approval_delivery_worker_outcomes = get_or_create_counter(
    "approval_delivery_worker_outcomes_total",
    "Approval delivery recovery scan/claim/handoff outcomes; closed labels only.",
    labelnames=["schema", "stage", "outcome", "reason"],
)

DeliveryObserver = Callable[[str, str, str, str], None]


def _safe_reason_label(reason_code: str | None) -> str:
    if reason_code is None:
        return "none"
    return reason_code if reason_code in SAFE_REASON_CODES else "invalid"


def record_delivery_outcome(schema: str, stage: str, outcome: str, reason: str) -> None:
    """Default observer: closed-label counter plus a content-blind log line.

    Labels never carry presentation/action identifiers, recipients, callback
    material, provider references or exception text.
    """
    if outcome not in OBSERVATION_OUTCOMES.get(stage, frozenset()):
        stage, outcome = "scan", "failed"
    approval_delivery_worker_outcomes.labels(
        schema=schema, stage=stage, outcome=outcome, reason=reason
    ).inc()
    if (stage, outcome) == ("scan", "idle"):
        return
    logger.info(
        "approval delivery %s outcome %s (reason %s)",
        stage,
        outcome,
        reason,
        extra={
            "approval_delivery_schema": schema,
            "approval_delivery_stage": stage,
            "approval_delivery_outcome": outcome,
            "approval_delivery_reason": reason,
        },
    )


class ClaimLeaseLost(RuntimeError):
    """The worker no longer owns the generation and must not write its result."""


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """One generation-specific fenced presentation claim."""

    presentation_id: Any
    presentation_generation: int
    presentation_key: str
    presentation_mode: Literal["single", "burst_digest"]
    subject_kind: Literal["action", "cohort"]
    subject_key: str
    claim_token: Any
    claim_fence: int
    reconcile_only: bool


@dataclass(frozen=True, slots=True)
class HandoffResult:
    """Safe result returned by the future trusted Messenger boundary."""

    classification: HandoffClass
    reason_code: str | None = None
    provider_reference: str | None = None


class ApprovalDeliveryRepository(Protocol):
    """Notification-only writes available to the core-owned worker."""

    async def cancel_ineligible_presentations(self) -> int: ...

    async def claim_next(self) -> DeliveryClaim | None: ...

    async def load_render_subject(self, claim: DeliveryClaim) -> Any | None: ...

    async def record_prestart_retry(self, claim: DeliveryClaim, reason_code: str) -> bool: ...

    async def mark_handoff_started(self, claim: DeliveryClaim) -> bool: ...

    async def heartbeat(self, claim: DeliveryClaim) -> bool: ...

    async def complete_handoff(self, claim: DeliveryClaim, result: HandoffResult) -> bool: ...

    # Optional (bu-dhloi1.13): ``prepare_owner_delivery(claim, owner_recipient)`` returns
    # the card's single-use owner-delivery tokens, or ``None`` while that path is off.


class ApprovalDeliveryRenderer(Protocol):
    """Approvals-owned deterministic rendering boundary."""

    def render_single(
        self,
        subject: Any,
        *,
        owner_recipient: str,
        callback_secret: str,
        owner_tokens: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def render_digest(
        self,
        subject: Any,
        *,
        owner_recipient: str,
    ) -> dict[str, Any]: ...


class ApprovalDeliveryRuntime(Protocol):
    """Narrow future transport boundary; no generic notification controls."""

    async def resolve_verified_owner_recipient(self) -> str | None: ...

    async def resolve_callback_secret(self) -> str | None: ...

    async def handoff(
        self,
        claim: DeliveryClaim,
        envelope: dict[str, Any],
    ) -> HandoffResult: ...

    async def reconcile(self, claim: DeliveryClaim) -> HandoffResult: ...


class ApprovalDeliveryWorker:
    """Process local approval presentations without domain-action authority."""

    def __init__(
        self,
        repository: ApprovalDeliveryRepository,
        renderer: ApprovalDeliveryRenderer,
        runtime: ApprovalDeliveryRuntime,
        *,
        poll_interval_s: float = 5.0,
        heartbeat_interval_s: float = 10.0,
        schema: str = "unknown",
        observe: DeliveryObserver = record_delivery_outcome,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat_interval_s must be positive")
        self._repository = repository
        self._renderer = renderer
        self._runtime = runtime
        self._poll_interval_s = poll_interval_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._schema = schema
        self._observe_outcome = observe

    def _observe(self, stage: str, outcome: str, reason_code: str | None = None) -> None:
        """Record one closed outcome; observation can never alter the outcome."""
        try:
            self._observe_outcome(self._schema, stage, outcome, _safe_reason_label(reason_code))
        except Exception:
            pass

    async def _complete(self, claim: DeliveryClaim, result: HandoffResult) -> None:
        if await self._repository.complete_handoff(claim, result):
            self._observe("handoff", result.classification, result.reason_code)
        else:
            self._observe("handoff", "write_refused")

    async def _prestart_retry(self, claim: DeliveryClaim, reason_code: str) -> None:
        if await self._repository.record_prestart_retry(claim, reason_code):
            self._observe("handoff", "safe_retry", reason_code)
        else:
            self._observe("handoff", "write_refused")

    async def _await_with_heartbeat(self, claim: DeliveryClaim, operation: Awaitable[Any]) -> Any:
        """Keep authority live during an external await and renew before CAS."""
        task = asyncio.ensure_future(operation)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self._heartbeat_interval_s)
                if done:
                    if not await self._repository.heartbeat(claim):
                        try:
                            task.result()
                        except BaseException:
                            pass
                        raise ClaimLeaseLost
                    return await task
                if not await self._repository.heartbeat(claim):
                    raise ClaimLeaseLost
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise

    async def process_one(self) -> bool:
        """Process at most one presentation; return whether work was claimed."""
        await self._repository.cancel_ineligible_presentations()
        claim = await self._repository.claim_next()
        if claim is None:
            self._observe("scan", "idle")
            return False
        self._observe("claim", "reconcile" if claim.reconcile_only else "send")

        if claim.reconcile_only:
            try:
                result = await self._await_with_heartbeat(claim, self._runtime.reconcile(claim))
            except ClaimLeaseLost:
                self._observe("handoff", "lease_lost")
                return True
            except Exception:  # an unknown post-start result is never retry permission
                result = HandoffResult(
                    classification="ambiguous",
                    reason_code="provider_outcome_unknown",
                )
            await self._complete(claim, result)
            return True

        subject = await self._repository.load_render_subject(claim)
        if subject is None:
            self._observe("handoff", "subject_unavailable")
            return True

        try:
            owner_recipient = await self._runtime.resolve_verified_owner_recipient()
        except Exception:
            owner_recipient = None
        if not isinstance(owner_recipient, str) or not owner_recipient.strip():
            await self._prestart_retry(claim, "owner_recipient_unavailable")
            return True
        owner_recipient = owner_recipient.strip()

        try:
            if claim.presentation_mode == "single":
                try:
                    callback_secret = await self._runtime.resolve_callback_secret()
                except Exception:
                    callback_secret = None
                if not isinstance(callback_secret, str) or not callback_secret:
                    await self._prestart_retry(claim, "callback_secret_unavailable")
                    return True
                # Canonical owner delivery (bu-dhloi1.13, default off): the repository
                # opens the subject and this card's presentation and returns its
                # single-use tokens, which replace the legacy HMAC buttons.
                prepare: Callable[..., Awaitable[Mapping[str, str] | None]] | None = getattr(
                    self._repository, "prepare_owner_delivery", None
                )
                owner_tokens = await prepare(claim, owner_recipient) if prepare else None
                if owner_tokens is None:
                    envelope = self._renderer.render_single(
                        subject,
                        owner_recipient=owner_recipient,
                        callback_secret=callback_secret,
                    )
                else:
                    envelope = self._renderer.render_single(
                        subject,
                        owner_recipient=owner_recipient,
                        callback_secret=callback_secret,
                        owner_tokens=owner_tokens,
                    )
            else:
                envelope = self._renderer.render_digest(
                    subject,
                    owner_recipient=owner_recipient,
                )
        except Exception:
            await self._prestart_retry(claim, "provider_preflight_failed")
            return True

        if not await self._repository.mark_handoff_started(claim):
            self._observe("handoff", "start_refused")
            return True

        try:
            result = await self._await_with_heartbeat(claim, self._runtime.handoff(claim, envelope))
        except ClaimLeaseLost:
            self._observe("handoff", "lease_lost")
            return True
        except Exception:
            result = HandoffResult(
                classification="ambiguous",
                reason_code="provider_outcome_unknown",
            )
        await self._complete(claim, result)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stopped, preserving cancellation as the shutdown fence."""
        while not stop.is_set():
            try:
                worked = await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception text can carry provider or SQL detail; keep the
                # structured record to the closed outcome and the error class.
                self._observe("scan", "failed")
                logger.warning(
                    "approval delivery recovery scan failed (%s)",
                    type(exc).__name__,
                )
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_s)
            except TimeoutError:
                pass


__all__ = [
    "ApprovalDeliveryRenderer",
    "ApprovalDeliveryRepository",
    "ApprovalDeliveryRuntime",
    "ApprovalDeliveryWorker",
    "ClaimLeaseLost",
    "DeliveryClaim",
    "HandoffClass",
    "HandoffResult",
    "OBSERVATION_OUTCOMES",
    "SAFE_REASON_CODES",
    "record_delivery_outcome",
    "start_approval_delivery_worker",
    "stop_approval_delivery_worker",
]


async def start_approval_delivery_worker(daemon: Any) -> bool:
    """Start one loop only when the active module's server-held flag enables it."""
    existing = getattr(daemon, "_approval_delivery_task", None)
    if existing is not None and not existing.done():
        return False
    runtime = getattr(daemon, "_approval_delivery_runtime", None)
    state = getattr(daemon, "_module_runtime_states", {}).get("approvals")
    if runtime is None or state is None or state.health != "active" or not state.enabled:
        return False
    module = next(
        (item for item in getattr(daemon, "_modules", ()) if item.name == "approvals"),
        None,
    )
    rollout_enabled = getattr(module, "approval_delivery_worker_enabled", None)
    if not callable(rollout_enabled):
        return False
    try:
        if not await rollout_enabled():
            return False
    except Exception:
        logger.warning("approval delivery worker rollout check failed closed")
        return False
    components = getattr(module, "approval_delivery_worker_components", None)
    if not callable(components):
        return False
    repository, renderer = components()
    stop = asyncio.Event()
    worker = ApprovalDeliveryWorker(repository, renderer, runtime, schema=daemon.config.name)
    daemon._approval_delivery_stop = stop
    daemon._approval_delivery_task = asyncio.create_task(
        worker.run(stop),
        name=f"approval-delivery-{daemon.config.name}",
    )
    return True


async def stop_approval_delivery_worker(daemon: Any) -> None:
    """Cancel the loop; a post-start interruption remains reconcilable."""
    stop = getattr(daemon, "_approval_delivery_stop", None)
    task = getattr(daemon, "_approval_delivery_task", None)
    if stop is not None:
        stop.set()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    daemon._approval_delivery_stop = None
    daemon._approval_delivery_task = None
