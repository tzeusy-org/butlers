"""Fenced-claim repository over ``public.runtime_attention_outbox``.

REQ-runtime-attention-outbox-002 asks for at-most-once delivery of runtime
attention.  The database already carries the structural half of that guarantee:
``runtime_attention_outbox_guard`` refuses any claim-identity rewrite, the RLS
policy demands ``SET ROLE butler_switchboard_rw``
(REQ-database-security-007), and ``butler_switchboard_rw`` holds no ``INSERT``
grant at all — which is why manual reissue is simply unavailable here rather
than merely unimplemented.

This module supplies the other half: every write is a *conditional* update
fenced on the exact claim that authorized it, so a transition can only land if
the claim that produced it is still the current one.

Two fences matter, and they are different things:

* the **claim fence** (``claim_token`` + ``claim_epoch``) proves *this* worker
  still owns the episode, and
* the **service-lease fence** (``delivery_lease_epoch``) proves no *other*
  worker is alive.

Recovery needs both.  Holding lease epoch ``E`` proves every row claimed at an
epoch below ``E`` has no live holder, because a live holder would still hold
the lease it claimed under.  Row age alone authorizes nothing: it only makes a
row eligible for *inspection* (:meth:`RuntimeAttentionOutbox.list_recoverable`),
and the transition that follows is still fenced on the prior claim
(:meth:`RuntimeAttentionOutbox.fence_stale_claim`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

# The single service lease, and the role the RLS policy insists on.
DELIVERY_LEASE_NAME = "runtime_attention_delivery"
SWITCHBOARD_ROLE = "butler_switchboard_rw"

# Fixed protocol bounds (bu-0uqgo.3 design). These are contract, not tuning:
# CLAIM_LEASE_SECONDS and STALE_SENDING_SECONDS agreeing is what makes an
# expired claim and a recoverable claim the same thing.
#
# Worst-case single-episode transport is bounded by MAX_TRANSPORT_ATTEMPTS
# full TRANSPORT_DEADLINE_SECONDS timeouts plus the backoff between them:
# 3*30 + 1 + 5 = 96s. The worker renews the service lease once per episode
# (after ``_deliver`` returns, before claiming the next one, see
# ``RuntimeAttentionDeliveryWorker.run_once``), so the lease is never touched
# *during* a single episode's transport. Every TTL below must therefore
# comfortably clear 96s, or a live claimant can be fenced mid-send by a
# successor that steals the lease while the original holder is still
# transporting (bu-5urw8) -- the fenced row then reads ``uncertain`` and
# offers the operator a reissue that would duplicate an already-delivered
# page. 150s clears the bound with ~54s of margin for per-attempt DB
# round-trips this budget does not otherwise account for.
SERVICE_LEASE_TTL_SECONDS = 150
CLAIM_LEASE_SECONDS = 150
TRANSPORT_DEADLINE_SECONDS = 30
STALE_SENDING_SECONDS = 150
MAX_TRANSPORT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 5.0)

# Alias-qualified: the claim statement joins its own candidate CTE, so a bare
# ``id`` in RETURNING is ambiguous.
_EPISODE_COLUMNS = """
    outbox.id, outbox.source, outbox.source_snapshot, outbox.payload,
    outbox.lifecycle_state, outbox.claim_token, outbox.claim_epoch,
    outbox.delivery_lease_epoch, outbox.claimed_at, outbox.claim_expires_at
