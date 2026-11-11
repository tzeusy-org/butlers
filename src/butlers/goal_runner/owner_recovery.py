"""Run or Drop a refused owner request (RFC 0038 S6, bu-dhloi1.14 package 4b).

A refused admission, or a failed goal, of an owner message leaves one ``pending_asks`` row (cause
``admission_refused``) holding a source *reference*. The owner may then:

- **Run**: admit the reference again through the full current admission rules, exactly as if
  the owner had re-sent the message now. Evidence is loaded afresh from the source reader, so
  Run cannot bypass or pre-satisfy any authorization check; if admission refuses again the
  decision rolls back and the card stays. The attempt id is derived from the ask, so a repeat
  admits once.
- **Drop**: the ask ends ``dropped`` and the reference is cleared.

Both go through ``owner_delivery.redeem`` on the ``pending_ask`` subject with the ask's own
``decide`` callback; an approval token can never redeem a pending ask and the reverse. Only
refused-admission asks can Run: a goal that already failed owns its source key, so replaying
it would only find that goal.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final, Literal
from uuid import UUID, uuid5

from butlers.core import owner_delivery as od
from butlers.core.goal_admission import AdmissionResult, Evidence, EvidenceRefused, SourceRef

logger = logging.getLogger(__name__)

CARD_SURFACE: Final = "dashboard"
CARD_ROUTE_REF: Final = "owner:dashboard"
CARD_TOKEN_TTL: Final = timedelta(hours=24)
#: Derives the admission attempt id from the ask, so a repeated Run is one admission.
_RUN_NAMESPACE: Final = UUID("8f1a3b2c-6d4e-5f70-9a1b-2c3d4e5f6071")
_NO_OWNER: Final = UUID(int=0)

#: ``admit(connection, source, attempt_id)``: admission on the redemption's own connection.
Admit = Callable[[Any, SourceRef, UUID], Awaitable[AdmissionResult]]


class AskDecisionRefused(Exception):
    """The ask cannot take this decision; the redemption rolls back and the card stays."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Card:
    ask_id: UUID
    cause: str
    tokens: dict[str, str]


@dataclass(frozen=True)
class CardBatch:
    """One tick's cards: counts by cause plus one Run/Drop token pair per ask."""

    counts: dict[str, int] = field(default_factory=dict)
    cards: tuple[Card, ...] = ()


@dataclass(frozen=True)
class AskDecision:
    outcome: Literal["won", "duplicate", "refused"]
    reason: str | None = None
    state: str | None = None


RETRY_PREFIX: Final = "owner_retry:"
RETRY_CAUSES: Final = ("failed", "budget_exhausted")

_RUNNABLE = """
SELECT id, cause, subject_id FROM switchboard.pending_asks
 WHERE state = 'open' AND subject_id IS NOT NULL AND expires_at > clock_timestamp()
   AND ((cause = 'admission_refused' AND original_ask IS NOT NULL)
        OR (cause = ANY('{failed,budget_exhausted}'::text[]) AND goal_id IS NOT NULL))
 ORDER BY created_at, id LIMIT $1
"""


def source_of(original_ask: Any) -> SourceRef:
    """The source reference a refused ask kept; anything unrecognised cannot run."""
    if not isinstance(original_ask, dict) or original_ask.get("v") != 1:
        raise AskDecisionRefused("cannot_run")
    source = original_ask.get("source")
    if not isinstance(source, dict):
        raise AskDecisionRefused("cannot_run")
    try:
        return SourceRef(source["kind"], source["id"], source.get("occurrence"))
    except (KeyError, ValueError):
        raise AskDecisionRefused("cannot_run") from None


