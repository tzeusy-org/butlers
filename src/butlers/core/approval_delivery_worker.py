"""Fenced, notification-only approval delivery recovery loop.

The core daemon owns this task's lifecycle while the Approvals module owns the
schema-local repository and deterministic renderer.  The worker deliberately
receives no approval operations or executor capability.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

HandoffClass = Literal["confirmed", "safe_retry", "ambiguous"]


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """One generation-specific fenced presentation claim."""

    presentation_id: Any
    presentation_generation: int
    presentation_key: str
    presentation_mode: Literal["single", "burst_digest"]
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


class ApprovalDeliveryRenderer(Protocol):
    """Approvals-owned deterministic rendering boundary."""

    def render_single(
        self,
        subject: Any,
        *,
        owner_recipient: str,
        callback_secret: str,
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
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        self._repository = repository
        self._renderer = renderer
        self._runtime = runtime
        self._poll_interval_s = poll_interval_s

    async def process_one(self) -> bool:
        """Process at most one presentation; return whether work was claimed."""
        await self._repository.cancel_ineligible_presentations()
        claim = await self._repository.claim_next()
        if claim is None:
            return False

        if claim.reconcile_only:
            try:
                result = await self._runtime.reconcile(claim)
            except Exception:  # an unknown post-start result is never retry permission
                result = HandoffResult(
                    classification="ambiguous",
                    reason_code="provider_outcome_unknown",
                )
            await self._repository.complete_handoff(claim, result)
            return True

        subject = await self._repository.load_render_subject(claim)
        if subject is None:
            return True

        try:
            owner_recipient = await self._runtime.resolve_verified_owner_recipient()
        except Exception:
            owner_recipient = None
        if not isinstance(owner_recipient, str) or not owner_recipient.strip():
            await self._repository.record_prestart_retry(claim, "owner_recipient_unavailable")
            return True
        owner_recipient = owner_recipient.strip()

        try:
            if claim.presentation_mode == "single":
                try:
                    callback_secret = await self._runtime.resolve_callback_secret()
                except Exception:
                    callback_secret = None
                if not isinstance(callback_secret, str) or not callback_secret:
                    await self._repository.record_prestart_retry(
                        claim, "callback_secret_unavailable"
                    )
                    return True
                envelope = self._renderer.render_single(
                    subject,
                    owner_recipient=owner_recipient,
                    callback_secret=callback_secret,
                )
            else:
                envelope = self._renderer.render_digest(
                    subject,
                    owner_recipient=owner_recipient,
                )
        except Exception:
            await self._repository.record_prestart_retry(claim, "provider_preflight_failed")
            return True

        if not await self._repository.mark_handoff_started(claim):
            return True

        try:
            result = await self._runtime.handoff(claim, envelope)
        except Exception:
            result = HandoffResult(
                classification="ambiguous",
                reason_code="provider_outcome_unknown",
            )
        await self._repository.complete_handoff(claim, result)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stopped, preserving cancellation as the shutdown fence."""
        while not stop.is_set():
            try:
                worked = await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("approval delivery recovery scan failed")
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
    "DeliveryClaim",
    "HandoffClass",
    "HandoffResult",
    "start_approval_delivery_worker",
    "stop_approval_delivery_worker",
]


async def start_approval_delivery_worker(daemon: Any) -> bool:
    """Start one loop only for an active local Approvals module and runtime.

    The delivery runtime remains deliberately absent until RFC 0023 task 4
    supplies the authenticated Messenger boundary, so landing this worker does
    not activate provider traffic.
    """
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
    components = getattr(module, "approval_delivery_worker_components", None)
    if not callable(components):
        return False
    repository, renderer = components()
    stop = asyncio.Event()
    worker = ApprovalDeliveryWorker(repository, renderer, runtime)
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
