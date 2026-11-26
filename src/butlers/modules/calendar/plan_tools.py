"""Read-only ``calendar_plan_change`` (bu-5ii4gj.11 P1b).

One owner verb over a window becomes a multi-event plan: ordered steps with their exact calls and
authority classes, and the events it deliberately skipped. This module only reads this butler's
calendar projection and hands what it read to the pure compiler
(``core/temporal/change_plans.py``). It writes nothing: no provider call, no approvals card, no
action-log row. Applying a plan is a separate, approval-bound step (slice P2).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from butlers.core.general_settings import load_configured_general_timezone
from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.calendar_source_roles import normalize_role
from butlers.core.temporal.change_plans import MAX_WINDOW, BusyInterval, Plan, compile_plan
from butlers.core.temporal.resolution_rows import blocks_owner, resolution_event

from .constants import SOURCE_KIND_PROVIDER, projection_staleness_threshold
from .invitation_guard import pool_of
from .source_staleness import source_is_stale

if TYPE_CHECKING:
    from . import CalendarModule

logger = logging.getLogger(__name__)

#: How far past a cleared window a moved event may land.
CLEAR_HORIZON = timedelta(days=7)

_ROWS_SQL = """
    -- quarantine-exempt: owner-facing plan read; no prompt
    SELECT i.id AS instance_id, i.starts_at AS instance_starts_at, i.ends_at AS instance_ends_at,
           i.status AS instance_status, e.origin_ref, e.all_day, e.status AS event_status,
           e.recurrence_rule, e.metadata AS event_metadata, e.etag AS event_etag,
           s.calendar_id, s.time_role AS source_time_role
    FROM calendar_event_instances AS i
    JOIN calendar_events AS e ON e.id = i.event_id
    JOIN calendar_sources AS s ON s.id = i.source_id
    WHERE i.starts_at < $2 AND i.ends_at > $1
    ORDER BY i.starts_at ASC, i.id ASC
"""

_SOURCES_SQL = """
    SELECT s.source_kind, s.metadata AS source_metadata,
           c.last_success_at, c.last_error_at, c.full_sync_required
    FROM calendar_sources AS s
    LEFT JOIN LATERAL (
        SELECT last_success_at, last_error_at, full_sync_required
        FROM calendar_sync_cursors
        WHERE source_id = s.id
        ORDER BY updated_at DESC
        LIMIT 1
    ) AS c ON TRUE
"""


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, **extra}


def _dedupe(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """One row per provider occurrence: the owner-role copy when a calendar holds it twice."""
    kept: dict[tuple[Any, Any], Mapping[str, Any]] = {}
    for row in rows:
        key = (row.get("origin_ref") or row["instance_id"], row["instance_starts_at"])
        held = kept.get(key)
        if held is None or (
            normalize_role(row.get("source_time_role")) == "owner"
            and normalize_role(held.get("source_time_role")) != "owner"
        ):
            kept[key] = row
    return list(kept.values())


def _sources_stale(sources: Sequence[Mapping[str, Any]], now: datetime) -> bool:
    threshold = projection_staleness_threshold()
    for source in sources:
        if source["source_kind"] != SOURCE_KIND_PROVIDER:
            continue
        meta = source["source_metadata"]
        if isinstance(meta, dict) and meta.get("sync_enabled") is False:
            continue  # an operator-disabled source is not a failure
        if source_is_stale(
            last_success_at=source["last_success_at"],
            last_error_at=source["last_error_at"],
            full_sync_required=bool(source["full_sync_required"]),
            now=now,
            threshold=threshold,
        ):
            return True
    return False


def plan_payload(plan: Plan, window: tuple[datetime, datetime]) -> dict[str, Any]:
    """The tool's JSON: no event title or description, only ids, calls and reasons."""
    return {
        "status": "ok" if plan.reason is None else "refused",
        "reason": plan.reason,
        "kind": plan.kind,
        "plan_id": plan.plan_id,
        "plan_digest": plan.plan_digest,
        "window": {"start": window[0].isoformat(), "end": window[1].isoformat()},
        "steps": [
            {
                "index": s.index,
                "event_ref": s.entry_id,
                "action": s.action,
                "exact_call": s.exact_call,
                "authority": s.authority,
                "reason": s.reason,
                "depends_on": list(s.depends_on),
            }
            for s in plan.steps
        ],
        "skipped": [{"event_ref": s.entry_id, "why": s.why} for s in plan.skipped],
    }


@dataclass(frozen=True)
class CompiledPlan:
    plan: Plan
    window: tuple[datetime, datetime]
    #: The owner's IANA zone name (None for a shift that never needed it).
    zone: str | None