"""


@dataclass(frozen=True, slots=True)
class ServiceLease:
    """Proof that this instance is the only active delivery service."""

    token: uuid.UUID
    epoch: int
    holder: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEpisode:
    """One claimed runtime-attention episode.

    Carries only the fixed allowlisted projection the outbox stores; there is
    no free-form failure text on this surface to leak.
    """

    id: uuid.UUID
    source: str
    source_snapshot: Any
    payload: Any
    lifecycle_state: str
    claim_token: uuid.UUID
    claim_epoch: int
    delivery_lease_epoch: int
    claimed_at: datetime
    claim_expires_at: datetime


@dataclass(frozen=True, slots=True)
class StaleClaim:
    """A ``sending`` row old enough to inspect, with the claim to fence."""

    id: uuid.UUID
    claim_token: uuid.UUID
    claim_epoch: int
    delivery_lease_epoch: int


def _episode(row: asyncpg.Record) -> OutboxEpisode:
    return OutboxEpisode(
        id=row["id"],
        source=row["source"],
        source_snapshot=row["source_snapshot"],
        payload=row["payload"],
        lifecycle_state=row["lifecycle_state"],
        claim_token=row["claim_token"],
        claim_epoch=row["claim_epoch"],
        delivery_lease_epoch=row["delivery_lease_epoch"],
        claimed_at=row["claimed_at"],
        claim_expires_at=row["claim_expires_at"],
    )


class RuntimeAttentionOutbox:
    """Switchboard's fenced view of the runtime-attention outbox."""

    def __init__(self, pool: asyncpg.Pool, *, instance_id: str) -> None:
        self._pool = pool
        self._instance_id = instance_id

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @asynccontextmanager
    async def _switchboard_tx(self) -> AsyncIterator[asyncpg.Connection]:
        """Run one statement group as ``butler_switchboard_rw``.

        ``SET LOCAL`` keeps the role change scoped to the transaction, so a
        pooled connection can never be handed back still wearing it.
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{SWITCHBOARD_ROLE}"')
                yield connection

    # -- service lease -------------------------------------------------------

    async def acquire_service_lease(self) -> ServiceLease | None:
        """Take the sole delivery lease, or return ``None`` if one is live.

        The epoch advances by exactly one per acquisition (the lease guard
        enforces this), which is what makes it usable as a recovery fence.
        """
        token = uuid.uuid4()
        async with self._switchboard_tx() as connection:
            await connection.execute(
                """
                INSERT INTO public.runtime_attention_delivery_lease (lease_name)
                VALUES ($1)
                ON CONFLICT (lease_name) DO NOTHING
                """,
                DELIVERY_LEASE_NAME,
            )
            row = await connection.fetchrow(
                """
                UPDATE public.runtime_attention_delivery_lease
                SET lease_token = $2,
                    lease_epoch = lease_epoch + 1,
                    holder_instance = $3,
                    acquired_at = now(),
                    expires_at = now() + make_interval(secs => $4)
                WHERE lease_name = $1
                  AND (lease_token IS NULL OR expires_at <= now())
                RETURNING lease_token, lease_epoch, holder_instance, expires_at
                """,
                DELIVERY_LEASE_NAME,
                token,
                self._instance_id,
                float(SERVICE_LEASE_TTL_SECONDS),
            )
        if row is None:
            return None
        return ServiceLease(
            token=row["lease_token"],
            epoch=row["lease_epoch"],
            holder=row["holder_instance"],
            expires_at=row["expires_at"],
        )

    async def renew_service_lease(self, lease: ServiceLease) -> bool:
        """Extend the lease this instance holds. ``False`` means it was lost."""
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                """
                UPDATE public.runtime_attention_delivery_lease
                SET expires_at = now() + make_interval(secs => $3)
                WHERE lease_name = $1 AND lease_token = $2
                RETURNING lease_epoch
                """,
                DELIVERY_LEASE_NAME,
                lease.token,
                float(SERVICE_LEASE_TTL_SECONDS),
            )
        return row is not None

    async def release_service_lease(self, lease: ServiceLease) -> bool:
        """Release the lease, preserving its fence epoch for the successor."""
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                """
                UPDATE public.runtime_attention_delivery_lease
                SET lease_token = NULL,
                    holder_instance = NULL,
                    acquired_at = NULL,
                    expires_at = NULL
                WHERE lease_name = $1 AND lease_token = $2
                RETURNING lease_epoch
                """,
                DELIVERY_LEASE_NAME,
                lease.token,
            )
        return row is not None

    # -- claiming ------------------------------------------------------------

    async def claim_next_pending(self, lease: ServiceLease) -> OutboxEpisode | None:
        """Claim the oldest due pending episode, committing ``sending`` first.

        The returned claim is durable before this method returns: transport
        must never begin against an uncommitted claim, or a crash mid-send
        would leave a row that looks deliverable and gets sent twice.
        """
        token = uuid.uuid4()
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                f"""
                WITH candidate AS (
                    SELECT id
                    FROM public.runtime_attention_outbox
                    WHERE lifecycle_state = 'pending'
                      AND next_attempt_at <= now()
                    ORDER BY next_attempt_at ASC, created_at ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE public.runtime_attention_outbox AS outbox
                SET lifecycle_state = 'sending',
                    claim_token = $1,
                    claim_epoch = outbox.claim_epoch + 1,
                    delivery_lease_epoch = $2,
                    claimed_by_instance = $3,
                    claimed_at = now(),
                    claim_expires_at = now() + make_interval(secs => $4)
                FROM candidate
                WHERE outbox.id = candidate.id
                  AND outbox.lifecycle_state = 'pending'
                RETURNING {_EPISODE_COLUMNS}
                """,  # noqa: S608 - _EPISODE_COLUMNS is a module constant
                token,
                lease.epoch,
                self._instance_id,
                float(CLAIM_LEASE_SECONDS),
            )
        return None if row is None else _episode(row)

    async def claim_is_current(self, episode: OutboxEpisode) -> bool:
        """Return whether this exact claim still owns a ``sending`` row.

        Called immediately before every transport attempt: between claiming and
        sending, a recovery sweep may have fenced this claim, and a fenced
        claimant must never put anything on the wire.
        """
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                """
                SELECT 1
                FROM public.runtime_attention_outbox
                WHERE id = $1
                  AND lifecycle_state = 'sending'
                  AND claim_token = $2
                  AND claim_epoch = $3
                  AND delivery_lease_epoch = $4
                """,
                episode.id,
                episode.claim_token,
                episode.claim_epoch,
                episode.delivery_lease_epoch,
            )
        return row is not None

    # -- terminal transitions ------------------------------------------------

    async def _finish(self, episode: OutboxEpisode, statement: str, *extra: Any) -> bool:
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                statement,
                episode.id,
                episode.claim_token,
                episode.claim_epoch,
                episode.delivery_lease_epoch,
                *extra,
            )
        return row is not None

    async def mark_sent(
        self, episode: OutboxEpisode, *, notification_ref: uuid.UUID | None = None
    ) -> bool:
        """Record a confirmed delivery. ``False`` means the claim was fenced.

        ``notification_ref`` is the optional scalar linkage to the logged
        ``switchboard.notifications`` row the CHECK constraint permits for a
        ``sent`` episode; it carries no delivery-error evidence of its own.
        """
        return await self._finish(
            episode,
            """
            UPDATE public.runtime_attention_outbox
            SET lifecycle_state = 'sent', delivered_at = now(), notification_ref = $5
            WHERE id = $1
              AND lifecycle_state = 'sending'
              AND claim_token = $2
              AND claim_epoch = $3
              AND delivery_lease_epoch = $4
            RETURNING id
            """,
            notification_ref,
        )

    async def mark_failed(
        self,
        episode: OutboxEpisode,
        *,
        error_class: str,
        error_detail: str,
        notification_ref: uuid.UUID | None = None,
    ) -> bool:
        """Record a terminal failure that is *proven* not to have delivered.

        ``error_class``/``error_detail`` must be one of the fixed pairs
        ``ck_runtime_attention_outbox_delivery_evidence`` accepts; anything
        else is rejected at the database, never at this layer.
        """
        return await self._finish(
            episode,
            """
            UPDATE public.runtime_attention_outbox
            SET lifecycle_state = 'failed',
                delivery_error_class = $5,
                delivery_error_detail = $6,
                notification_ref = $7
            WHERE id = $1
              AND lifecycle_state = 'sending'
              AND claim_token = $2
              AND claim_epoch = $3
              AND delivery_lease_epoch = $4
            RETURNING id
            """,
            error_class,
            error_detail,
            notification_ref,
        )

    async def mark_uncertain(
        self,
        episode: OutboxEpisode,
        *,
        error_class: str,
        error_detail: str,
        notification_ref: uuid.UUID | None = None,
    ) -> bool:
        """Record an ambiguous send. Terminal: it is never reclaimed.

        ``error_class``/``error_detail`` must be one of the fixed pairs
        ``ck_runtime_attention_outbox_delivery_evidence`` accepts; anything
        else is rejected at the database, never at this layer.
        """
        return await self._finish(
            episode,
            """
            UPDATE public.runtime_attention_outbox
            SET lifecycle_state = 'uncertain',
                delivery_error_class = $5,
                delivery_error_detail = $6,
                notification_ref = $7
            WHERE id = $1
              AND lifecycle_state = 'sending'
              AND claim_token = $2
              AND claim_epoch = $3
              AND delivery_lease_epoch = $4
            RETURNING id
            """,
            error_class,
            error_detail,
            notification_ref,
        )

    # -- recovery ------------------------------------------------------------

    async def list_recoverable(
        self,
        lease: ServiceLease,
        *,
        limit: int = 50,
        stale_after_seconds: float = STALE_SENDING_SECONDS,
    ) -> list[StaleClaim]:
        """List ``sending`` rows this lease holder is entitled to inspect.

        Two conditions, both required.  Age makes a row *interesting*; the
        lease-epoch comparison is what makes it *safe* — a row claimed under an
        earlier epoch cannot have a live holder, because acquiring this lease
        required the previous one to be absent or expired.

        Inspection is not authority: nothing here transitions a row.
        ``stale_after_seconds`` exists so a test can compress the wall-clock
        window; it cannot widen recovery authority, because the transition that
        follows is fenced on the claim, never on age.
        """
        async with self._switchboard_tx() as connection:
            rows = await connection.fetch(
                """
                SELECT id, claim_token, claim_epoch, delivery_lease_epoch
                FROM public.runtime_attention_outbox
                WHERE lifecycle_state = 'sending'
                  AND claimed_at <= now() - make_interval(secs => $1)
                  AND delivery_lease_epoch < $2
                ORDER BY claimed_at ASC, id ASC
                LIMIT $3
                """,
                float(stale_after_seconds),
                lease.epoch,
                limit,
            )
        return [
            StaleClaim(
                id=row["id"],
                claim_token=row["claim_token"],
                claim_epoch=row["claim_epoch"],
                delivery_lease_epoch=row["delivery_lease_epoch"],
            )
            for row in rows
        ]

    async def fence_stale_claim(self, claim: StaleClaim, lease: ServiceLease) -> bool:
        """Atomically fence a dead claim to ``uncertain``.

        The dead claim is never reclaimed for transport.  Its previous holder
        may have handed the message to the provider before dying, so the only
        honest terminal state is ``uncertain`` — replaying it would be exactly
        the double delivery this lane exists to prevent.

        ``delivery_error_class``/``delivery_error_detail`` are set to the
        fixed ``('transport_uncertain', 'worker_recovery')`` pair
        ``ck_runtime_attention_outbox_delivery_evidence`` reserves for exactly
        this recovery-fenced path (mirrors ``routing.transport.WORKER_RECOVERY``).
        ``notification_ref`` stays NULL: no delivery was observed here, so
        there is no notification to link.
        """
        async with self._switchboard_tx() as connection:
            row = await connection.fetchrow(
                """
                UPDATE public.runtime_attention_outbox
                SET lifecycle_state = 'uncertain',
                    delivery_error_class = 'transport_uncertain',
                    delivery_error_detail = 'worker_recovery'
                WHERE id = $1
                  AND lifecycle_state = 'sending'
                  AND claim_token = $2
                  AND claim_epoch = $3
                  AND delivery_lease_epoch = $4
                  AND delivery_lease_epoch < $5
                RETURNING id
                """,
                claim.id,
                claim.claim_token,
                claim.claim_epoch,
                claim.delivery_lease_epoch,
                lease.epoch,
            )
        return row is not None
