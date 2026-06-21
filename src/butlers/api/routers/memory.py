"""Memory system endpoints — episodes, facts, rules, stats, activity.

Provides read-only endpoints for browsing memory data across all butler
databases that expose memory tables. The router gracefully skips pools where
memory tables are unavailable, so dedicated-memory deployments are optional.

Also exposes admin endpoints for retention policies, compaction log, and
the inspect search bar (§10.2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from datetime import UTC

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.memory import (
    _DEFAULT_EMBEDDING_MODEL,
    ButlerMemoryStats,
    CompactionLogEntry,
    ConsolidationStatus,
    EntityDetail,
    EntityInfoEntry,
    EntitySummary,
    Episode,
    Fact,
    MemoryActivity,
    MemoryInspectResult,
    MemoryRetentionPolicy,
    MemoryStats,
    ReembedPendingCounts,
    ReembedRunRequest,
    ReembedRunResult,
    Rule,
    UpdateEntityRequest,
    UpdateRetentionPoliciesRequest,
)
from butlers.api.routers import audit as _audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _memory_pool_names(db: DatabaseManager) -> list[str]:
    """Return butler names to probe for memory tables."""
    return sorted(db.butler_names)


def _memory_pools(db: DatabaseManager) -> list[tuple[str, object]]:
    """Return available pools to probe for memory queries."""
    pools: list[tuple[str, object]] = []
    for name in _memory_pool_names(db):
        try:
            pools.append((name, db.pool(name)))
        except KeyError:
            continue
    return pools


def _any_pool(db: DatabaseManager) -> object:
    """Return any available pool for querying shared schema tables.

    Since public.entities is accessible from every butler's pool, we just
    need one working connection.  Raises HTTPException(503) if none available.
    """
    for name in _memory_pool_names(db):
        try:
            return db.pool(name)
        except KeyError:
            continue
    raise HTTPException(status_code=503, detail="No database pools available")


async def _fan_out_memory_queries(
    db: DatabaseManager,
    *,
    query_name: str,
    query_fn: Callable[[str, object], Awaitable[object | None]],
    butler_filter: str | None = None,
) -> list[object]:
    """Run a query across candidate pools and skip pools without memory schema.

    When *butler_filter* is provided the fan-out is restricted to the single
    pool owned by that butler.  If that butler is unknown the function returns
    immediately with an empty list, avoiding unnecessary pool probing.
    """
    if butler_filter is not None:
        # Narrow to exactly one pool; return early when the butler is unknown.
        try:
            pools: list[tuple[str, object]] = [(butler_filter, db.pool(butler_filter))]
        except KeyError:
            logger.debug(
                "Butler %r not found in pool registry; returning empty for query %s",
                butler_filter,
                query_name,
            )
            return []
    else:
        pools = _memory_pools(db)
    if not pools:
        logger.info("No database pools available for memory query: %s", query_name)
        return []

    async def _run(name: str, pool: object) -> object | None:
        try:
            return await query_fn(name, pool)
        except Exception:
            logger.debug(
                "Skipping pool %s for memory query %s (pool lacks memory tables or query failed)",
                name,
                query_name,
                exc_info=True,
            )
            return None

    results = await asyncio.gather(*(_run(name, pool) for name, pool in pools))
    return [result for result in results if result is not None]


def _parse_jsonb(value):
    """Parse a JSONB value that may be a string or already decoded."""
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return value


def _parse_tags(value):
    """Parse a JSONB tags array that may be a string or already decoded."""
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def _sort_rows_by_created_at(rows: list[object]) -> list[object]:
    """Sort rows by created_at DESC."""
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


async def _resolve_entity_names(db: DatabaseManager, facts: list[Fact]) -> list[Fact]:
    """Batch-resolve entity_id and object_entity_id → canonical_name for a list of Facts."""
    entity_ids = {f.entity_id for f in facts if f.entity_id}
    entity_ids |= {f.object_entity_id for f in facts if f.object_entity_id}
    if not entity_ids:
        return facts
    pool = _any_pool(db)
    rows = await pool.fetch(
        "SELECT id, canonical_name FROM public.entities WHERE id = ANY($1)",
        [_uuid.UUID(eid) for eid in entity_ids],
    )
    name_map = {str(r["id"]): r["canonical_name"] for r in rows}
    for f in facts:
        if f.entity_id and f.entity_id in name_map:
            f.entity_name = name_map[f.entity_id]
        if f.object_entity_id and f.object_entity_id in name_map:
            f.object_entity_name = name_map[f.object_entity_id]
    return facts


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=ApiResponse[MemoryStats])
async def get_memory_stats(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[MemoryStats]:
    """Return aggregated counts across all memory tiers."""

    async def _stats_for_pool(butler_name: str, pool: object) -> dict[str, object]:
        # Latest consolidation run for THIS pool's butler, read from the shared
        # public.consolidation_runs audit table (core_119). Scoped per-butler so
        # the fan-out picks the globally-latest run without double counting.
        # Degrade gracefully when the audit table is absent (e.g. core_119 not
        # yet applied) so the established episode/fact/rule counts still return.
        try:
            last_run = await pool.fetchrow(
                "SELECT consolidated_at, facts_produced FROM public.consolidation_runs"
                " WHERE butler = $1 ORDER BY consolidated_at DESC LIMIT 1",
                butler_name,
            )
        except Exception:
            logger.warning(
                "Failed to fetch latest consolidation run for butler %s; "
                "omitting consolidation fields for this pool",
                butler_name,
                exc_info=True,
            )
            last_run = None
        return {
            "total_episodes": await pool.fetchval("SELECT count(*) FROM episodes") or 0,
            "unconsolidated_episodes": await pool.fetchval(
                "SELECT count(*) FROM episodes WHERE consolidated = false"
            )
            or 0,
            "dead_letter_episodes": await pool.fetchval(
                "SELECT count(*) FROM episodes WHERE consolidation_status = 'dead_letter'"
            )
            or 0,
            "total_facts": await pool.fetchval("SELECT count(*) FROM facts") or 0,
            "active_facts": await pool.fetchval(
                "SELECT count(*) FROM facts WHERE validity = 'active'"
            )
            or 0,
            "fading_facts": await pool.fetchval(
                "SELECT count(*) FROM facts WHERE validity = 'fading'"
            )
            or 0,
            "total_rules": await pool.fetchval("SELECT count(*) FROM rules") or 0,
            "candidate_rules": await pool.fetchval(
                "SELECT count(*) FROM rules WHERE maturity = 'candidate'"
            )
            or 0,
            "established_rules": await pool.fetchval(
                "SELECT count(*) FROM rules WHERE maturity = 'established'"
            )
            or 0,
            "proven_rules": await pool.fetchval(
                "SELECT count(*) FROM rules WHERE maturity = 'proven'"
            )
            or 0,
            "anti_pattern_rules": await pool.fetchval(
                "SELECT count(*) FROM rules WHERE maturity = 'anti_pattern'"
            )
            or 0,
            "last_consolidation_at": last_run["consolidated_at"] if last_run else None,
            "last_consolidation_facts_produced": (last_run["facts_produced"] if last_run else None),
        }

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="stats",
        query_fn=_stats_for_pool,
    )

    totals = MemoryStats()
    # Track the globally-latest consolidation run across pools so the header band
    # shows a single "last write-up" timestamp and its facts_produced count.
    latest_consolidation_at = None
    for row in per_pool:
        totals.total_episodes += row["total_episodes"]
        totals.unconsolidated_episodes += row["unconsolidated_episodes"]
        totals.dead_letter_episodes += row["dead_letter_episodes"]
        totals.total_facts += row["total_facts"]
        totals.active_facts += row["active_facts"]
        totals.fading_facts += row["fading_facts"]
        totals.total_rules += row["total_rules"]
        totals.candidate_rules += row["candidate_rules"]
        totals.established_rules += row["established_rules"]
        totals.proven_rules += row["proven_rules"]
        totals.anti_pattern_rules += row["anti_pattern_rules"]

        run_at = row["last_consolidation_at"]
        if run_at is not None and (
            latest_consolidation_at is None or run_at > latest_consolidation_at
        ):
            latest_consolidation_at = run_at
            totals.last_consolidation_at = str(run_at)
            totals.last_consolidation_facts_produced = row["last_consolidation_facts_produced"]

    return ApiResponse[MemoryStats](data=totals)


# ---------------------------------------------------------------------------
# GET /api/memory/episodes
# ---------------------------------------------------------------------------


@router.get("/episodes", response_model=PaginatedResponse[Episode])
async def list_episodes(
    butler: str | None = Query(None, description="Filter by butler name"),
    consolidated: bool | None = Query(None, description="Filter by consolidated status"),
    status: ConsolidationStatus | None = Query(
        None,
        description=(
            "Filter by consolidation lifecycle status (pending|consolidated|failed|dead_letter)"
        ),
    ),
    since: str | None = Query(None, description="Created after this timestamp"),
    until: str | None = Query(None, description="Created before this timestamp"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Episode]:
    """List episodes with optional filters, paginated."""
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if butler is not None:
        conditions.append(f"butler = ${idx}")
        args.append(butler)
        idx += 1

    if consolidated is not None:
        conditions.append(f"consolidated = ${idx}")
        args.append(consolidated)
        idx += 1

    if status is not None:
        conditions.append(f"consolidation_status = ${idx}")
        args.append(status.value)
        idx += 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"created_at <= ${idx}")
        args.append(until)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    row_limit = offset + limit

    async def _query_pool(_: str, pool: object) -> tuple[int, list[object]]:
        total = await pool.fetchval(f"SELECT count(*) FROM episodes{where}", *args) or 0
        rows = await pool.fetch(
            f"SELECT id, butler, session_id, content, importance, reference_count,"
            f" consolidated, consolidation_status, created_at, last_referenced_at,"
            f" expires_at, metadata"
            f" FROM episodes{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}",
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="episodes",
        query_fn=_query_pool,
        butler_filter=butler,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_episode(r) for r in rows]

    return PaginatedResponse[Episode](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/episodes/{episode_id}
# ---------------------------------------------------------------------------


@router.get("/episodes/{episode_id}", response_model=ApiResponse[Episode])
async def get_episode(
    episode_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Episode]:
    """Return a single episode by ID."""

    async def _query_pool(_: str, pool: object):
        return await pool.fetchrow(
            "SELECT id, butler, session_id, content, importance, reference_count,"
            " consolidated, consolidation_status, created_at, last_referenced_at,"
            " expires_at, metadata"
            " FROM episodes WHERE id = $1",
            episode_id,
        )

    rows = await _fan_out_memory_queries(
        db,
        query_name="episode_by_id",
        query_fn=_query_pool,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Episode not found")

    return ApiResponse[Episode](data=_row_to_episode(rows[0]))


# ---------------------------------------------------------------------------
# GET /api/memory/facts
# ---------------------------------------------------------------------------


@router.get("/facts", response_model=PaginatedResponse[Fact])
async def list_facts(
    q: str | None = Query(None, description="Text search query"),
    scope: str | None = Query(None, description="Filter by scope"),
    validity: str | None = Query(None, description="Filter by validity"),
    permanence: str | None = Query(None, description="Filter by permanence"),
    subject: str | None = Query(None, description="Filter by subject"),
    source_episode_id: str | None = Query(
        None, description="Filter to facts derived from this episode"
    ),
    importance_min: float | None = Query(
        None, description="Filter to facts with importance >= this threshold"
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Fact]:
    """List/search facts with optional filters, paginated."""
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(f"search_vector @@ plainto_tsquery('english', ${idx})")
        args.append(q)
        idx += 1

    if scope is not None:
        conditions.append(f"scope = ${idx}")
        args.append(scope)
        idx += 1

    if validity is not None:
        conditions.append(f"validity = ${idx}")
        args.append(validity)
        idx += 1

    if permanence is not None:
        conditions.append(f"permanence = ${idx}")
        args.append(permanence)
        idx += 1

    if subject is not None:
        conditions.append(f"subject = ${idx}")
        args.append(subject)
        idx += 1

    if source_episode_id is not None:
        conditions.append(f"source_episode_id = ${idx}")
        args.append(source_episode_id)
        idx += 1

    if importance_min is not None:
        conditions.append(f"importance >= ${idx}")
        args.append(importance_min)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    row_limit = offset + limit

    async def _query_pool(_: str, pool: object) -> tuple[int, list[object]]:
        total = await pool.fetchval(f"SELECT count(*) FROM facts{where}", *args) or 0
        rows = await pool.fetch(
            f"SELECT id, subject, predicate, content, importance, confidence,"
            f" decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            f" entity_id, object_entity_id, validity, scope, reference_count,"
            f" created_at, last_referenced_at,"
            f" last_confirmed_at, tags, metadata"
            f" FROM facts{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}",
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="facts",
        query_fn=_query_pool,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_fact(r) for r in rows]
    data = await _resolve_entity_names(db, data)

    return PaginatedResponse[Fact](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/facts/{fact_id}
# ---------------------------------------------------------------------------


@router.get("/facts/{fact_id}", response_model=ApiResponse[Fact])
async def get_fact(
    fact_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Fact]:
    """Return a single fact by ID."""

    async def _query_pool(_: str, pool: object):
        row = await pool.fetchrow(
            "SELECT id, subject, predicate, content, importance, confidence,"
            " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            " entity_id, object_entity_id, validity, scope, reference_count,"
            " created_at, last_referenced_at,"
            " last_confirmed_at, tags, metadata"
            " FROM facts WHERE id = $1",
            fact_id,
        )
        if row is None:
            return None
        # Reverse-lookup the fact that supersedes THIS one (if any).  Runs on the
        # same pool that owns the fact, so we never cross butler schemas.
        superseder = await pool.fetchrow(
            "SELECT id FROM facts WHERE supersedes_id = $1 LIMIT 1",
            fact_id,
        )
        return (row, superseder)

    results = await _fan_out_memory_queries(
        db,
        query_name="fact_by_id",
        query_fn=_query_pool,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Fact not found")

    row, superseder = results[0]
    fact = _row_to_fact(row)
    if superseder is not None:
        fact.superseded_by = str(superseder["id"])
    await _resolve_entity_names(db, [fact])
    return ApiResponse[Fact](data=fact)


# ---------------------------------------------------------------------------
# POST /api/memory/facts/{fact_id}/confirm
# ---------------------------------------------------------------------------

_FACT_SELECT_COLUMNS = (
    "SELECT id, subject, predicate, content, importance, confidence,"
    " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
    " entity_id, object_entity_id, validity, scope, reference_count,"
    " created_at, last_referenced_at,"
    " last_confirmed_at, tags, metadata"
    " FROM facts WHERE id = $1"
)


@router.post("/facts/{fact_id}/confirm", response_model=ApiResponse[Fact])
async def confirm_fact(
    fact_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Fact]:
    """Re-ink a fact: reset its confidence-decay timer (set last_confirmed_at=now).

    Delegates to ``storage.confirm_memory`` (the same call backing the MCP
    ``memory_confirm`` tool) on whichever butler pool owns the fact, then
    returns the updated row so the fact-detail commit footer can reflect the
    fresh confirmation immediately.

    Errors:
    - 400: ``fact_id`` is not a valid UUID.
    - 404: no memory pool holds a fact with this id.
    - 503: no database pools are available.
    """
    from butlers.modules.memory import storage as _storage

    try:
        fact_uuid = _uuid.UUID(fact_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid fact id (must be a UUID)") from exc

    pools = _memory_pools(db)
    if not pools:
        raise HTTPException(status_code=503, detail="No database pools available")

    # Locate the pool that owns this fact, confirm it there, and re-fetch the
    # updated row.  Each pool probe is guarded so a pool lacking memory tables is
    # skipped rather than failing the whole request.
    for _name, pool in pools:
        try:
            confirmed = await _storage.confirm_memory(pool, "fact", fact_uuid)
        except Exception:
            logger.debug(
                "Skipping pool %s while confirming fact %s (pool lacks memory tables or failed)",
                _name,
                fact_id,
                exc_info=True,
            )
            continue
        if not confirmed:
            continue
        row = await pool.fetchrow(_FACT_SELECT_COLUMNS, fact_uuid)
        if row is None:
            continue
        fact = _row_to_fact(row)
        await _resolve_entity_names(db, [fact])
        return ApiResponse[Fact](data=fact)

    raise HTTPException(status_code=404, detail="Fact not found")


# ---------------------------------------------------------------------------
# POST /api/memory/facts/{fact_id}/retract
# ---------------------------------------------------------------------------


@router.post("/facts/{fact_id}/retract", response_model=ApiResponse[Fact])
async def retract_fact(
    fact_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Fact]:
    """Retract a fact: mark it invalid (set validity='retracted').

    The inverse of confirm.  Delegates to ``storage.forget_memory`` (the same
    call backing the MCP ``memory_forget`` tool) on whichever butler pool owns
    the fact, then returns the updated row so the fact-detail view can reflect
    the retracted state immediately.  The row remains in the database but is
    excluded from active retrieval.

    Errors:
    - 400: ``fact_id`` is not a valid UUID.
    - 404: no memory pool holds a fact with this id.
    - 503: no database pools are available.
    """
    from butlers.modules.memory import storage as _storage

    try:
        fact_uuid = _uuid.UUID(fact_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid fact id (must be a UUID)") from exc

    pools = _memory_pools(db)
    if not pools:
        raise HTTPException(status_code=503, detail="No database pools available")

    # Locate the pool that owns this fact, retract it there, and re-fetch the
    # updated row.  Each pool probe is guarded so a pool lacking memory tables is
    # skipped rather than failing the whole request.
    for _name, pool in pools:
        try:
            retracted = await _storage.forget_memory(pool, "fact", fact_uuid)
        except Exception:
            logger.debug(
                "Skipping pool %s while retracting fact %s (pool lacks memory tables or failed)",
                _name,
                fact_id,
                exc_info=True,
            )
            continue
        if not retracted:
            continue
        row = await pool.fetchrow(_FACT_SELECT_COLUMNS, fact_uuid)
        if row is None:
            continue
        fact = _row_to_fact(row)
        await _resolve_entity_names(db, [fact])
        return ApiResponse[Fact](data=fact)

    raise HTTPException(status_code=404, detail="Fact not found")


# ---------------------------------------------------------------------------
# GET /api/memory/rules
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=PaginatedResponse[Rule])
async def list_rules(
    q: str | None = Query(None, description="Text search query"),
    scope: str | None = Query(None, description="Filter by scope"),
    maturity: str | None = Query(None, description="Filter by maturity"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Rule]:
    """List/search rules with optional filters, paginated."""
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(f"search_vector @@ plainto_tsquery('english', ${idx})")
        args.append(q)
        idx += 1

    if scope is not None:
        conditions.append(f"scope = ${idx}")
        args.append(scope)
        idx += 1

    if maturity is not None:
        conditions.append(f"maturity = ${idx}")
        args.append(maturity)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    row_limit = offset + limit

    async def _query_pool(_: str, pool: object) -> tuple[int, list[object]]:
        total = await pool.fetchval(f"SELECT count(*) FROM rules{where}", *args) or 0
        rows = await pool.fetch(
            f"SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
            f" effectiveness_score, applied_count, success_count, harmful_count,"
            f" source_episode_id, source_butler, created_at, last_applied_at,"
            f" last_evaluated_at, tags, metadata"
            f" FROM rules{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}",
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="rules",
        query_fn=_query_pool,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_rule(r) for r in rows]

    return PaginatedResponse[Rule](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/rules/{rule_id}
# ---------------------------------------------------------------------------


@router.get("/rules/{rule_id}", response_model=ApiResponse[Rule])
async def get_rule(
    rule_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Rule]:
    """Return a single rule by ID."""

    async def _query_pool(_: str, pool: object):
        return await pool.fetchrow(
            "SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
            " effectiveness_score, applied_count, success_count, harmful_count,"
            " source_episode_id, source_butler, created_at, last_applied_at,"
            " last_evaluated_at, tags, metadata"
            " FROM rules WHERE id = $1",
            rule_id,
        )

    rows = await _fan_out_memory_queries(
        db,
        query_name="rule_by_id",
        query_fn=_query_pool,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Rule not found")

    return ApiResponse[Rule](data=_row_to_rule(rows[0]))


# ---------------------------------------------------------------------------
# GET /api/memory/activity
# ---------------------------------------------------------------------------


@router.get("/activity", response_model=ApiResponse[list[MemoryActivity]])
async def list_activity(
    limit: int = Query(50, ge=1, le=200, description="Max activity items to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryActivity]]:
    """Return recent memory activity interleaved from all three tables."""

    async def _query_pool(_: str, pool: object) -> tuple[list[object], list[object], list[object]]:
        episode_rows = await pool.fetch(
            "SELECT id, butler, content, created_at"
            " FROM episodes ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        fact_rows = await pool.fetch(
            "SELECT id, subject, predicate, source_butler, created_at"
            " FROM facts ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        rule_rows = await pool.fetch(
            "SELECT id, content, source_butler, created_at"
            " FROM rules ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return list(episode_rows), list(fact_rows), list(rule_rows)

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="activity",
        query_fn=_query_pool,
    )

    items: list[MemoryActivity] = []
    for episode_rows, fact_rows, rule_rows in per_pool:
        for r in episode_rows:
            content = r["content"] or ""
            items.append(
                MemoryActivity(
                    id=str(r["id"]),
                    type="episode",
                    summary=content[:100] + ("..." if len(content) > 100 else ""),
                    butler=r["butler"],
                    created_at=str(r["created_at"]),
                )
            )

        for r in fact_rows:
            items.append(
                MemoryActivity(
                    id=str(r["id"]),
                    type="fact",
                    summary=f"{r['subject']}: {r['predicate']}",
                    butler=r["source_butler"],
                    created_at=str(r["created_at"]),
                )
            )

        for r in rule_rows:
            content = r["content"] or ""
            items.append(
                MemoryActivity(
                    id=str(r["id"]),
                    type="rule",
                    summary=content[:100] + ("..." if len(content) > 100 else ""),
                    butler=r["source_butler"],
                    created_at=str(r["created_at"]),
                )
            )

    # Sort by created_at descending and trim to limit
    items.sort(key=lambda a: a.created_at, reverse=True)
    items = items[:limit]

    return ApiResponse[list[MemoryActivity]](data=items)


# ---------------------------------------------------------------------------
# GET /api/memory/entities
# ---------------------------------------------------------------------------

# Role priority for entity list ordering. Lower value = higher in the list.
# Add new roles here to extend the ranking; unlisted roles fall through to ELSE.
_ENTITY_ROLE_RANK: dict[str, int] = {
    "owner": 0,
    "family": 1,
}

# Sentinel rank for entities with none of the prioritised roles.
_ENTITY_ROLE_RANK_DEFAULT: int = 99


def _entity_role_priority(roles: list[str]) -> int:
    """Return the lowest (most-prioritised) role rank for a list of entity roles."""
    if not roles:
        return _ENTITY_ROLE_RANK_DEFAULT
    return min(_ENTITY_ROLE_RANK.get(r, _ENTITY_ROLE_RANK_DEFAULT) for r in roles)


def _sort_entity_summaries(items: list[EntitySummary]) -> list[EntitySummary]:
    """Sort EntitySummary items by role priority + Dunbar score, then non-person.

    Sort order (stable, ascending by key tuple):
      1. is_non_person: 0 for person entities, 1 for all others
         — person-entities always sort before non-person entities
      2. role_priority: lower value = higher priority (owner=0, family=1, default=99)
         — both "no roles" and "unranked roles" map to the same default rank (99);
           they are further ordered by Dunbar score and name (keys 3 & 4)
      3. dunbar_score: descending (negated so lower key = higher score)
         — None treated as 0.0 (no interactions yet)
      4. canonical_name: ascending tiebreaker
    """
    return sorted(
        items,
        key=lambda e: (
            0 if e.entity_type == "person" else 1,
            _entity_role_priority(e.roles) if e.entity_type == "person" else 0,
            -(e.dunbar_score or 0.0) if e.entity_type == "person" else 0.0,
            e.canonical_name,
        ),
    )


async def _compute_entity_dunbar_map(
    db: DatabaseManager,
) -> dict[str, dict[str, float | int | None]]:
    """Return a mapping of entity_id → {dunbar_tier, dunbar_score} for all scored contacts.

    Uses the relationship butler's pool to compute decay scores.  Gracefully
    returns an empty dict if the relationship pool is unavailable or the scoring
    query fails (e.g. relationship schema not configured in this deployment).
    """
    from butlers.tools.relationship.dunbar import compute_tier_ranking

    try:
        rel_pool = db.pool("relationship")
    except KeyError:
        logger.debug("Relationship pool not available; skipping Dunbar enrichment")
        return {}
    try:
        ranked = await compute_tier_ranking(rel_pool)
    except Exception:
        logger.debug("Dunbar scoring failed; skipping enrichment", exc_info=True)
        return {}

    result: dict[str, dict[str, float | int | None]] = {}
    for entry in ranked:
        entity_id = entry.get("entity_id")
        if entity_id is None:
            continue

        eid = str(entity_id)
        score = float(entry.get("dunbar_score") or 0.0)
        existing = result.get(eid)
        existing_score = float(existing.get("dunbar_score") or 0.0) if existing else -1.0
        if existing is not None and existing_score >= score:
            continue

        result[eid] = {
            "dunbar_tier": entry["dunbar_tier"],
            "dunbar_score": entry["dunbar_score"],
        }

    return result


@router.get("/entities", response_model=PaginatedResponse[EntitySummary])
async def list_entities(
    q: str | None = Query(None, description="Search canonical_name and aliases"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    unidentified: bool | None = Query(
        None,
        description="Filter by unidentified status: true=only unidentified, false=only confirmed",
    ),
    archived: bool = Query(
        False,
        description="When true, return only archived entities; when false (default), exclude them",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[EntitySummary]:
    """List entities from public.entities with optional search and type filter.

    Sort order: person-entities first (by role priority, then Dunbar score
    descending), followed by non-person entities (alphabetical).  Search
    results preserve this same ordering.
    """
    pool = _any_pool(db)

    conditions: list[str] = [
        "(e.metadata->>'merged_into') IS NULL",
        "(e.metadata->>'deleted_at') IS NULL",
        "NOT ('google_account' = ANY(e.roles))",
    ]

    if archived:
        conditions.append("(e.metadata->>'archived_at') IS NOT NULL")
    else:
        conditions.append("(e.metadata->>'archived_at') IS NULL")
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(
            f"(LOWER(e.canonical_name) LIKE '%' || ${idx} || '%'"
            f" OR EXISTS (SELECT 1 FROM UNNEST(e.aliases) AS a"
            f" WHERE LOWER(a) LIKE '%' || ${idx} || '%')"
            f" OR e.id::text LIKE '%' || ${idx} || '%')"
        )
        args.append(q.lower())
        idx += 1

    if entity_type is not None:
        types = [t.strip() for t in entity_type.split(",") if t.strip()]
        if len(types) == 1:
            conditions.append(f"e.entity_type = ${idx}")
            args.append(types[0])
            idx += 1
        elif types:
            conditions.append(f"e.entity_type = ANY(${idx}::text[])")
            args.append(types)
            idx += 1

    if unidentified is True:
        conditions.append("COALESCE((e.metadata->>'unidentified')::boolean, false) IS TRUE")
    elif unidentified is False:
        conditions.append("COALESCE((e.metadata->>'unidentified')::boolean, false) IS NOT TRUE")

    where = " WHERE " + " AND ".join(conditions)

    total = (
        await pool.fetchval(
            f"SELECT count(*) FROM public.entities e{where}",
            *args,
        )
        or 0
    )

    # Fetch all matching rows — sorting is done in Python after Dunbar enrichment
    # so that role-priority + score ordering is consistent across pages.
    rows = await pool.fetch(
        f"SELECT e.id, e.canonical_name, e.entity_type, e.aliases,"
        f" e.created_at, e.updated_at,"
        f" e.roles AS linked_contact_roles,"
        f" COALESCE((e.metadata->>'unidentified')::boolean, false) AS unidentified,"
        f" e.metadata->>'source_butler' AS source_butler,"
        f" e.metadata->>'source_scope' AS source_scope,"
        f" (e.metadata->>'archived_at') IS NOT NULL AS archived"
        f" FROM public.entities e{where}",
        *args,
    )

    # Fact counts live in per-butler schemas — fan out across memory pools.
    entity_ids = [r["id"] for r in rows]
    fact_counts: dict[str, int] = {}
    if entity_ids:

        async def _count_facts(_: str, fpool: object) -> dict[str, int]:
            fc_rows = await fpool.fetch(
                "SELECT entity_id, count(*) AS cnt FROM facts"
                " WHERE entity_id = ANY($1) AND validity = 'active'"
                " GROUP BY entity_id",
                entity_ids,
            )
            return {str(r["entity_id"]): r["cnt"] for r in fc_rows}

        per_pool = await _fan_out_memory_queries(
            db, query_name="entity_fact_counts", query_fn=_count_facts
        )
        for pool_counts in per_pool:
            for eid_str, cnt in pool_counts.items():
                fact_counts[eid_str] = fact_counts.get(eid_str, 0) + cnt

    # Compute Dunbar scores for all person-entities via the relationship pool.
    dunbar_map = await _compute_entity_dunbar_map(db)

    all_items = []
    for r in rows:
        eid = str(r["id"])
        entity_type_val = r["entity_type"]
        dunbar_info = dunbar_map.get(eid) if entity_type_val == "person" else None
        all_items.append(
            EntitySummary(
                id=eid,
                canonical_name=r["canonical_name"],
                entity_type=entity_type_val,
                aliases=list(r["aliases"]) if r["aliases"] else [],
                roles=list(r["linked_contact_roles"]) if r["linked_contact_roles"] else [],
                fact_count=fact_counts.get(eid, 0),
                # public.contacts retired (bu-jnaa3): no contact row to link.
                linked_contact_id=None,
                unidentified=r["unidentified"],
                source_butler=r["source_butler"],
                source_scope=r["source_scope"],
                archived=r["archived"],
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
                dunbar_tier=dunbar_info["dunbar_tier"] if dunbar_info else None,
                dunbar_score=dunbar_info["dunbar_score"] if dunbar_info else None,
            )
        )

    # Sort by role priority + Dunbar score, then paginate in Python.
    sorted_items = _sort_entity_summaries(all_items)
    data = sorted_items[offset : offset + limit]

    return PaginatedResponse[EntitySummary](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}", response_model=ApiResponse[EntityDetail])
async def get_entity(
    entity_id: str,
    facts_offset: int = Query(0, ge=0, description="Facts page offset"),
    facts_limit: int = Query(20, ge=1, le=200, description="Facts page size"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EntityDetail]:
    """Return a single entity with recent facts and linked contact info."""
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    # Entity metadata from shared schema — safe with any pool.
    row = await pool.fetchrow(
        "SELECT e.id, e.canonical_name, e.entity_type,"
        " e.aliases, e.metadata,"
        " e.created_at, e.updated_at,"
        " COALESCE((e.metadata->>'unidentified')::boolean, false) AS unidentified,"
        " e.roles AS linked_contact_roles"
        " FROM public.entities e"
        " WHERE e.id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Facts live in per-butler schemas — fan out across all memory pools.
    row_limit = facts_offset + facts_limit

    async def _query_entity_facts(_: str, fpool: object) -> tuple[int, list[object]]:
        count = (
            await fpool.fetchval(
                "SELECT count(*) FROM facts"
                " WHERE (entity_id = $1 OR object_entity_id = $1)"
                " AND validity = 'active'",
                eid,
            )
            or 0
        )
        rows = await fpool.fetch(
            "SELECT f.id, f.subject, f.predicate, f.content, f.importance, f.confidence,"
            " f.decay_rate, f.permanence, f.source_butler, f.source_episode_id,"
            " ep.session_id, f.supersedes_id,"
            " f.entity_id, f.object_entity_id, f.validity, f.scope, f.reference_count,"
            " f.created_at, f.last_referenced_at,"
            " f.last_confirmed_at, f.tags, f.metadata"
            " FROM facts f"
            " LEFT JOIN episodes ep ON ep.id = f.source_episode_id"
            " WHERE (f.entity_id = $1 OR f.object_entity_id = $1)"
            " AND f.validity = 'active'"
            " ORDER BY f.created_at DESC"
            " OFFSET $2 LIMIT $3",
            eid,
            0,
            row_limit,
        )
        return count, list(rows)

    per_pool = await _fan_out_memory_queries(
        db, query_name="entity_facts", query_fn=_query_entity_facts
    )
    fact_count = sum(c for c, _ in per_pool)
    merged_fact_rows: list[object] = []
    for _, frows in per_pool:
        merged_fact_rows.extend(frows)
    merged_fact_rows = _sort_rows_by_created_at(merged_fact_rows)
    merged_fact_rows = merged_fact_rows[facts_offset : facts_offset + facts_limit]

    try:
        info_rows = await pool.fetch(
            "SELECT id, type, value, label, is_primary, secured"
            " FROM public.entity_info"
            " WHERE entity_id = $1"
            " ORDER BY type",
            eid,
        )
    except Exception:
        info_rows = []

    recent_facts = [_row_to_fact(f) for f in merged_fact_rows]
    recent_facts = await _resolve_entity_names(db, recent_facts)

    entity_info = [
        EntityInfoEntry(
            id=str(r["id"]),
            type=r["type"],
            value=None if r["secured"] else r["value"],
            label=r["label"],
            is_primary=r["is_primary"],
            secured=r["secured"],
        )
        for r in info_rows
    ]

    detail = EntityDetail(
        id=str(row["id"]),
        canonical_name=row["canonical_name"],
        entity_type=row["entity_type"],
        aliases=list(row["aliases"]) if row["aliases"] else [],
        roles=list(row["linked_contact_roles"]) if row["linked_contact_roles"] else [],
        metadata=_parse_jsonb(row["metadata"]),
        unidentified=row["unidentified"],
        fact_count=fact_count,
        # public.contacts retired (bu-jnaa3): no contact row to link.
        linked_contact_id=None,
        linked_contact_name=None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        recent_facts=recent_facts,
        recent_facts_total=fact_count,
        recent_facts_offset=facts_offset,
        recent_facts_limit=facts_limit,
        recent_facts_has_more=(facts_offset + facts_limit) < fact_count,
        entity_info=entity_info,
    )

    return ApiResponse[EntityDetail](data=detail)


# ---------------------------------------------------------------------------
# PATCH /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


@router.patch("/entities/{entity_id}", response_model=ApiResponse[EntitySummary])
async def update_entity(
    entity_id: str,
    body: UpdateEntityRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EntitySummary]:
    """Update entity core fields (canonical_name, aliases, metadata merge)."""
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    # Build SET clause dynamically from provided fields
    sets: list[str] = []
    args: list[object] = [eid]
    idx = 2

    if body.canonical_name is not None:
        sets.append(f"canonical_name = ${idx}")
        args.append(body.canonical_name)
        idx += 1

    if body.entity_type is not None:
        _VALID_ENTITY_TYPES = {"person", "organization", "place", "other"}
        if body.entity_type not in _VALID_ENTITY_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid entity_type. Must be one of: {', '.join(sorted(_VALID_ENTITY_TYPES))}"
                ),
            )
        sets.append(f"entity_type = ${idx}")
        args.append(body.entity_type)
        idx += 1

    if body.aliases is not None:
        sets.append(f"aliases = ${idx}")
        args.append(body.aliases)
        idx += 1

    if body.roles is not None:
        sets.append(f"roles = ${idx}")
        args.append(body.roles)
        idx += 1

    if body.metadata is not None:
        # Filter out system-managed keys to prevent unauthorized state manipulation.
        # deleted_at/merged_into are managed by delete_entity/merge_entity endpoints.
        # unidentified is managed by the promote_entity endpoint.
        _SYSTEM_METADATA_KEYS = {"deleted_at", "merged_into", "unidentified"}
        allowed_metadata = {
            k: v for k, v in body.metadata.items() if k not in _SYSTEM_METADATA_KEYS
        }
        if allowed_metadata:
            # Merge patch into existing metadata (JSONB || operator).
            # Pass the dict directly — the asyncpg JSONB codec handles encoding.
            # json.dumps() here would double-encode and store a JSONB string scalar,
            # which the || operator then arrayifies, corrupting the column.
            sets.append(f"metadata = COALESCE(metadata, '{{}}'::jsonb) || ${idx}")
            args.append(allowed_metadata)
            idx += 1

    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")

    sets.append("updated_at = now()")

    row = await pool.fetchrow(
        f"UPDATE public.entities SET {', '.join(sets)}"
        f" WHERE id = $1"
        f" RETURNING id, canonical_name, entity_type, aliases, roles,"
        f" metadata, created_at, updated_at",
        *args,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    return ApiResponse[EntitySummary](
        data=EntitySummary(
            id=str(row["id"]),
            canonical_name=row["canonical_name"],
            entity_type=row["entity_type"],
            aliases=list(row["aliases"]) if row["aliases"] else [],
            roles=list(row["roles"]) if row["roles"] else [],
            fact_count=0,
            unidentified=bool(_parse_jsonb(row["metadata"]).get("unidentified", False)),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
    )


# ---------------------------------------------------------------------------
# PUT /api/memory/entities/{entity_id}/linked-contact
# ---------------------------------------------------------------------------


class _LinkContactRequest(BaseModel):
    contact_id: str


@router.put("/entities/{entity_id}/linked-contact")
async def set_linked_contact(
    entity_id: str,
    body: _LinkContactRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict:
    """Migrate any contact-scoped facts onto this entity.

    public.contacts is retired (bu-jnaa3): there is no contact row to link, so
    this route no longer writes ``contacts.entity_id``. It still migrates any
    existing contact-scoped facts (stored with ``subject = 'contact:{cid}'`` or
    legacy bare-UUID ``subject = '{cid}'``) to the entity by setting their
    ``entity_id`` column, so facts created before entity promotion are visible
    on the entity detail page.
    """
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)
    cid = _uuid.UUID(body.contact_id)

    # Verify entity exists
    entity = await pool.fetchval("SELECT id FROM public.entities WHERE id = $1", eid)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Migrate existing contact-scoped facts to the entity across all memory pools.
    # Matches both the current 'contact:{cid}' prefix and legacy bare-UUID subjects.
    cid_str = str(cid)
    prefixed_subject = f"contact:{cid_str}"

    async def _migrate_facts(_name: str, fpool: object) -> int:
        result = await fpool.execute(
            "UPDATE facts SET entity_id = $1"
            " WHERE (subject = $2 OR subject = $3)"
            " AND entity_id IS NULL"
            " AND validity = 'active'",
            eid,
            prefixed_subject,
            cid_str,
        )
        # asyncpg returns 'UPDATE N' — extract the count
        return int(result.split()[-1]) if result else 0

    counts = await _fan_out_memory_queries(
        db, query_name="migrate_contact_facts", query_fn=_migrate_facts
    )
    migrated = sum(c for c in counts if c)

    return {"entity_id": str(eid), "contact_id": str(cid), "facts_migrated": migrated}


# ---------------------------------------------------------------------------
# POST /api/memory/entities/{entity_id}/merge was removed (bu-f0i4w).
#
# It merged memory `facts` via `entity_merge` with no compare view, no
# merge_reviews audit row, no relationship.entity_facts repoint, and no
# roles-aware owner gate — an unaudited bypass of the relationship-merge-review
# spec. Its only frontend caller (EntitiesPage) was removed in PR #2206, leaving
# it unreachable from the dashboard. The audited entity-merge surface is
# POST /api/relationship/entities/{id}/merge.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DELETE /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


@router.delete("/entities/{entity_id}", status_code=204)
async def delete_entity(
    entity_id: str,
    retire_facts: bool = Query(False, description="Retire all active facts before deleting"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete an entity by setting metadata.deleted_at.

    Owner entities cannot be deleted (returns 403).  When active facts exist,
    returns 409 with the count unless ``retire_facts=true`` is passed, in which
    case all active facts are retired (validity → 'retracted') first.
    """
    import uuid as _uuid
    from datetime import datetime

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    row = await pool.fetchrow(
        "SELECT id, roles FROM public.entities WHERE id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    roles = list(row["roles"]) if row["roles"] else []
    if "owner" in roles:
        raise HTTPException(status_code=403, detail="Cannot delete owner entity")

    # Check active facts referencing this entity across all memory pools.
    async def _count_active_facts(_: str, fpool: object) -> int:
        return (
            await fpool.fetchval(
                "SELECT count(*) FROM facts WHERE entity_id = $1 AND validity = 'active'",
                eid,
            )
            or 0
        )

    per_pool_counts = await _fan_out_memory_queries(
        db,
        query_name="delete_entity_fact_check",
        query_fn=_count_active_facts,
    )
    total_active_facts = sum(per_pool_counts)
    if total_active_facts > 0:
        if not retire_facts:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Entity has {total_active_facts} active fact(s). "
                    "Reassign or retire all active facts before deleting this entity."
                ),
            )

        # Retire all active facts for this entity across every memory pool.
        async def _retire_facts(_: str, fpool: object) -> int:
            await fpool.execute(
                "UPDATE facts SET validity = 'retracted'"
                " WHERE entity_id = $1 AND validity = 'active'",
                eid,
            )
            return 0

        await _fan_out_memory_queries(
            db,
            query_name="delete_entity_retire_facts",
            query_fn=_retire_facts,
        )

    deleted_at = datetime.now(UTC).isoformat()
    await pool.execute(
        "UPDATE public.entities"
        " SET metadata = COALESCE(metadata, '{}'::jsonb) || $2,"
        " updated_at = now()"
        " WHERE id = $1",
        eid,
        {"deleted_at": deleted_at},
    )
    # public.contacts retired (bu-jnaa3): no contact rows to unlink.