async def compile_for_window(
    module: CalendarModule,
    *,
    kind: Literal["clear_window", "shift_window"],
    window_start: datetime,
    window_end: datetime,
    shift_minutes: int | None = None,
    now: datetime | None = None,
) -> CompiledPlan | dict[str, Any]:
    """Compile a plan from this butler's projection, or a refusal payload. Read-only."""
    if window_start.tzinfo is None or window_end.tzinfo is None:
        return _refused("window_must_be_timezone_aware")
    if window_end <= window_start:
        return _refused("window_must_end_after_it_starts")
    if kind == "shift_window" and not shift_minutes:
        return _refused("shift_minutes_required")
    window = (window_start, window_end)
    if window_end - window_start > MAX_WINDOW:
        # Refused before any read: an oversized window is never partly planned.
        plan = compile_plan(kind, window, [], now=now or datetime.now(UTC))
        return plan_payload(plan, window)
    pool = pool_of(module)
    if pool is None:
        return _refused("database_unavailable")
    moment = now or datetime.now(UTC)
    delta = timedelta(minutes=shift_minutes) if shift_minutes else None
    lo = window_start + min(delta or timedelta(0), timedelta(0))
    hi = window_end + (
        CLEAR_HORIZON if kind == "clear_window" else max(delta or timedelta(0), timedelta(0))
    )
    try:
        rows = _dedupe([dict(r) for r in await pool.fetch(_ROWS_SQL, lo, hi)])
        sources = [dict(r) for r in await pool.fetch(_SOURCES_SQL)]
        zone_name = await load_configured_general_timezone(pool)
    except Exception:
        logger.warning("calendar_plan_change: projection unreadable", exc_info=True)
        return _refused("projection_unreadable")
    if not zone_name:
        # Working hours are the owner's, in their zone: no zone, no guess (either verb).
        return _refused("owner_zone_unknown")

    busy_rows = [r for r in rows if blocks_owner(r)]
    busy = [
        BusyInterval(str(r["instance_id"]), r["instance_starts_at"], r["instance_ends_at"])
        for r in busy_rows
    ]
    free: list[tuple[datetime, datetime]] = []
    if kind == "clear_window":
        free = cr.free_windows(
            [(b.start_at, b.end_at) for b in busy],
            max(window_end, moment),
            hi,
            ZoneInfo(str(zone_name)),
        )
    hours = None
    if kind == "shift_window" and zone_name:
        # Whole owner-local days, not [lo, hi): a destination in the day's working hours before
        # ``lo`` or after ``hi`` is still inside the owner's working day.
        zone = ZoneInfo(str(zone_name))
        day_lo = lo.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        day_hi = hi.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        hours = cr.free_windows([], day_lo, day_hi, zone)
    plan = compile_plan(
        kind,
        window,
        [resolution_event(r) for r in busy_rows],
        now=moment,
        free_slots=free,
        busy=busy,
        delta=delta,
        all_day_entry_ids=frozenset(str(r["instance_id"]) for r in busy_rows if r.get("all_day")),
        stale_source=_sources_stale(sources, moment),
        working_hours=hours,
    )
    return CompiledPlan(plan, window, str(zone_name) if zone_name else None)


async def plan_change(
    module: CalendarModule,
    *,
    kind: Literal["clear_window", "shift_window"],
    window_start: datetime,
    window_end: datetime,
    shift_minutes: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile a plan from this butler's projection. Read-only."""
    compiled = await compile_for_window(
        module,
        kind=kind,
        window_start=window_start,
        window_end=window_end,
        shift_minutes=shift_minutes,
        now=now,
    )
    if isinstance(compiled, dict):
        return compiled
    return plan_payload(compiled.plan, compiled.window)


def register_plan_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("core")
    async def calendar_plan_change(
        kind: Literal["clear_window", "shift_window"],
        window_start: datetime,
        window_end: datetime,
        shift_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Plan a multi-event change over a window. Read-only: nothing is written or asked.

        ``clear_window`` moves each movable event out to the earliest free slot after the window
        and declines invited meetings; ``shift_window`` moves each movable event by
        ``shift_minutes`` (signed) and leaves invited meetings alone. Returns
        ``{plan_id, plan_digest, steps, skipped}``: each step names its exact call and authority
        class, and every skipped event says why. A window over 25 hours, more than 16 steps, or a
        stale or partial calendar source gives no steps and the reason
        ``plan_unavailable``. Titles are never returned.
        """
        return await plan_change(
            module,
            kind=kind,
            window_start=window_start,
            window_end=window_end,
            shift_minutes=shift_minutes,
        )


def register_plan_apply_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:
    from .plan_apply import apply_plan
    from .plan_undo import undo_plan

    @_tool("core")
    async def calendar_apply_plan(
        plan_id: str,
        plan_digest: str,
        kind: Literal["clear_window", "shift_window"],
        window_start: datetime,
        window_end: datetime,
        shift_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Apply a compiled plan by parking one approvals card per move step. Returns ``pending``.

        The plan is compiled again and refused (``plan_changed``, nothing written) unless its id and
        digest still match. Nothing runs until the owner approves each card, and a card declines if
        its event changed since the plan or a step it depends on was not applied. Re-applying the
        same plan replays the recorded receipts and parks only the steps that have none. An invited
        meeting's decline parks ``calendar_respond`` for the owner like any other response.
        """
        return await apply_plan(
            module,
            butler_name,
            plan_id=plan_id,
            plan_digest=plan_digest,
            kind=kind,
            window_start=window_start,
            window_end=window_end,
            shift_minutes=shift_minutes,
        )

    @_tool("core")
    async def calendar_undo_plan(plan_id: str) -> dict[str, Any]:
        """Undo an applied plan: one approvals card per reversal. Returns ``pending``.

        Only steps whose move really applied are reversed, each bound to the version that step
        left: a card declines (``changed_since_move``, nothing written) if its event changed since.
        Declined, pending, skipped and uncertain steps are never reversed. Calling it again
        returns the cards already parked.
        """
        return await undo_plan(module, butler_name, plan_id=plan_id)
