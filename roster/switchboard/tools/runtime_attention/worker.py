"""The runtime-attention delivery worker.

The worker is the only thing that turns an outbox row into an operator-visible
message, and it is built around one rule from REQ-core-notify-027: *external
transport truth wins*.  It claims durably before sending, rechecks its fence
before every attempt, retries only an outcome that is **proven** not to have
been attempted, and treats anything ambiguous as terminal.

``SwitchboardModule.on_startup`` (``roster/switchboard/modules/__init__.py``)
constructs and schedules this worker at daemon startup, polling the outbox on
a fixed interval for the lifetime of the process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

from butlers.metrics_registry import get_or_create_counter
from butlers.tools.switchboard.notification.deliver import deliver
from butlers.tools.switchboard.routing.transport import (
    CONFIRMED,
    RECIPIENT_UNAVAILABLE,
    TRANSPORT_TIMEOUT,
    TransportOutcome,
    TransportResult,
    classify_transport_exception,
    transport_result_from_envelope,
)
from butlers.tools.switchboard.runtime_attention.outbox import (
    MAX_TRANSPORT_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    TRANSPORT_DEADLINE_SECONDS,
    OutboxEpisode,
    RuntimeAttentionOutbox,
    ServiceLease,
)

logger = logging.getLogger(__name__)

Transport = Callable[[OutboxEpisode], Awaitable[TransportResult]]
Sleeper = Callable[[float], Awaitable[None]]

# Typed outcome counters only. No recipient, body, credential, or provider
# string is a label value here, and none can become one: every label is drawn
# from the closed transport vocabulary.
_delivery_total = get_or_create_counter(
    "switchboard_runtime_attention_delivery_total",
    "Runtime-attention delivery attempts by typed transport outcome.",
    labelnames=["outcome", "error_detail"],
)
_recovered_total = get_or_create_counter(
    "switchboard_runtime_attention_recovered_total",
    "Runtime-attention claims fenced to uncertain by a recovery sweep.",
)
_fenced_total = get_or_create_counter(
    "switchboard_runtime_attention_fenced_total",
    "Runtime-attention transitions refused because the claim was no longer current.",
)
_lease_lost_total = get_or_create_counter(
    "switchboard_runtime_attention_lease_lost_total",
    "Delivery cycles cut short because the sole service lease was lost mid-cycle.",
)
_record_failed_total = get_or_create_counter(
    "switchboard_runtime_attention_record_failed_total",
    "Terminal transitions that raised after transport already returned an outcome.",
)


@dataclass(frozen=True, slots=True)
class DeliveryCycle:
    """What one worker pass did. Every field is a count or a typed code."""

    lease_acquired: bool
    recovered: int = 0
    outcomes: tuple[str, ...] = ()

    @property
    def delivered(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome == TransportOutcome.CONFIRMED)


class RuntimeAttentionDeliveryWorker:
    """Drives outbox episodes through at-most-once external delivery."""

    def __init__(
        self,
        repository: RuntimeAttentionOutbox,
        transport: Transport,
        *,
        max_episodes: int = 10,
        sleep: Sleeper | None = None,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._max_episodes = max_episodes
        self._sleep = sleep or asyncio.sleep

    async def run_once(self) -> DeliveryCycle:
        """Run one delivery pass under the sole service lease.

        Returning ``lease_acquired=False`` is a normal outcome, not an error:
        it means another instance is alive, and a second delivery service is
        precisely what must not happen.
        """
        lease = await self._repository.acquire_service_lease()
        if lease is None:
            return DeliveryCycle(lease_acquired=False)
        try:
            recovered = await self._recover(lease)
            outcomes: list[str] = []
            for _ in range(self._max_episodes):
                episode = await self._repository.claim_next_pending(lease)
                if episode is None:
                    break
                result = await self._deliver(episode)
                if result is not None:
                    outcomes.append(str(result.outcome))
                if not await self._repository.renew_service_lease(lease):
                    # A successor already holds the service lease. Claiming
                    # again would put a second delivery service on the wire,
                    # which is the single thing this lease exists to prevent.
                    _lease_lost_total.inc()
                    logger.warning(
                        "runtime-attention delivery lease lost mid-cycle after %d episode(s); "
                        "stopping before claiming another",
                        len(outcomes),
                    )
                    break
            return DeliveryCycle(lease_acquired=True, recovered=recovered, outcomes=tuple(outcomes))
        finally:
            await self._repository.release_service_lease(lease)

    async def _recover(self, lease: ServiceLease) -> int:
        """Fence claims whose holder is provably gone. Sends nothing."""
        recovered = 0
        for claim in await self._repository.list_recoverable(lease):
            if await self._repository.fence_stale_claim(claim, lease):
                recovered += 1
                _recovered_total.inc()
                logger.warning(
                    "runtime-attention claim %s fenced to uncertain by recovery "
                    "(prior lease epoch %s); it will not be retried",
                    claim.id,
                    claim.delivery_lease_epoch,
                )
        return recovered

    async def _deliver(self, episode: OutboxEpisode) -> TransportResult | None:
        """Deliver one claimed episode, or ``None`` if the claim was fenced."""
        result = TRANSPORT_TIMEOUT
        for attempt in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
            # Re-read the fence before *every* attempt, not just the first: a
            # recovery sweep may have declared this claim dead while we were
            # backing off, and a fenced claimant must put nothing on the wire.
            if not await self._repository.claim_is_current(episode):
                _fenced_total.inc()
                logger.warning(
                    "runtime-attention episode %s abandoned: claim no longer current",
                    episode.id,
                )
                return None

            result = await self._attempt(episode)
            if result.outcome is TransportOutcome.CONFIRMED or not result.retryable:
                break
            if attempt < MAX_TRANSPORT_ATTEMPTS:
                await self._sleep(RETRY_BACKOFF_SECONDS[attempt - 1])

        _delivery_total.labels(
            outcome=str(result.outcome),
            error_detail=str(result.error_detail) if result.error_detail else "none",
        ).inc()
        try:
            await self._record(episode, result)
        except Exception as exc:  # noqa: BLE001 - reduced to a typed log below
            # Transport has already returned its outcome; the send either
            # happened or provably did not.  A bookkeeping failure here must
            # not abort the cycle or strand the remaining episodes -- recovery
            # observes the still-``sending`` row and fences it.  The outcome is
            # still reported so the cycle's counts stay truthful.  Only the
            # exception *type* is logged, never its message.
            _record_failed_total.inc()
            logger.warning(
                "runtime-attention episode %s outcome %s could not be recorded (%s)",
                episode.id,
                result.outcome,
                type(exc).__name__,
            )
        return result

    async def _attempt(self, episode: OutboxEpisode) -> TransportResult:
        """One bounded transport attempt, reduced to a typed outcome.

        An unrecognized exception classifies as uncertain rather than
        retryable: a send this code cannot explain may still have arrived.
        ``CancelledError`` is not an outcome and is left to propagate.
        """
        try:
            async with asyncio.timeout(TRANSPORT_DEADLINE_SECONDS):
                return await self._transport(episode)
        except Exception as exc:  # noqa: BLE001 - every failure becomes typed
            return classify_transport_exception(exc)

    async def _record(self, episode: OutboxEpisode, result: TransportResult) -> None:
        """Apply the terminal transition the outcome implies."""
        if result.outcome is TransportOutcome.CONFIRMED:
            applied = await self._repository.mark_sent(episode)
        elif result.outcome is TransportOutcome.UNCERTAIN:
            applied = await self._repository.mark_uncertain(episode)
        else:
            # Proven not-attempted (retries exhausted) and provider-rejected are
            # both terminal *and* known not to have delivered.
            applied = await self._repository.mark_failed(episode)

        if not applied:
            _fenced_total.inc()
            logger.warning(
                "runtime-attention episode %s outcome %s not recorded: claim was fenced",
                episode.id,
                result.outcome,
            )
            return
        logger.info(
            "runtime-attention episode %s delivery outcome=%s error_detail=%s",
            episode.id,
            result.outcome,
            result.error_detail or "none",
        )


def _episode_payload(episode: OutboxEpisode) -> dict[str, Any]:
    """Return the episode payload as a mapping.

    asyncpg hands back JSONB as text unless a codec is installed, and this
    worker must not depend on the caller having installed one.
    """
    payload = episode.payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _episode_message(episode: OutboxEpisode) -> str:
    """Build operator-facing text from allowlisted payload fields only.

    ``classification`` and ``door`` are the only free-ish fields the outbox
    accepts, and the payload CHECK constraint restricts them to a fixed set at
    write time — so nothing user-supplied can reach a message body here.
    """
    payload = _episode_payload(episode)
    classification = str(payload.get("classification", episode.source))
    door = payload.get("door")
    text = f"Runtime attention: {classification}."
    if isinstance(door, str) and door:
        text = f"{text} Open {door} to review."
    return text


def build_messenger_transport(
    pool: asyncpg.Pool,
    *,
    resolve_recipient: Callable[[], Awaitable[str | None]],
    channel: str = "telegram",
) -> Transport:
    """Build the Messenger-backed transport the worker sends through.

    Routing through ``deliver()`` rather than a channel connector is the point:
    RFC 0003 makes Switchboard the sole delivery boundary, so the worker has no
    business holding a Telegram or Messenger client of its own.

    An unresolvable recipient is proven not-attempted — nothing has left this
    process — and is therefore the one outcome the worker may retry.
    """

    async def _transport(episode: OutboxEpisode) -> TransportResult:
        recipient = await resolve_recipient()
        if not recipient:
            return RECIPIENT_UNAVAILABLE

        result = await deliver(
            pool,
            notify_request={
                "schema_version": "notify.v1",
                "origin_butler": "switchboard",
                "delivery": {
                    "intent": "send",
                    "channel": channel,
                    "recipient": recipient,
                    "message": _episode_message(episode),
                },
            },
            source_butler="switchboard",
        )
        transport = transport_result_from_envelope(result)
        if transport is not None:
            return transport
        # A pre-vocabulary envelope (a validation refusal, say) never reached
        # the wire, so it is safely not-attempted.
        return CONFIRMED if result.get("status") == "sent" else RECIPIENT_UNAVAILABLE

    return _transport
