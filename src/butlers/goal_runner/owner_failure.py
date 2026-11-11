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
#: How long an ask stays open from the failure or refusal; after it, dropped(stale), never replayed.
ASK_LIFETIME: Final = timedelta(hours=24)

#: Closed cause words to the one sentence the owner sees. Nothing else is ever sent.
CAUSE_LINES: Final = {
    "failed": "A request you sent did not complete. Nothing was done in response.",
    "budget_exhausted": "A request you sent used up its work budget before it could finish.",
    "admission_refused": (
        "A request you sent was not accepted because the assistant was too busy. It was not run."
    ),
    "capability_unavailable": (
        "A request you sent could not run because a service it needs was unavailable."
    ),
}

#: A configuration fault is not "too busy": a refusal for unreadable limits gets its own sentence.
LIMITS_UNAVAILABLE_LINE: Final = (
    "A request you sent was not accepted because the assistant could not check its limits. "
    "It was not run."
)


def line_for(cause: str, refusal: str | None) -> str | None:
    if cause == "admission_refused" and refusal == "limits_unavailable":
        return LIMITS_UNAVAILABLE_LINE
    return CAUSE_LINES.get(cause)


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
SELECT g.id, g.source_key, g.status, g.updated_at AS ended_at,
       EXISTS (SELECT 1 FROM switchboard.broker_refusals r
                WHERE r.goal_id = g.id AND r.reason = 'capability_unavailable') AS unavailable
  FROM switchboard.goals g
 WHERE g.origin = 'owner' AND g.source_kind = 'message'
   AND g.status IN ('failed', 'budget_exhausted')
   AND g.updated_at > clock_timestamp() - interval '24 hours'
   AND NOT EXISTS (SELECT 1 FROM switchboard.pending_asks p WHERE p.source_key = g.source_key)
   AND NOT EXISTS (
       SELECT 1 FROM lineage l JOIN switchboard.effects e ON e.goal_id = l.goal_id
        WHERE l.root = g.id AND e.recipient_entity = $1
          AND NOT e.prompt_accepts_input
          AND e.status IN ('committed', 'dispatched', 'unknown'))
 ORDER BY g.updated_at, g.id
 LIMIT $2
"""

# Pending asks with no presentation at the subject's current generation, or whose presentation
# there was proven not sent ('failed'): ``present`` resets that one and it is sent once.
# A line that cannot be sent is presented at most this many times; the ask then waits for expiry.
MAX_LINE_ATTEMPTS = 3

_UNPRESENTED = """
SELECT p.id, p.cause, p.subject_id, p.original_ask ->> 'refusal' AS refusal, p.line_attempts
  FROM switchboard.pending_asks p
  JOIN public.owner_delivery_subjects s ON s.id = p.subject_id
 WHERE p.state = 'open' AND p.expires_at > clock_timestamp() AND p.cause = ANY($3::text[])
   AND p.line_attempts < $4
   AND NOT EXISTS (SELECT 1 FROM public.owner_delivery_presentations x
                    WHERE x.subject_id = s.id AND x.surface = $1 AND x.generation = s.generation
                      AND x.state <> 'failed')
 ORDER BY p.created_at, p.id
 LIMIT $2