# ---------------------------------------------------------------------------
# POST /api/memory/entities/{entity_id}/archive
# ---------------------------------------------------------------------------


@router.post("/entities/{entity_id}/archive", status_code=204)
async def archive_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Archive an entity by setting metadata.archived_at.

    Archived entities are hidden from the default list view but remain fully
    intact (contacts stay linked, facts are preserved).  Owner entities cannot
    be archived (returns 403).
    """
    import uuid as _uuid
    from datetime import datetime

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    row = await pool.fetchrow(
        "SELECT id, roles, metadata FROM public.entities WHERE id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    roles = list(row["roles"]) if row["roles"] else []
    if "owner" in roles:
        raise HTTPException(status_code=403, detail="Cannot archive owner entity")

    metadata = _parse_jsonb(row["metadata"])
    if isinstance(metadata, dict) and metadata.get("archived_at"):
        return  # Already archived — idempotent

    archived_at = datetime.now(UTC).isoformat()
    await pool.execute(
        "UPDATE public.entities"
        " SET metadata = COALESCE(metadata, '{}'::jsonb) || $2,"
        " updated_at = now()"
        " WHERE id = $1",
        eid,
        {"archived_at": archived_at},
    )


# ---------------------------------------------------------------------------
# POST /api/memory/entities/{entity_id}/unarchive
# ---------------------------------------------------------------------------


@router.post("/entities/{entity_id}/unarchive", status_code=204)
async def unarchive_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Restore an archived entity by removing metadata.archived_at."""
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    row = await pool.fetchrow(
        "SELECT id FROM public.entities WHERE id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    await pool.execute(
        "UPDATE public.entities"
        " SET metadata = metadata - 'archived_at',"
        " updated_at = now()"
        " WHERE id = $1",
        eid,
    )


