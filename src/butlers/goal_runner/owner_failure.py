"""Tell the owner when their request failed and nobody else did (RFC 0038 S6, bu-dhloi1.14).

An owner message that became a goal and ended ``failed`` or ``budget_exhausted`` leaves the
owner with silence unless the goal's own lineage already delivered something to them. The
runner's sweep finds such goals, records one ``switchboard.pending_asks`` row per goal
(identity: the goal's ``source_key``) and sends one model-free cause line.

- Dark: nothing is written or sent unless ``public.owner_delivery_rollout`` is on and exactly
  one owner entity exists.
- The line is built from closed words only. It carries no ask text, no token and no provider
  detail. A presentation (``owner_delivery.present``) gives the retry identity; the send is
  marked handed-off before the call, so a crash leaves an honest ``unknown``, never a second
  line.
- "Already told" means a possibly-delivered owner effect inside this goal's own lineage (the
  goal and the goals it caused). An owner delivery by an unrelated goal does not count.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from butlers.core import owner_delivery as od

logger = logging.getLogger(__name__)

SURFACE: Final = "telegram"
ROUTE_REF: Final = "owner:telegram"
#: Presentation tokens are minted but never shown on a cause line; they lapse unused.
_TOKEN_TTL: Final = timedelta(hours=24)
_BATCH: Final = 10

#: Closed cause words to the one sentence the owner sees. Nothing else is ever sent.
CAUSE_LINES: Final = {
    "failed": "A request you sent did not complete. Nothing was done in response.",
    "budget_exhausted": "A request you sent used up its work budget before it could finish.",
    "capability_unavailable": (
        "A request you sent could not run because a service it needs was unavailable."
    ),
}

# A terminal owner-message goal with no possibly-delivered owner effect in its lineage and
# no pending ask yet. 'committed', 'dispatched' and 'unknown' may have reached the owner.
_UNREPORTED = """
WITH RECURSIVE lineage (root, goal_id) AS (
    SELECT g.id, g.id FROM switchboard.goals g
     WHERE g.origin = 'owner' AND g.source_kind = 'message'
       AND g.status IN ('failed', 'budget_exhausted')
    UNION
    SELECT l.root, c.id FROM lineage l
      JOIN switchboard.goals c ON c.caused_by_goal = l.goal_id
)
SELECT g.id, g.source_key, g.status,
       EXISTS (SELECT 1 FROM switchboard.broker_refusals r
                WHERE r.goal_id = g.id AND r.reason = 'capability_unavailable') AS unavailable
  FROM switchboard.goals g
 WHERE g.origin = 'owner' AND g.source_kind = 'message'
   AND g.status IN ('failed', 'budget_exhausted')
   AND NOT EXISTS (SELECT 1 FROM switchboard.pending_asks p WHERE p.source_key = g.source_key)
   AND NOT EXISTS (
       SELECT 1 FROM lineage l JOIN switchboard.effects e ON e.goal_id = l.goal_id
        WHERE l.root = g.id AND e.recipient_entity = $1
          AND e.status IN ('committed', 'dispatched', 'unknown'))
 ORDER BY g.updated_at, g.id
 LIMIT $2
"""

# Pending asks whose subject has no presentation yet at its current generation.
_UNPRESENTED = """
SELECT p.id, p.cause, p.subject_id
  FROM switchboard.pending_asks p
  JOIN public.owner_delivery_subjects s ON s.id = p.subject_id
 WHERE p.state = 'open'
   AND NOT EXISTS (SELECT 1 FROM public.owner_delivery_presentations x
                    WHERE x.subject_id = s.id AND x.surface = $1 AND x.generation = s.generation)
 ORDER BY p.created_at, p.id
 LIMIT $2
