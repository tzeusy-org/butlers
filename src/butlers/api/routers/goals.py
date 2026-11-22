"""Goal-ledger read API for the dashboard (RFC 0038 D19, bu-0rbswe). Built dark.

Three owner-authenticated reads over ``switchboard.goals`` and its sibling ledger tables:

- ``GET /api/goals`` -- cursor list filtered by origin, exposure, state and region.
- ``GET /api/goals/regions`` -- recent goals and obligations coming due per region.
- ``GET /api/goals/{goal_id}`` -- delegation tree, effects with receipts, approvals with
  lineage, budget and outcome.

The projection is content-free (D17). A ledger outage on the list answers 200 with
``sources_degraded`` naming ``goal_ledger`` (never a truthful-looking empty page); on a detail
it answers 503, distinct from a 404 for an absent goal.

Stop is not served here: the service-listener operation that fences a goal does not exist yet
(``GoalRuntime.stop`` has no dashboard-callable door), and the signed D19 change leaves its
literal route to the Hold/Stop-all owner.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.deps import Db
from butlers.api.models import ApiMeta, ApiResponse, ErrorResponse
from butlers.api.models.goals import (
    GoalApprovalLineage,
    GoalBudget,
    GoalDetail,
    GoalEffect,
    GoalInvocation,
    GoalOutcome,
    GoalSummary,
    RegionGoal,
    RegionObligation,
    RegionSummaries,
    RegionSummary,
)
from butlers.api.pagination import Page, decode_cursor, encode_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/goals", tags=["goals"])

CURSOR_SCOPE = "goals:list:v1"
LEDGER_SOURCE = "goal_ledger"

GoalOrigin = Literal["owner", "obligation", "third_party", "staffer"]
GoalState = Literal[
    "admitted",
    "running",
    "settling",
    "completed",
    "partial",
    "failed",
    "stopped",
    "budget_exhausted",
    "awaiting_approval",
    "recovering",
]

_REGION_SQL = "split_part(e.capability, '__', 1)"
_SUMMARY_SELECT = f"""
    SELECT g.id, g.origin, g.status, g.exposed, g.admitted_exposed, g.chain_depth,
           g.caused_by_goal, g.caused_by_effect, g.created_at, g.updated_at,
           COALESCE((SELECT array_agg(DISTINCT {_REGION_SQL} ORDER BY {_REGION_SQL})
                     FROM switchboard.effects e WHERE e.goal_id = g.id), '{{}}') AS regions,
           o.outcome AS outcome
    FROM switchboard.goals g
    LEFT JOIN switchboard.goal_outcomes o ON o.goal_id = g.id
