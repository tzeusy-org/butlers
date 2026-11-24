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

import hashlib
import json
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
from butlers.api.pagination import InvalidCursorError, Page, decode_cursor, encode_cursor


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, default=str).encode()).hexdigest()[:16]


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/goals", tags=["goals"])

CURSOR_SCOPE = "goals:list:v1"
LEDGER_SOURCE = "goal_ledger"

GoalOrigin = Literal["owner", "obligation", "third_party", "staffer"]
GoalOutcomeFilter = Literal["completed", "partial", "failed"]
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

# Skip directly to the next region key, then read only that region's newest
# distinct goals. DISTINCT(region) would still walk the entire projection, and
# probing effects from newest goals would scan unrelated history for sparse regions.
_REGION_SUMMARIES_SELECT = """
    WITH RECURSIVE region_keys(region) AS (
        (SELECT region FROM switchboard.goal_region_reads ORDER BY region LIMIT 1)
        UNION ALL
        SELECT next_region.region FROM region_keys previous
        CROSS JOIN LATERAL (
            SELECT region FROM switchboard.goal_region_reads
            WHERE region > previous.region ORDER BY region LIMIT 1
        ) next_region
    )
    SELECT r.region, g.id, g.status, g.origin, recent.created_at
    FROM region_keys r
    CROSS JOIN LATERAL (
        SELECT goal_id, created_at FROM switchboard.goal_region_reads
        WHERE region = r.region ORDER BY created_at DESC, goal_id DESC LIMIT $1
    ) recent
    JOIN switchboard.goals g ON g.id = recent.goal_id
    ORDER BY r.region, recent.created_at DESC, g.id DESC
"""


def _is_missing_table(exc: Exception) -> bool:
    return exc.__class__.__name__ == "UndefinedTableError"


async def _region_ledger_is_unprovisioned(pool: Any) -> bool:
    try:
        absent = await pool.fetchval("""
            SELECT pg_catalog.to_regclass('switchboard.goals') IS NULL
               AND pg_catalog.to_regclass('switchboard.effects') IS NULL
               AND pg_catalog.to_regclass('switchboard.goal_region_reads') IS NULL
        """)
    except Exception:
        return False
    return absent is True


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
    outcome: GoalOutcomeFilter | None = Query(None, description="The lead's recorded outcome."),
    since: datetime | None = Query(None, description="created_at >= since (ISO 8601 with offset)."),
    until: datetime | None = Query(None, description="created_at < until (ISO 8601 with offset)."),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Any:
    """Goals newest first (``created_at DESC, id DESC``).

    ``since`` is inclusive and ``until`` exclusive, both on ``created_at``. A cursor is bound to
    the filter set that minted it: replayed under other filters it is a 400.
    """
    for name, value in (("since", since), ("until", until)):
        if value is not None and value.utcoffset() is None:
            raise HTTPException(status_code=422, detail=f"{name} must include a UTC offset")
    if since is not None and until is not None and since >= until:
        raise HTTPException(status_code=422, detail="since must be before until")
    filters = digest([origin, exposed, state, region, outcome, since, until])
    position = None
    if cursor:
        *position, bound = decode_cursor(cursor, (datetime, uuid.UUID, str), scope=CURSOR_SCOPE)
        if bound != filters:
            raise InvalidCursorError()
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
    if outcome is not None:
        clauses.append(f"o.outcome = {bind(outcome)}")
    if since is not None:
        clauses.append(f"g.created_at >= {bind(since)}")
    if until is not None:
        clauses.append(f"g.created_at < {bind(until)}")
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
        encode_cursor((page[-1]["created_at"], page[-1]["id"], filters), scope=CURSOR_SCOPE)
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
                _REGION_SUMMARIES_SELECT,
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
            if not _is_missing_table(exc) or not await _region_ledger_is_unprovisioned(pool):
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