"""


# Open, unexpired asks the sweep has not yet given an owner-delivery subject (refused admissions).
_UNOPENED = """
SELECT id, source_key, cause FROM switchboard.pending_asks
 WHERE state = 'open' AND subject_id IS NULL AND expires_at > clock_timestamp()
 ORDER BY created_at, id LIMIT $1
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
        await self._expire()  # privacy first: runs even while delivery is dark
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
                try:
                    async with conn.transaction():  # a savepoint: one ask never aborts the rest
                        made += await self._record_one(conn, row, owner)
                except Exception:
                    logger.warning("pending ask could not be recorded goal=%s", row["id"])
            orphans = await conn.fetch(_UNOPENED, self.batch)
            for orphan in orphans:
                try:
                    async with conn.transaction():
                        await self._open_subject(
                            conn, orphan["id"], orphan["source_key"], orphan["cause"], owner
                        )
                except Exception:
                    logger.warning("pending ask subject could not be opened ask=%s", orphan["id"])
            return made

    async def _expire(self) -> int:
        """Open asks past their expiry become ``dropped_stale``: the reference is cleared, the
        subject is closed, and nothing is replayed. Runs whether or not delivery is on.

        Each row is its own savepoint, so one bad row cannot abort the rest of the sweep.
        """
        done = 0
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            due = await conn.fetch(
                "SELECT id FROM switchboard.pending_asks "
                "WHERE state = 'open' AND expires_at <= clock_timestamp() "
                "ORDER BY expires_at, id LIMIT $1",
                self.batch * 5,
            )
            for ask in due:
                try:
                    async with conn.transaction():
                        row = await conn.fetchrow(
                            "UPDATE switchboard.pending_asks SET state = 'dropped_stale', "
                            "original_ask = NULL, updated_at = clock_timestamp() "
                            "WHERE id = $1 AND state = 'open' RETURNING subject_id",
                            ask["id"],
                        )
                        if row is None:
                            continue
                        if row["subject_id"] is not None:
                            await od.close_for_authority(
                                conn,
                                kind="pending_ask",
                                source_ref=str(ask["id"]),
                                status="expired",
                            )
                        done += 1
                except Exception:
                    logger.warning("pending ask could not be expired ask=%s", ask["id"])
        return done

    @classmethod
    async def _record_one(cls, conn: Any, row: Any, owner: Any) -> int:
        cause = cause_for(row["status"], row["unavailable"])
        ask = await conn.fetchrow(
            "INSERT INTO switchboard.pending_asks (source_key, goal_id, cause, expires_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (source_key) DO NOTHING RETURNING id",
            row["source_key"],
            row["id"],
            cause,
            # ended_at is goals.updated_at, which later touches can move; acceptable for a
            # 24 hour bound that only ever errs toward offering the ask for longer.
            row["ended_at"] + ASK_LIFETIME,
        )
        if ask is None:
            return 0
        await cls._open_subject(conn, ask["id"], row["source_key"], cause, owner)
        return 1

    @staticmethod
    async def _open_subject(
        conn: Any, ask_id: Any, source_key: str, cause: str, owner: Any
    ) -> None:
        subject = await od.open_subject(
            conn,
            kind="pending_ask",
            source_ref=str(ask_id),
            owner_entity=owner,
            content_digest=od.digest_of({"cause": cause, "source_key": source_key}),
            action_digest=od.digest_of({"kind": "pending_ask", "ask": str(ask_id)}),
        )
        await conn.execute(
            "UPDATE switchboard.pending_asks SET subject_id = $2 WHERE id = $1",
            ask_id,
            subject.id,
        )

    async def _present(self) -> int:
        """Send the cause line of each ask that has none, at most once each."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            pending = await conn.fetch(
                _UNPRESENTED, SURFACE, self.batch, list(CAUSE_LINES), MAX_LINE_ATTEMPTS
            )
        sent = 0
        for ask in pending:
            if await self.sender.suppressed():
                break  # quiet hours or a context hold: look again on the next sweep
            try:
                if await self._deliver(ask):
                    sent += 1
            except Exception:
                logger.warning("pending ask cause line failed ask=%s", ask["id"])
        return sent

    async def _deliver(self, ask: Any) -> bool:
        line = line_for(ask["cause"], ask["refusal"])
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
            await conn.execute(
                "UPDATE switchboard.pending_asks SET line_attempts = line_attempts + 1 "
                "WHERE id = $1",
                ask["id"],
            )
        try:
            outcome = await self.sender.send(line, dedup_key=f"pending_ask:{ask['id']}")
        except Exception:
            logger.warning("pending ask cause line send failed ask=%s", ask["id"])
            outcome = "unknown"
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('search_path', 'switchboard,public', true)")
            await od.record_send(conn, presentation.id, outcome=outcome)
        if outcome == "failed" and ask["line_attempts"] + 1 >= MAX_LINE_ATTEMPTS:
            logger.warning(
                "pending ask cause line given up after %d presentations ask=%s cause=%s",
                MAX_LINE_ATTEMPTS,
                ask["id"],
                ask["cause"],
            )
        return outcome == "sent"


class OwnerNotifySender:
    """The existing owner notify path: suppression check, then ``notify.v1`` via ``deliver``.

    Only a result decided before any provider call is a proven non-send (``failed``, safe to
    present again). Anything after the call began is ``unknown`` and is never sent again.
    """

    def __init__(
        self,
        pool: Any,
        *,
        send: Callable[..., Awaitable[Any]] | None = None,
        suppression: Callable[..., Awaitable[str | None]] | None = None,
    ) -> None:
        from butlers.core.attention_ledger import check_owner_notify_suppression
        from butlers.jobs.owner_notify import send_owner_telegram_checked

        self.pool = pool
        self._send = send or send_owner_telegram_checked
        self._suppression = suppression or check_owner_notify_suppression

    async def suppressed(self) -> bool:
        return await self._suppression(self.pool, log_context="pending_ask") is not None

    async def send(self, text: str, *, dedup_key: str) -> od.SendOutcome:
        result = await self._send(
            self.pool,
            origin_butler="switchboard",
            message=text,
            log_context="pending_ask",
            dedup_key=dedup_key,
        )
        if result.outcome == "delivered":
            return "sent"
        # Suppressed (quiet hours began after the pre-check), no recipient or no client:
        # nothing was handed to the provider, so the next unsuppressed sweep sends once.
        # A failed call may still have delivered: ambiguous, never resent.
        return "unknown" if result.call_started else "failed"