"""


def _is_missing_table(exc: Exception) -> bool:
    return exc.__class__.__name__ == "UndefinedTableError"


def _ledger_pool(db: DatabaseManager) -> Any:
    """The switchboard pool, or None when the ledger cannot be reached."""
    try:
        return db.pool("switchboard")
    except KeyError:
        return None


def _summary(row: Any) -> GoalSummary:
    return GoalSummary(
        id=row["id"],
        origin=row["origin"],
        state=row["status"],
        exposed=row["exposed"],
        admitted_exposed=row["admitted_exposed"],
        chain_depth=row["chain_depth"],
        caused_by_goal=row["caused_by_goal"],
        caused_by_effect=row["caused_by_effect"],
        regions=list(row["regions"] or []),
        outcome=row["outcome"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get(
    "",
    response_model=Page[GoalSummary],
    responses={400: {"model": ErrorResponse}},
)
async def list_goals(
    *,
    db: Db,
    origin: GoalOrigin | None = Query(None),
    exposed: bool | None = Query(None, description="Exposure: true or false."),
    state: GoalState | None = Query(None),
    region: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Any:
    """Goals newest first (``created_at DESC, id DESC``)."""
    position = decode_cursor(cursor, (datetime, uuid.UUID), scope=CURSOR_SCOPE) if cursor else None
    pool = _ledger_pool(db)
    if pool is None:
        return Page[GoalSummary](items=[], limit=limit, sources_degraded=[LEDGER_SOURCE])

    clauses: list[str] = []
    args: list[Any] = []

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if origin is not None:
        clauses.append(f"g.origin = {bind(origin)}")
    if exposed is not None:
        clauses.append(f"g.exposed = {bind(exposed)}")
    if state is not None:
        clauses.append(f"g.status = {bind(state)}")
    if region is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM switchboard.effects e "
            f"WHERE e.goal_id = g.id AND {_REGION_SQL} = {bind(region)})"
        )
    if position is not None:
        clauses.append(f"(g.created_at, g.id) < ({bind(position[0])}, {bind(position[1])})")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = (
        f"{_SUMMARY_SELECT} {where} ORDER BY g.created_at DESC, g.id DESC LIMIT {bind(limit + 1)}"
    )
    try:
        rows = await pool.fetch(query, *args)
    except Exception as exc:
        if _is_missing_table(exc):
            return Page[GoalSummary](items=[], limit=limit)
        logger.warning("Goal ledger list failed", exc_info=True)
        return Page[GoalSummary](items=[], limit=limit, sources_degraded=[LEDGER_SOURCE])

    page = rows[:limit]
    next_cursor = (
        encode_cursor((page[-1]["created_at"], page[-1]["id"]), scope=CURSOR_SCOPE)
        if len(rows) > limit
        else None
    )
    return Page[GoalSummary](
        items=[_summary(r) for r in page], next_cursor=next_cursor, limit=limit
    )


@router.get("/regions", response_model=ApiResponse[RegionSummaries])
async def region_summaries(
    *,
    db: Db,
    goals_per_region: int = Query(5, ge=1, le=25),
    due_within_hours: int = Query(72, ge=1, le=24 * 30),
) -> Any:
    """Recent goals per region, and each region's schedules coming due.

    A region is a domain: the prefix of the capability identity an effect used. A failed
    source is named in ``sources_degraded``; it never renders as an empty region.
    """
    tracker = DegradedSources(logger)
    regions: dict[str, RegionSummary] = {}

    pool = _ledger_pool(db)
    if pool is None:
        tracker.names.append(LEDGER_SOURCE)
    else:
        try:
            rows = await pool.fetch(
                f"""
                SELECT region, id, status, origin, created_at FROM (
                    SELECT ge.region, g.id, g.status, g.origin, g.created_at,
                           row_number() OVER (PARTITION BY ge.region
                                              ORDER BY g.created_at DESC, g.id DESC) AS rn
                    FROM (SELECT DISTINCT e.goal_id, {_REGION_SQL} AS region
                          FROM switchboard.effects e) ge
                    JOIN switchboard.goals g ON g.id = ge.goal_id
                ) ranked WHERE rn <= $1 ORDER BY region, created_at DESC, id DESC
                """,
                goals_per_region,
            )
            for row in rows:
                summary = regions.setdefault(row["region"], RegionSummary(region=row["region"]))
                summary.recent_goals.append(
                    RegionGoal(
                        id=row["id"],
                        state=row["status"],
                        origin=row["origin"],
                        created_at=row["created_at"],
                    )
                )
        except Exception as exc:
            if not _is_missing_table(exc):
                tracker.mark(LEDGER_SOURCE, msg="Goal ledger region read failed")

    horizon = datetime.now(UTC) + timedelta(hours=due_within_hours)
    for butler in sorted(db.butler_names):
        try:
            rows = await db.pool(butler).fetch(
                "SELECT name, source, cron, next_run_at FROM scheduled_tasks "
                "WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= $1 "
                "ORDER BY next_run_at, name",
                horizon,
            )
        except Exception as exc:
            if not _is_missing_table(exc):
                tracker.mark(f"obligations:{butler}", msg="Obligation source failed")
            continue
        if not rows:
            continue
        summary = regions.setdefault(butler, RegionSummary(region=butler))
        summary.obligations_due.extend(
            RegionObligation(
                name=r["name"], source=r["source"], cron=r["cron"], next_run_at=r["next_run_at"]
            )
            for r in rows
        )

    body = RegionSummaries(
        regions=[regions[name] for name in sorted(regions)], sources_degraded=tracker.names
    )
    meta = (
        ApiMeta.model_validate({"sources_degraded": tracker.names}) if tracker.failed else ApiMeta()
    )
    return ApiResponse[RegionSummaries](data=body, meta=meta)


@router.get(
    "/{goal_id}",
    response_model=ApiResponse[GoalDetail],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def goal_detail(goal_id: uuid.UUID, *, db: Db) -> Any:
    """One goal: delegation tree, effects with receipts, approval lineage, budget, outcome."""
    pool = _ledger_pool(db)
    if pool is None:
        raise HTTPException(status_code=503, detail="Goal ledger unavailable")
    try:
        goal = await pool.fetchrow(f"{_SUMMARY_SELECT} WHERE g.id = $1", goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail="Goal not found")
        invocations = await pool.fetch(
            "SELECT id, parent_invocation_id, role, depth, status, exposed, exposure_cause, "
            "used_turns, max_turns, used_tokens, max_tokens, end_reason, started_at, ended_at "
            "FROM switchboard.invocations WHERE goal_id = $1 ORDER BY depth, created_at, id",
            goal_id,
        )
        effects = await pool.fetch(
            "SELECT id, invocation_id, capability, status, approval_ref, receipt_ref, "
            "delivery_ref, recipient_entity, recipient_channel, created_at, updated_at "
            "FROM switchboard.effects WHERE goal_id = $1 ORDER BY created_at, id",
            goal_id,
        )
        caused = await pool.fetch(
            "SELECT id, caused_by_effect FROM switchboard.goals WHERE caused_by_goal = $1 "
            "ORDER BY created_at, id",
            goal_id,
        )
        budget = await pool.fetchrow(
            "SELECT * FROM switchboard.budgets WHERE goal_id = $1", goal_id
        )
        outcome = await pool.fetchrow(
            "SELECT outcome, final_status, delivery_failure, relied_effects, reply_effect_id, "
            "created_at, resolved_at FROM switchboard.goal_outcomes WHERE goal_id = $1",
            goal_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Goal ledger detail failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Goal ledger unavailable") from exc

    caused_by_effect: dict[Any, list[uuid.UUID]] = {}
    for row in caused:
        caused_by_effect.setdefault(row["caused_by_effect"], []).append(row["id"])
    effect_models = [
        GoalEffect(
            region=row["capability"].split("__", 1)[0],
            **{k: row[k] for k in GoalEffect.model_fields if k != "region"},
        )
        for row in effects
    ]
    approvals = [
        GoalApprovalLineage(
            effect_id=row["id"],
            approval_ref=row["approval_ref"],
            effect_status=row["status"],
            capability=row["capability"],
            caused_by_goal=goal["caused_by_goal"],
            caused_by_effect=goal["caused_by_effect"],
            caused_goals=caused_by_effect.get(row["id"], []),
        )
        for row in effects
        if row["approval_ref"] is not None
    ]
    causes = sorted({r["exposure_cause"] for r in invocations if r["exposure_cause"]})
    detail = GoalDetail(
        **_summary(goal).model_dump(),
        invocations=[
            GoalInvocation(**{k: r[k] for k in GoalInvocation.model_fields}) for r in invocations
        ],
        effects=effect_models,
        approvals=approvals,
        budget=GoalBudget(**{k: budget[k] for k in GoalBudget.model_fields}) if budget else None,
        goal_outcome=(
            GoalOutcome(**{k: outcome[k] for k in GoalOutcome.model_fields}) if outcome else None
        ),
        exposure_causes=causes,
    )
    return ApiResponse[GoalDetail](data=detail)
