"""Owning-source transaction and dark candidate preparation; no admission call.

There is deliberately no EvidenceProvider, cross-domain SQL, service endpoint or
publisher here. A future authenticated control adapter must consume committed
outbox references and verify catalog/source/cause authority separately.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
