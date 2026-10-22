"""Owning-source transaction and dark candidate preparation; no admission call.

There is deliberately no EvidenceProvider, cross-domain SQL, service endpoint or
publisher here. A future authenticated control adapter must consume committed
outbox references and verify catalog/source/cause authority separately.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from butlers.core.goal_admission import SourceRef
from butlers.core.obligation_sources import (
    DefinitionRevision,
    ObligationSourceRepository,
    SourceConflict,
)


class SourceUnavailable(RuntimeError):
    """The command's commit is unconfirmed; reconcile its original identity."""


async def run_source_transaction[T](
    pool,
    owner_domain: str,
    command: Callable[[ObligationSourceRepository], Awaitable[T]],
) -> T:
    """Return ONLY after commit and pool release; never retry a callback blindly.

    SourceConflict is a deterministic local refusal. SQL/commit/release failures
    and cancellation never become a claimed successful write. A lost commit
    reply is resolved by reusing the original command/occurrence/claim identity.
    No exception content enters this closed error message.
    """
    try:
        async with pool.acquire() as connection, connection.transaction():
            result = await command(ObligationSourceRepository(connection, owner_domain))
        return result
    except SourceConflict:
        raise
    except (Exception, asyncio.CancelledError):
        raise SourceUnavailable("source_transaction_unconfirmed") from None


@dataclass(frozen=True)
class DarkSubmission:
    """Staged identity only, NOT verified Evidence or a committed receipt."""

    occurrence_id: UUID
    source: SourceRef
    definition: DefinitionRevision


async def prepare_dark_submission(
    repository: ObligationSourceRepository,
    source_id: UUID,
    occurrence_id: UUID,
    token: UUID,
) -> DarkSubmission:
    """Revalidate the claim/current generation in the same owning transaction.

    Locks are held through the caller's transaction. This is not a promise that
    a later network operation is fenced, and it never creates a goal/reserves
    budget/changes today's dispatch. Caller must not publish before commit.
    """
    definition, occurrence = await repository.fence(source_id, occurrence_id)
    if occurrence["state"] != "claimed" or occurrence["claim_token"] != token:
        raise SourceConflict("claim_not_current")
    return DarkSubmission(
        occurrence_id,
        SourceRef(occurrence["source_kind"], occurrence["source_ref"], occurrence["occurrence"]),
        definition,
    )


@dataclass(frozen=True)
class DarkAttemptObservation:
    """OFF-path identity hints, never verified source/cause/creator authority.

    These local immutable values do not drive dispatch and are not trace data.
    In particular a reported event UUID does not prove an active subscription.
    """

    kind: Literal["schedule_attempt", "domain_wake"]
    phase: Literal["post_claim", "wake_created", "wake_reconciled"]
    owner_domain: str | None
    source_row_id: UUID | None
    nominal_slot: datetime | None
    reported_event_id: UUID | None
    definition_state: Literal["unavailable"] = field(default="unavailable", init=False)
    authority: Literal["unverified"] = field(default="unverified", init=False)
    mode: Literal["off"] = field(default="off", init=False)


def _reported_uuid(value: object) -> UUID | None:
    if type(value) is UUID:
        return value
    if type(value) is str and len(value) == 36:
        try:
            return UUID(value)
        except ValueError:
            pass
    return None


def build_dark_attempt_observation(
    *,
    kind: str,
    phase: str,
    owner_domain: object = None,
    source_row_id: object = None,
    nominal_slot: object = None,
    reported_event_id: object = None,
) -> DarkAttemptObservation:
    """Normalize ONLY reported identity; no IO, clock, hash or permission.

    The nominal cron slot comes from the selected row before its existing CAS,
    never the advanced next_run_at or observation time. Invalid hints stay null.
    """
    if (kind, phase) not in {
        ("schedule_attempt", "post_claim"),
        ("domain_wake", "wake_created"),
        ("domain_wake", "wake_reconciled"),
    }:
        raise ValueError("invalid_off_observation_category")
    domain = (
        owner_domain
        if type(owner_domain) is str
        and len(owner_domain) <= 63
        and re.fullmatch(r"[a-z][a-z0-9_-]*", owner_domain)
        else None
    )
    slot = (
        nominal_slot
        if kind == "schedule_attempt"
        and type(nominal_slot) is datetime
        and nominal_slot.utcoffset() is not None
        else None
    )
    return DarkAttemptObservation(
        kind=kind,
        phase=phase,
        owner_domain=domain,
        source_row_id=_reported_uuid(source_row_id),
        nominal_slot=slot,
        reported_event_id=_reported_uuid(reported_event_id) if kind == "domain_wake" else None,
    )


def observe_dark_attempt(
    *,
    kind: str,
    phase: str,
    owner_domain: object = None,
    source_row_id: object = None,
    nominal_slot: object = None,
    reported_event_id: object = None,
) -> None:
    """Best-effort closed trace only; OFF never calls B2 or GoalAdmission.

    Ordinary builder/trace failures cannot alter legacy dispatch. Cancellation
    and other BaseExceptions propagate. IDs, timestamps and source content are
    deliberately excluded from the trace, including failure text.
    """
    if (kind, phase) not in {
        ("schedule_attempt", "post_claim"),
        ("domain_wake", "wake_created"),
        ("domain_wake", "wake_reconciled"),
    }:
        return
    quality = "unavailable"
    try:
        observation = build_dark_attempt_observation(
            kind=kind,
            phase=phase,
            owner_domain=owner_domain,
            source_row_id=source_row_id,
            nominal_slot=nominal_slot,
            reported_event_id=reported_event_id,
        )
        if (
            type(observation) is DarkAttemptObservation
            and observation.kind == kind
            and observation.phase == phase
            and observation.mode == "off"
            and observation.authority == "unverified"
            and observation.definition_state == "unavailable"
        ):
            quality = "observed"
    except Exception:
        pass
    try:
        from opentelemetry import trace

        trace.get_current_span().add_event(
            "obligation.off_observation",
            attributes={
                "mode": "off",
                "kind": kind,
                "phase": phase,
                "authority": "unverified",
                "definition_state": "unavailable",
                "quality": quality,
            },
        )
    except Exception:
        pass