# ---------------------------------------------------------------------------
# DELETE /api/memory/entities/{entity_id}/linked-contact
# ---------------------------------------------------------------------------


@router.delete("/entities/{entity_id}/linked-contact", status_code=204)
async def unlink_contact(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """No-op: public.contacts is retired (bu-jnaa3), so there is no contact link
    to clear. Retained for API compatibility; returns 204."""
    return None


# ---------------------------------------------------------------------------
# POST /api/memory/entities/{entity_id}/promote
# ---------------------------------------------------------------------------


@router.post("/entities/{entity_id}/promote", response_model=ApiResponse[EntitySummary])
async def promote_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EntitySummary]:
    """Promote a transitory (unidentified) entity by clearing the unidentified flag.

    Sets metadata.unidentified to null (removes the key) so the entity is no
    longer shown as needing review.  Returns 409 if the entity is not currently
    unidentified.
    """
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    # Atomically promote: only update if the entity is currently unidentified.
    # A single conditional UPDATE avoids the TOCTOU race between SELECT and UPDATE.
    updated_row = await pool.fetchrow(
        "UPDATE public.entities"
        " SET metadata = metadata - 'unidentified',"
        " updated_at = now()"
        " WHERE id = $1 AND (metadata->>'unidentified')::boolean IS TRUE"
        " RETURNING id, canonical_name, entity_type, aliases, roles,"
        " metadata, created_at, updated_at",
        eid,
    )

    if updated_row is None:
        # No rows updated — either the entity doesn't exist or it isn't unidentified.
        exists = await pool.fetchval("SELECT 1 FROM public.entities WHERE id = $1", eid)
        if not exists:
            raise HTTPException(status_code=404, detail="Entity not found")
        raise HTTPException(status_code=409, detail="Entity is not unidentified")

    return ApiResponse[EntitySummary](
        data=EntitySummary(
            id=str(updated_row["id"]),
            canonical_name=updated_row["canonical_name"],
            entity_type=updated_row["entity_type"],
            aliases=list(updated_row["aliases"]) if updated_row["aliases"] else [],
            roles=list(updated_row["roles"]) if updated_row["roles"] else [],
            fact_count=0,
            unidentified=False,
            created_at=str(updated_row["created_at"]),
            updated_at=str(updated_row["updated_at"]),
        )
    )


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_episode(r) -> Episode:
    """Convert an asyncpg Record to an Episode model.

    Expects the episodes column set used by the list/get/inspect endpoints
    (id, butler, session_id, content, importance, reference_count, consolidated,
    consolidation_status, created_at, last_referenced_at, expires_at, metadata).
    """
    return Episode(
        id=str(r["id"]),
        butler=r["butler"],
        session_id=str(r["session_id"]) if r["session_id"] else None,
        content=r["content"],
        importance=float(r["importance"]),
        reference_count=r["reference_count"],
        consolidated=r["consolidated"],
        consolidation_status=r["consolidation_status"],
        created_at=str(r["created_at"]),
        last_referenced_at=str(r["last_referenced_at"]) if r["last_referenced_at"] else None,
        expires_at=str(r["expires_at"]) if r["expires_at"] else None,
        metadata=_parse_jsonb(r["metadata"]),
    )