class OwnerRecovery:
    def __init__(self, pool: Any, admit: Admit, *, batch: int = 10) -> None:
        self.pool = pool
        self.admit = admit
        self.batch = batch

    async def cards(self) -> CardBatch:
        """Dashboard cards for the owner's runnable asks; empty while delivery is dark."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            if not await od.rollout_enabled(conn):
                return CardBatch()
            asks = await conn.fetch(_RUNNABLE, self.batch)
            cards: list[Card] = []
            counts: dict[str, int] = {}
            for ask in asks:
                try:
                    presentation = await od.present(
                        conn,
                        ask["subject_id"],
                        surface=CARD_SURFACE,
                        route_ref=CARD_ROUTE_REF,
                        actions=("approve", "reject"),
                        expires_at=await conn.fetchval("SELECT clock_timestamp()") + CARD_TOKEN_TTL,
                    )
                except od.OwnerDeliveryRefused:
                    continue
                cards.append(Card(ask["id"], ask["cause"], dict(presentation.tokens)))
                counts[ask["cause"]] = counts.get(ask["cause"], 0) + 1
            return CardBatch(counts, tuple(cards))

    async def decide(
        self,
        token: object,
        ask_id: UUID,
        action: Literal["approve", "reject"],
        *,
        surface: od.Surface = CARD_SURFACE,
    ) -> AskDecision:
        """Redeem one card token: Run (approve) or Drop (reject)."""
        decided: dict[str, Any] = {}

        async def decide(conn: Any, verb: str) -> dict[str, Any]:
            ask = await conn.fetchrow(
                "SELECT state, cause, original_ask, goal_id FROM switchboard.pending_asks "
                "WHERE id = $1 FOR UPDATE",
                ask_id,
            )
            if ask is None or ask["state"] != "open":
                raise AskDecisionRefused("ask_not_open")
            if verb == "reject":
                state = "dropped"
            else:
                if ask["cause"] == "admission_refused":
                    source = source_of(ask["original_ask"])
                elif ask["cause"] in RETRY_CAUSES and ask["goal_id"] is not None:
                    source = SourceRef("message", RETRY_PREFIX + str(ask_id))
                else:
                    raise AskDecisionRefused("cannot_run")
                admitted = await self.admit(conn, source, uuid5(_RUN_NAMESPACE, str(ask_id)))
                if admitted.result not in ("admitted", "joined"):
                    raise AskDecisionRefused("still_refused")
                state = "run"
            # Every terminal state clears the reference in the same update.
            await conn.execute(
                "UPDATE switchboard.pending_asks SET state = $2, original_ask = NULL, "
                "updated_at = clock_timestamp() WHERE id = $1",
                ask_id,
                state,
            )
            decided["state"] = state
            return {"state": state}

        try:
            async with self._locked(ask_id) as conn:
                redemption = await od.redeem(
                    conn,
                    token,
                    kind="pending_ask",
                    source_ref=str(ask_id),
                    expected_action=action,
                    owner_entity=await od.owner_entity_id(conn) or _NO_OWNER,
                    surface=surface,
                    current_action_digest=od.digest_of({"kind": "pending_ask", "ask": str(ask_id)}),
                    decide=decide,
                )
        except AskDecisionRefused as refused:
            return AskDecision("refused", refused.reason)
        if redemption.outcome == "refused":
            return AskDecision("refused", redemption.reason)
        return AskDecision(redemption.outcome, None, decided.get("state"))

    @asynccontextmanager
    async def _locked(self, ask_id: UUID) -> AsyncIterator[Any]:
        """One transaction holding the ask row first, then the subject (the lock order)."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            await conn.fetchval(
                "SELECT id FROM switchboard.pending_asks WHERE id = $1 FOR UPDATE", ask_id
            )
            yield conn


class OwnerRetryEvidence:
    """Evidence reader that answers owner-retry sources and hands everything else to ``inner``."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def load(self, connection: Any, source: SourceRef) -> Evidence:
        if source.kind != "message" or not source.id.startswith(RETRY_PREFIX):
            return await self.inner.load(connection, source)
        try:
            ask_id = UUID(source.id[len(RETRY_PREFIX) :])
        except ValueError:
            raise EvidenceRefused("source_evidence_unavailable") from None
        goal = await connection.fetchrow(
            "SELECT g.exposed FROM switchboard.pending_asks a "
            "JOIN switchboard.goals g ON g.id = a.goal_id "
            "WHERE a.id = $1 AND a.state = 'open' AND a.cause = ANY($2::text[]) "
            "AND g.origin = 'owner' AND g.source_kind = 'message'",
            ask_id,
            list(RETRY_CAUSES),
        )
        if goal is None:
            raise EvidenceRefused("source_evidence_unavailable")
        # Owner evidence is the redeemed Run token, checked by ``redeem`` before this runs.
        return Evidence("message", "owner_retry", owner=True, outside_content=bool(goal["exposed"]))


def bound(connection: Any) -> Any:
    """A pool-shaped view of one connection, so ``GoalAdmission`` joins the caller's transaction."""

    class _Bound:
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[Any]:
            yield connection

    return _Bound()