"""


class CauseLineSender(Protocol):
    """Sends one fixed line to the owner. ``suppressed`` means "not now, ask again later"."""

    async def suppressed(self) -> bool: ...

    async def send(self, text: str, *, dedup_key: str) -> od.SendOutcome: ...


def cause_for(status: str, unavailable: bool) -> str:
    """The closed cause word for a terminal goal."""
    if unavailable:
        return "capability_unavailable"
    return "budget_exhausted" if status == "budget_exhausted" else "failed"


class OwnerFailureReporter:
    """One sweep step: record unreported owner failures, then send their cause lines."""

    def __init__(
        self,
        pool: Any,
        sender: CauseLineSender,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        batch: int = _BATCH,
    ) -> None:
        self.pool = pool
        self.sender = sender
        self.clock = clock
        self.batch = batch

    async def sweep(self) -> int:
        """Returns how many cause lines were handed to the sender."""
        if await self._record() is None:
            return 0
        return await self._present()

    async def _record(self) -> int | None:
        """Insert missing pending asks. ``None`` when the path is dark or has no owner."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            if not await od.rollout_enabled(conn):
                return None
            owner = await od.owner_entity_id(conn)
            if owner is None:
                return None
            rows = await conn.fetch(_UNREPORTED, owner, self.batch)
            made = 0
            for row in rows:
                cause = cause_for(row["status"], row["unavailable"])
                ask = await conn.fetchrow(
                    "INSERT INTO switchboard.pending_asks (source_key, goal_id, cause) "
                    "VALUES ($1, $2, $3) ON CONFLICT (source_key) DO NOTHING RETURNING id",
                    row["source_key"],
                    row["id"],
                    cause,
                )
                if ask is None:
                    continue
                subject = await od.open_subject(
                    conn,
                    kind="pending_ask",
                    source_ref=str(ask["id"]),
                    owner_entity=owner,
                    content_digest=od.digest_of({"cause": cause, "source_key": row["source_key"]}),
                    action_digest=od.digest_of({"kind": "pending_ask", "ask": str(ask["id"])}),
                )
                await conn.execute(
                    "UPDATE switchboard.pending_asks SET subject_id = $2 WHERE id = $1",
                    ask["id"],
                    subject.id,
                )
                made += 1
            return made

    async def _present(self) -> int:
        """Send the cause line of each ask that has none, at most once each."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            pending = await conn.fetch(_UNPRESENTED, SURFACE, self.batch)
        sent = 0
        for ask in pending:
            if await self.sender.suppressed():
                break  # quiet hours or a context hold: look again on the next sweep
            if await self._deliver(ask):
                sent += 1
        return sent

    async def _deliver(self, ask: Any) -> bool:
        line = CAUSE_LINES.get(ask["cause"])
        if line is None:
            return False
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            presentation = await od.present(
                conn,
                ask["subject_id"],
                surface=SURFACE,
                route_ref=ROUTE_REF,
                actions=("ack",),
                expires_at=self.clock() + _TOKEN_TTL,
            )
            # A presentation already handed to its provider is never sent again.
            if presentation.state != "pending" or not await od.begin_send(conn, presentation.id):
                return False
        try:
            outcome = await self.sender.send(line, dedup_key=f"pending_ask:{ask['id']}")
        except Exception:
            logger.warning("pending ask cause line send failed ask=%s", ask["id"])
            outcome = "unknown"
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            await od.record_send(conn, presentation.id, outcome=outcome)
        return outcome == "sent"


class OwnerNotifySender:
    """The existing owner notify path: suppression check, then ``notify.v1`` via ``deliver``."""

    def __init__(
        self,
        pool: Any,
        *,
        send: Callable[..., Awaitable[str]] | None = None,
        suppression: Callable[..., Awaitable[str | None]] | None = None,
    ) -> None:
        from butlers.core.attention_ledger import check_owner_notify_suppression
        from butlers.jobs.owner_notify import send_owner_telegram

        self.pool = pool
        self._send = send or send_owner_telegram
        self._suppression = suppression or check_owner_notify_suppression

    async def suppressed(self) -> bool:
        return await self._suppression(self.pool, log_context="pending_ask") is not None

    async def send(self, text: str, *, dedup_key: str) -> od.SendOutcome:
        outcome = await self._send(
            self.pool,
            origin_butler="switchboard",
            message=text,
            log_context="pending_ask",
            dedup_key=dedup_key,
        )
        # A suppression that began after the pre-check is a dropped line, not a retry:
        # the handed-off presentation cannot be sent again.
        return "sent" if outcome == "delivered" else "failed"