def _row_to_fact(r) -> Fact:
    """Convert an asyncpg Record to a Fact model."""
    return Fact(
        id=str(r["id"]),
        subject=r["subject"],
        predicate=r["predicate"],
        content=r["content"],
        importance=float(r["importance"]),
        confidence=float(r["confidence"]),
        decay_rate=float(r["decay_rate"]),
        permanence=r["permanence"],
        source_butler=r["source_butler"],
        source_episode_id=str(r["source_episode_id"]) if r["source_episode_id"] else None,
        session_id=str(r["session_id"]) if r.get("session_id") else None,
        supersedes_id=str(r["supersedes_id"]) if r["supersedes_id"] else None,
        entity_id=str(r["entity_id"]) if r.get("entity_id") else None,
        object_entity_id=str(r["object_entity_id"]) if r.get("object_entity_id") else None,
        validity=r["validity"],
        scope=r["scope"],
        reference_count=r["reference_count"],
        created_at=str(r["created_at"]),
        last_referenced_at=str(r["last_referenced_at"]) if r["last_referenced_at"] else None,
        last_confirmed_at=str(r["last_confirmed_at"]) if r["last_confirmed_at"] else None,
        tags=_parse_tags(r["tags"]),
        metadata=_parse_jsonb(r["metadata"]),
    )


