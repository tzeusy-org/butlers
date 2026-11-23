"""Decay of owner break-through clauses: ask once, then go dormant (bu-s11n0s.8 S4).

A clause the owner declared should not outlive their interest in it. This deterministic sweep
(no model, run by the hourly Switchboard urgent sub-cycle) only ever **narrows**:

* a clause whose delivered hits were not opened for 30 days gets **one** content-free "keep?"
  line, through the ordinary insight broker, and is marked ``asked``;
* a clause with **no hit at all** for 90 days is marked ``dormant``: the fixed evaluator skips it,
  while it stays listed and revivable on ``/settings/reach``.

Keeping an asked clause or reviving a dormant one is never done here: it is the dashboard cookie
session's ``clause_keep`` action (a grant-adjacent act). The sweep reaches the clause store only
through two narrow definers (``decay_clauses`` reads ids and dates, ``decay_apply`` enforces the
age thresholds itself), so a wrong or hostile caller can at most ask or dormant a clause that is
already old enough; it cannot create, widen, revoke or re-arm one.

"Opened" is the one open signal that exists: the delivered line's ``insight_engagement.engaged``
flag, which the broker sets when the owner sends any message within the engagement window after
delivery or marks the line useful. It is a proxy owned by bu-s11n0s.15; this module does not
change it. Engagement rows are kept 30 days, which is exactly the "unopened" window here.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

ASK_AFTER = timedelta(days=30)
DORMANT_AFTER = timedelta(days=90)
KEEP_CATEGORY = "break_through_keep"
KEEP_PRIORITY = 60
_KEEP_TTL = timedelta(days=14)
# A lost "keep?" proposal is retried this long after the clause was marked asked.
_RETRY_WINDOW = timedelta(days=7)
KEEP_MESSAGE = (
    "A sender you declared hasn't been opened in 30 days - keep them? Decide in Settings > Reach."
)


@dataclass(frozen=True)
class Clause:
    id: uuid.UUID
    created_at: datetime
    state: str | None
    changed_at: datetime | None


@dataclass(frozen=True)
class Stats:
    last_hit: datetime | None = None
    delivered_30: int = 0
    opened_30: int = 0


@dataclass(frozen=True)
class Decision:
    ask: tuple[uuid.UUID, ...]
    dormant: tuple[uuid.UUID, ...]


def _baseline(clause: Clause) -> datetime:
    """When the owner last vouched for the clause: its creation, or their latest keep."""
    if clause.state == "kept" and clause.changed_at is not None:
        return max(clause.created_at, clause.changed_at)
    return clause.created_at


def decide(clauses: Sequence[Clause], stats: Mapping[uuid.UUID, Stats], now: datetime) -> Decision:
    """Which clauses to ask about and which to put dormant. Pure and deterministic."""
    ask: list[uuid.UUID] = []
    dormant: list[uuid.UUID] = []
    for clause in clauses:
        if clause.state == "dormant":
            continue
        seen = stats.get(clause.id, Stats())
        age = now - _baseline(clause)
        if age >= DORMANT_AFTER and (seen.last_hit is None or now - seen.last_hit >= DORMANT_AFTER):
            dormant.append(clause.id)
        elif (
            clause.state in (None, "kept")
            and age >= ASK_AFTER
            and seen.delivered_30 > 0
            and seen.opened_30 == 0
        ):
            ask.append(clause.id)
    return Decision(ask=tuple(ask), dormant=tuple(dormant))


def _loads(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


def _when(value: Any) -> datetime | None:
    if value is None:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


async def _live_clauses(pool: Any) -> list[Clause]:
    data = _loads(await pool.fetchval("SELECT dashboard_attention.decay_clauses()")) or {}
    out: list[Clause] = []
    for row in data.get("clauses", []):
        created = _when(row.get("created_at"))
        if created is None:
            continue
        out.append(
            Clause(
                id=uuid.UUID(str(row["id"])),
                created_at=created,
                state=row.get("state"),
                changed_at=_when(row.get("changed_at")),
            )
        )
    return out


async def _stats(pool: Any, ids: Sequence[uuid.UUID], now: datetime) -> dict[uuid.UUID, Stats]:
    rows = await pool.fetch(
        "SELECT h.clause_id, max(h.observed_at) AS last_hit, "
        "count(e.id) FILTER (WHERE e.delivered_at >= $2) AS delivered_30, "
        "count(e.id) FILTER (WHERE e.delivered_at >= $2 AND e.engaged) AS opened_30 "
        "FROM switchboard.break_through_hits h "
        "LEFT JOIN insight_candidates c "
        "  ON c.dedup_key = 'switchboard:break_through:' || h.source_request_id::text "
        "     || ':' || h.clause_id::text "
        "LEFT JOIN insight_engagement e ON e.insight_id = c.id "
        "WHERE h.clause_id = ANY($1::uuid[]) GROUP BY h.clause_id",
        list(ids),
        now - ASK_AFTER,
    )
    return {
        row["clause_id"]: Stats(
            last_hit=row["last_hit"],
            delivered_30=int(row["delivered_30"] or 0),
            opened_30=int(row["opened_30"] or 0),
        )
        for row in rows
    }


def _keep_key(clause_id: uuid.UUID, asked_at: datetime) -> str:
    """One line per ask: a clause kept and later unopened again is asked again."""
    return f"switchboard:{KEEP_CATEGORY}:{clause_id}:{int(asked_at.timestamp() * 1_000_000)}"


async def _propose_keep(pool: Any, clause_id: uuid.UUID, asked_at: datetime) -> bool:
    """Queue the content-free "keep?" line for one ask; ``False`` if it did not queue."""
    key = _keep_key(clause_id, asked_at)
    if await pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1 LIMIT 1", key):
        return True
    broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    result = await broker.propose_insight_candidate(
        pool,
        origin_butler="switchboard",
        priority=KEEP_PRIORITY,
        category=KEEP_CATEGORY,
        dedup_key=key,
        message=KEEP_MESSAGE,
        expires_at=datetime.now(UTC) + _KEEP_TTL,
    )
    return result.get("status") == "accepted"


SWEEP_TIMEOUT_S = 5.0


async def sweep(
    pool: Any, *, now: datetime | None = None, timeout_s: float = SWEEP_TIMEOUT_S
) -> Decision:
    """Run one decay pass, bounded by ``timeout_s``.

    Never raises: decay is best effort and must not fail or stall the delivery job it runs
    before. A timeout is a logged skip; the next hourly pass tries again.
    """
    try:
        return await asyncio.wait_for(_sweep(pool, now=now), timeout_s)
    except TimeoutError:
        logger.warning("break_through_decay_timeout budget_s=%s", timeout_s)
    except Exception as exc:  # noqa: BLE001 -- class only: SQL text is never evidence
        logger.warning("break_through_decay_unavailable error_class=%s", type(exc).__name__)
    return Decision((), ())


async def _sweep(pool: Any, *, now: datetime | None = None) -> Decision:
    now = now or datetime.now(UTC)
    clauses = await _live_clauses(pool)
    if not clauses:
        return Decision((), ())
    decision = decide(clauses, await _stats(pool, [c.id for c in clauses], now), now)
    applied: dict[str, Any] = {"asked": [], "dormant": []}
    if decision.ask or decision.dormant:
        applied = (
            _loads(
                await pool.fetchval(
                    "SELECT dashboard_attention.decay_apply($1::jsonb)",
                    {
                        "ask": [str(i) for i in decision.ask],
                        "dormant": [str(i) for i in decision.dormant],
                    },
                )
            )
            or applied
        )
    # Each ask is keyed by when it was made: the definer's ``changed_at`` for a new ask, the
    # stored one for a clause asked recently whose line never queued.
    asks: dict[uuid.UUID, datetime] = {}
    for item in applied.get("asked", []):
        at = _when(item.get("at"))
        if at is not None:
            asks[uuid.UUID(str(item["id"]))] = at
    for c in clauses:
        if c.state == "asked" and c.changed_at is not None and now - c.changed_at < _RETRY_WINDOW:
            asks.setdefault(c.id, c.changed_at)
    for clause_id, asked_at in sorted(asks.items(), key=lambda kv: str(kv[0])):
        await _propose_keep(pool, clause_id, asked_at)
    return Decision(
        ask=tuple(uuid.UUID(str(i["id"])) for i in applied.get("asked", [])),
        dormant=tuple(uuid.UUID(str(i)) for i in applied.get("dormant", [])),
    )
