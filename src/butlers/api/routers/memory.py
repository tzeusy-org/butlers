"""Memory system endpoints — episodes, facts, rules, stats, activity.

Provides read-only endpoints for browsing memory data across all butler
databases that expose memory tables. The router gracefully skips pools where
memory tables are unavailable, so dedicated-memory deployments are optional.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.memory import Episode, Fact, MemoryActivity, MemoryStats, Rule

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


async def _fan_out_memory_queries(
    db: DatabaseManager,
    *,
    query_name: str,
    query_fn: Callable[[str, object], Awaitable[object | None]],
) -> list[object]:
    """Run a query across candidate pools and skip pools without memory schema."""
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


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=ApiResponse[MemoryStats])
async def get_memory_stats(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[MemoryStats]:
    """Return aggregated counts across all memory tiers."""

    async def _stats_for_pool(_: str, pool: object) -> dict[str, int]:
        return {
            "total_episodes": await pool.fetchval("SELECT count(*) FROM episodes") or 0,
            "unconsolidated_episodes": await pool.fetchval(
                "SELECT count(*) FROM episodes WHERE consolidated = false"
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
        }

    per_pool = await _fan_out_memory_queries(
        db,
        query_name="stats",
        query_fn=_stats_for_pool,
    )

    totals = MemoryStats()
    for row in per_pool:
        totals.total_episodes += row["total_episodes"]
        totals.unconsolidated_episodes += row["unconsolidated_episodes"]
        totals.total_facts += row["total_facts"]
        totals.active_facts += row["active_facts"]
        totals.fading_facts += row["fading_facts"]
        totals.total_rules += row["total_rules"]
        totals.candidate_rules += row["candidate_rules"]
        totals.established_rules += row["established_rules"]
        totals.proven_rules += row["proven_rules"]
        totals.anti_pattern_rules += row["anti_pattern_rules"]

    return ApiResponse[MemoryStats](data=totals)


# ---------------------------------------------------------------------------
# GET /api/memory/episodes
# ---------------------------------------------------------------------------


@router.get("/episodes", response_model=PaginatedResponse[Episode])
async def list_episodes(
    butler: str | None = Query(None, description="Filter by butler name"),
    consolidated: bool | None = Query(None, description="Filter by consolidated status"),
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
            f" consolidated, created_at, last_referenced_at, expires_at, metadata"
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
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [
        Episode(
            id=str(r["id"]),
            butler=r["butler"],
            session_id=str(r["session_id"]) if r["session_id"] else None,
            content=r["content"],
            importance=float(r["importance"]),
            reference_count=r["reference_count"],
            consolidated=r["consolidated"],
            created_at=str(r["created_at"]),
            last_referenced_at=str(r["last_referenced_at"]) if r["last_referenced_at"] else None,
            expires_at=str(r["expires_at"]) if r["expires_at"] else None,
            metadata=_parse_jsonb(r["metadata"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Episode](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


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

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    row_limit = offset + limit

    async def _query_pool(_: str, pool: object) -> tuple[int, list[object]]:
        total = await pool.fetchval(f"SELECT count(*) FROM facts{where}", *args) or 0
        rows = await pool.fetch(
            f"SELECT id, subject, predicate, content, importance, confidence,"
            f" decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            f" validity, scope, reference_count, created_at, last_referenced_at,"
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
        return await pool.fetchrow(
            "SELECT id, subject, predicate, content, importance, confidence,"
            " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            " validity, scope, reference_count, created_at, last_referenced_at,"
            " last_confirmed_at, tags, metadata"
            " FROM facts WHERE id = $1",
            fact_id,
        )

    rows = await _fan_out_memory_queries(
        db,
        query_name="fact_by_id",
        query_fn=_query_pool,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Fact not found")

    return ApiResponse[Fact](data=_row_to_fact(rows[0]))


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
# Row converters
# ---------------------------------------------------------------------------


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
        supersedes_id=str(r["supersedes_id"]) if r["supersedes_id"] else None,
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