def _row_to_rule(r) -> Rule:
    """Convert an asyncpg Record to a Rule model."""
    return Rule(
        id=str(r["id"]),
        content=r["content"],
        scope=r["scope"],
        maturity=r["maturity"],
        confidence=float(r["confidence"]),
        decay_rate=float(r["decay_rate"]),
        permanence=r["permanence"],
        effectiveness_score=float(r["effectiveness_score"]),
        applied_count=r["applied_count"],
        success_count=r["success_count"],
        harmful_count=r["harmful_count"],
        source_episode_id=str(r["source_episode_id"]) if r["source_episode_id"] else None,
        source_butler=r["source_butler"],
        created_at=str(r["created_at"]),
        last_applied_at=str(r["last_applied_at"]) if r["last_applied_at"] else None,
        last_evaluated_at=str(r["last_evaluated_at"]) if r["last_evaluated_at"] else None,
        tags=_parse_tags(r["tags"]),
        metadata=_parse_jsonb(r["metadata"]),
    )


# ---------------------------------------------------------------------------
# Butler-scoped memory stats: GET /api/butlers/{name}/memory/stats
# ---------------------------------------------------------------------------

butler_memory_router = APIRouter(prefix="/api/butlers", tags=["butlers", "memory"])


@butler_memory_router.get("/{name}/memory/stats", response_model=ApiResponse[ButlerMemoryStats])
async def get_butler_memory_stats(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ButlerMemoryStats]:
    """Return per-butler memory subsystem counts with 24-hour deltas.

    Queries the butler's own schema tables (episodes, facts, rules) and the
    shared public.entities table filtered by butler_name.  Returns all-zero
    counts when the butler exists but has no memory tables (e.g. memory module
    not enabled).

    Errors:
    - 404: Butler is not registered in the DatabaseManager.
    - 200 with zeros: Butler exists but memory tables are absent.
    - 500: Unexpected database error.
    """
    if name not in db.butler_names:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    # Run one batched query per table concurrently: each fetchrow returns (total, count_24h)
    # using COUNT(*) FILTER (...) to fetch both counts in a single round-trip.
    # Individual failures are caught per-table so a missing table returns zeros, not 500.

    _INTERVAL = "NOW() - INTERVAL '1 day'"

    async def _count_episodes() -> tuple[int, int]:
        try:
            row = await pool.fetchrow(
                f"SELECT"
                f"  count(*) AS total,"
                f"  count(*) FILTER (WHERE created_at > {_INTERVAL}) AS recent"
                f" FROM episodes"
            )
            return (row["total"] or 0, row["recent"] or 0) if row else (0, 0)
        except Exception:
            logger.debug("episodes table not available for butler '%s'; returning zeros", name)
            return (0, 0)

    async def _count_facts() -> tuple[int, int]:
        try:
            row = await pool.fetchrow(
                f"SELECT"
                f"  count(*) AS total,"
                f"  count(*) FILTER (WHERE created_at > {_INTERVAL}) AS recent"
                f" FROM facts"
            )
            return (row["total"] or 0, row["recent"] or 0) if row else (0, 0)
        except Exception:
            logger.debug("facts table not available for butler '%s'; returning zeros", name)
            return (0, 0)

    async def _count_entities() -> tuple[int, int]:
        try:
            row = await pool.fetchrow(
                f"SELECT"
                f"  count(*) AS total,"
                f"  count(*) FILTER (WHERE created_at > {_INTERVAL}) AS recent"
                f" FROM public.entities"
                f" WHERE metadata->>'source_butler' = $1",
                name,
            )
            return (row["total"] or 0, row["recent"] or 0) if row else (0, 0)
        except Exception:
            logger.debug("public.entities not available for butler '%s'; returning zeros", name)
            return (0, 0)

    async def _count_rules() -> tuple[int, int]:
        try:
            row = await pool.fetchrow(
                f"SELECT"
                f"  count(*) AS total,"
                f"  count(*) FILTER (WHERE created_at > {_INTERVAL}) AS recent"
                f" FROM rules"
            )
            return (row["total"] or 0, row["recent"] or 0) if row else (0, 0)
        except Exception:
            logger.debug("rules table not available for butler '%s'; returning zeros", name)
            return (0, 0)

    (
        (total_episodes, episodes_24h),
        (total_facts, facts_24h),
        (total_entities, entities_24h),
        (total_rules, rules_24h),
    ) = await asyncio.gather(
        _count_episodes(),
        _count_facts(),
        _count_entities(),
        _count_rules(),
    )

    stats = ButlerMemoryStats(
        total_episodes=total_episodes,
        episodes_24h=episodes_24h,
        total_facts=total_facts,
        facts_24h=facts_24h,
        total_entities=total_entities,
        entities_24h=entities_24h,
        total_rules=total_rules,
        rules_24h=rules_24h,
    )

    return ApiResponse[ButlerMemoryStats](data=stats)


# ---------------------------------------------------------------------------
# GET /api/memory/retention-policies
# ---------------------------------------------------------------------------


@router.get("/retention-policies", response_model=ApiResponse[list[MemoryRetentionPolicy]])
async def get_retention_policies(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryRetentionPolicy]]:
    """Return all rows from public.memory_retention_policies."""
    pool = _any_pool(db)
    try:
        rows = await pool.fetch(
            "SELECT kind, ttl_days, max_rows, updated_at, updated_by"
            " FROM public.memory_retention_policies"
            " ORDER BY kind"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "memory_retention_policies table not available"
                " — migration core_096 may not have run"
            ),
        ) from exc

    policies = [
        MemoryRetentionPolicy(
            kind=r["kind"],
            ttl_days=r["ttl_days"],
            max_rows=r["max_rows"],
            updated_at=str(r["updated_at"]),
            updated_by=r["updated_by"],
        )
        for r in rows
    ]
    return ApiResponse[list[MemoryRetentionPolicy]](data=policies)


# ---------------------------------------------------------------------------
# PUT /api/memory/retention-policies
# ---------------------------------------------------------------------------


@router.put("/retention-policies", response_model=ApiResponse[list[MemoryRetentionPolicy]])
async def update_retention_policies(
    body: UpdateRetentionPoliciesRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryRetentionPolicy]]:
    """Bulk-update retention policies; one audit entry per changed row."""
    pool = _any_pool(db)

    if not body.policies:
        raise HTTPException(status_code=400, detail="policies list must not be empty")

    # Validate kinds
    _VALID_KINDS = {"event", "fact", "preference", "summary", "transcript", "embedding"}
    for entry in body.policies:
        if entry.kind not in _VALID_KINDS:
            valid = ", ".join(sorted(_VALID_KINDS))
            raise HTTPException(
                status_code=400,
                detail=f"Invalid kind '{entry.kind}'. Must be one of: {valid}",
            )

    updated: list[MemoryRetentionPolicy] = []
    for entry in body.policies:
        row = await pool.fetchrow(
            "INSERT INTO public.memory_retention_policies"
            " (kind, ttl_days, max_rows, updated_by)"
            " VALUES ($1, $2, $3, 'owner')"
            " ON CONFLICT (kind) DO UPDATE"
            "  SET ttl_days = EXCLUDED.ttl_days,"
            "      max_rows = EXCLUDED.max_rows,"
            "      updated_at = now(),"
            "      updated_by = 'owner'"
            " RETURNING kind, ttl_days, max_rows, updated_at, updated_by",
            entry.kind,
            entry.ttl_days,
            entry.max_rows,
        )
        updated.append(
            MemoryRetentionPolicy(
                kind=row["kind"],
                ttl_days=row["ttl_days"],
                max_rows=row["max_rows"],
                updated_at=str(row["updated_at"]),
                updated_by=row["updated_by"],
            )
        )
        try:
            await _audit.append(
                pool,
                "owner",
                "memory.retention_policy",
                target=f"kind:{entry.kind}",
                note=f"ttl_days={entry.ttl_days} max_rows={entry.max_rows}",
            )
        except _audit.AuditTableNotAvailableError:
            raise HTTPException(
                status_code=503,
                detail="Audit log is not available — migration core_092 may not have run",
            )

    return ApiResponse[list[MemoryRetentionPolicy]](data=updated)


# ---------------------------------------------------------------------------
# GET /api/memory/compaction-log
# ---------------------------------------------------------------------------


@router.get("/compaction-log", response_model=ApiResponse[list[CompactionLogEntry]])
async def get_compaction_log(
    limit: int = Query(50, ge=1, le=500, description="Max entries to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[CompactionLogEntry]]:
    """Return recent compaction events from public.memory_compaction_log."""
    pool = _any_pool(db)
    try:
        rows = await pool.fetch(
            "SELECT id, ts, kind, rows_removed, bytes_freed"
            " FROM public.memory_compaction_log"
            " ORDER BY ts DESC"
            " LIMIT $1",
            limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "memory_compaction_log table not available — migration core_096 may not have run"
            ),
        ) from exc

    entries = [
        CompactionLogEntry(
            id=r["id"],
            ts=str(r["ts"]),
            kind=r["kind"],
            rows_removed=r["rows_removed"],
            bytes_freed=r["bytes_freed"],
        )
        for r in rows
    ]
    return ApiResponse[list[CompactionLogEntry]](data=entries)


# ---------------------------------------------------------------------------
# GET /api/memory/inspect
# ---------------------------------------------------------------------------

_INSPECT_VALID_KINDS = {"episode", "fact", "rule"}


@router.get("/inspect", response_model=PaginatedResponse[MemoryInspectResult])
async def inspect_memory(
    q: str | None = Query(None, description="Full-text search query"),
    kind: str | None = Query(None, description="Filter by kind: episode|fact|rule"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[MemoryInspectResult]:
    """Search across memory tiers (episodes, facts, rules) with optional kind filter."""
    if kind is not None and kind not in _INSPECT_VALID_KINDS:
        valid = ", ".join(sorted(_INSPECT_VALID_KINDS))
        raise HTTPException(
            status_code=400,
            detail=f"Invalid kind '{kind}'. Must be one of: {valid}",
        )

    target_kinds = [kind] if kind else list(_INSPECT_VALID_KINDS)
    row_limit = offset + limit

    # Each result carries the full register row for its kind (`fact`/`rule`/
    # `episode`) in addition to the flat id/kind/content/butler/created_at/
    # metadata fields, so search results render the same belief/maturity/
    # importance data as browse mode.  We SELECT the same column sets the list
    # endpoints use and reuse their serialization helpers (_row_to_fact /
    # _row_to_rule / _row_to_episode) to keep the row shapes identical.
    async def _query_pool(_: str, pool: object) -> list[dict]:
        results: list[dict] = []

        if "episode" in target_kinds:
            ep_cond = ""
            ep_args: list[object] = []
            idx = 1
            if q:
                ep_cond = f" WHERE search_vector @@ plainto_tsquery('english', ${idx})"
                ep_args.append(q)
                idx += 1
            ep_rows = await pool.fetch(
                f"SELECT id, butler, session_id, content, importance, reference_count,"
                f" consolidated, consolidation_status, created_at, last_referenced_at,"
                f" expires_at, metadata"
                f" FROM episodes{ep_cond}"
                f" ORDER BY created_at DESC"
                f" LIMIT ${idx}",
                *ep_args,
                row_limit,
            )
            for r in ep_rows:
                episode = _row_to_episode(r)
                results.append(
                    {
                        "id": episode.id,
                        "kind": "episode",
                        "content": episode.content or "",
                        "butler": episode.butler,
                        "created_at": episode.created_at,
                        "metadata": episode.metadata,
                        "episode": episode,
                    }
                )

        if "fact" in target_kinds:
            fact_cond = ""
            fact_args: list[object] = []
            idx = 1
            if q:
                fact_cond = f" WHERE search_vector @@ plainto_tsquery('english', ${idx})"
                fact_args.append(q)
                idx += 1
            fact_rows = await pool.fetch(
                f"SELECT id, subject, predicate, content, importance, confidence,"
                f" decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
                f" entity_id, object_entity_id, validity, scope, reference_count,"
                f" created_at, last_referenced_at,"
                f" last_confirmed_at, tags, metadata"
                f" FROM facts{fact_cond}"
                f" ORDER BY created_at DESC"
                f" LIMIT ${idx}",
                *fact_args,
                row_limit,
            )
            for r in fact_rows:
                fact = _row_to_fact(r)
                results.append(
                    {
                        "id": fact.id,
                        "kind": "fact",
                        "content": fact.content or "",
                        "butler": fact.source_butler,
                        "created_at": fact.created_at,
                        "metadata": fact.metadata,
                        "fact": fact,
                    }
                )

        if "rule" in target_kinds:
            rule_cond = ""
            rule_args: list[object] = []
            idx = 1
            if q:
                rule_cond = f" WHERE search_vector @@ plainto_tsquery('english', ${idx})"
                rule_args.append(q)
                idx += 1
            rule_rows = await pool.fetch(
                f"SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
                f" effectiveness_score, applied_count, success_count, harmful_count,"
                f" source_episode_id, source_butler, created_at, last_applied_at,"
                f" last_evaluated_at, tags, metadata"
                f" FROM rules{rule_cond}"
                f" ORDER BY created_at DESC"
                f" LIMIT ${idx}",
                *rule_args,
                row_limit,
            )
            for r in rule_rows:
                rule = _row_to_rule(r)
                results.append(
                    {
                        "id": rule.id,
                        "kind": "rule",
                        "content": rule.content or "",
                        "butler": rule.source_butler,
                        "created_at": rule.created_at,
                        "metadata": rule.metadata,
                        "rule": rule,
                    }
                )
        return results

    per_pool = await _fan_out_memory_queries(db, query_name="inspect", query_fn=_query_pool)

    merged: list[dict] = []
    for pool_results in per_pool:
        merged.extend(pool_results)

    # Sort by created_at DESC across all pools
    merged.sort(key=lambda r: r["created_at"], reverse=True)
    total = len(merged)
    page = merged[offset : offset + limit]

    # Resolve entity_id → canonical_name for the embedded fact payloads, mirroring
    # GET /facts so the ledger row can label related entities the same way.
    page_facts = [r["fact"] for r in page if r["kind"] == "fact" and r["fact"] is not None]
    if page_facts:
        await _resolve_entity_names(db, page_facts)

    data = [
        MemoryInspectResult(
            id=r["id"],
            kind=r["kind"],
            content=r["content"][:200] + ("..." if len(r["content"]) > 200 else ""),
            butler=r["butler"],
            created_at=r["created_at"],
            metadata=r["metadata"],
            fact=r.get("fact"),
            rule=r.get("rule"),
            episode=r.get("episode"),
        )
        for r in page
    ]
    return PaginatedResponse[MemoryInspectResult](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/reembed/pending
# ---------------------------------------------------------------------------


@router.get("/reembed/pending", response_model=ApiResponse[ReembedPendingCounts])
async def get_reembed_pending(
    butler: str | None = Query(
        None,
        description=(
            "Butler schema to probe. Defaults to all memory-capable schemas when omitted."
        ),
    ),
    current_model: str = Query(
        _DEFAULT_EMBEDDING_MODEL,
        description=(
            "Embedding model currently configured for this butler. "
            "Rows whose stored embedding_model_version differs from this are counted as stale."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ReembedPendingCounts]:
    """Count stale embeddings per tier without performing any DB writes.

    Returns the number of rows in each memory tier (episodes, facts, rules)
    whose ``embedding_model_version`` differs from ``current_model``.  Only
    rows with a non-NULL embedding are considered stale (rows with no embedding
    have never been embedded and are not counted).

    Use this before triggering a re-embed run to estimate scope.
    """
    from butlers.modules.memory import reembedding as _reembedding

    if butler is not None:
        try:
            pool = db.pool(butler)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No pool available for butler '{butler}'")
        try:
            counts = await _reembedding.count_pending(pool, current_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        results = await _fan_out_memory_queries(
            db,
            query_name="reembed_pending",
            query_fn=lambda _name, pool: _reembedding.count_pending(pool, current_model),
        )
        counts = dict.fromkeys(_reembedding.ALL_TIERS, 0)
        for result in results:
            for tier, count in result.items():
                counts[tier] = counts.get(tier, 0) + count

    return ApiResponse[ReembedPendingCounts](
        data=ReembedPendingCounts(
            counts=counts,
            total=sum(counts.values()),
            current_model=current_model,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/memory/reembed
# ---------------------------------------------------------------------------


@router.post("/reembed", response_model=ApiResponse[ReembedRunResult])
async def run_reembed(
    body: ReembedRunRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ReembedRunResult]:
    """Trigger a synchronous re-embedding run for stale memory rows.

    Re-embeds rows whose ``embedding_model_version`` differs from
    ``body.current_model`` using the embedding engine for that model.

    **WARNING — this is a synchronous, long-running endpoint.**  Re-embedding
    thousands of rows can take several minutes.  Use ``dry_run=True`` (the
    default) with GET /api/memory/reembed/pending first to estimate scope, then
    call with ``dry_run=False`` to commit changes.

    The embedding engine is loaded lazily on first call and cached per model
    name (shared with the butler's MCP layer).  A non-standard
    ``current_model`` that is not installed in the container will raise a 500
    error during engine initialisation.
    """
    from butlers.modules.memory import reembedding as _reembedding
    from butlers.modules.memory.tools import get_embedding_engine

    try:
        pool = db.pool(body.butler)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No pool available for butler '{body.butler}'")

    try:
        engine = get_embedding_engine(body.current_model)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load embedding engine for model '{body.current_model}': {exc}",
        ) from exc

    try:
        result = await _reembedding.run(
            pool,
            engine,
            dry_run=body.dry_run,
            tiers=body.tiers,
            batch_size=body.batch_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ApiResponse[ReembedRunResult](
        data=ReembedRunResult(
            dry_run=result.dry_run,
            current_model=result.current_model,
            tiers_processed=result.tiers_processed,
            counts=result.counts,
            total=result.total,
            errors=result.errors,
        )
    )
