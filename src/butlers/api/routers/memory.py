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
from datetime import UTC

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.memory import (
    EntityDetail,
    EntityInfoEntry,
    EntitySummary,
    Episode,
    Fact,
    MemoryActivity,
    MemoryStats,
    Rule,
    UpdateEntityRequest,
)

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

    Since shared.entities is accessible from every butler's pool, we just
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
            " consolidated, created_at, last_referenced_at, expires_at, metadata"
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

    r = rows[0]
    return ApiResponse[Episode](
        data=Episode(
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
# GET /api/memory/entities
# ---------------------------------------------------------------------------

# Role priority for entity list ordering. Lower rank = higher in the list.
# Add new roles here to extend the ranking; unlisted roles fall through to ELSE.
_ENTITY_ROLE_RANK: dict[str, int] = {
    "owner": 0,
    "family": 1,
}

_ENTITY_ROLE_ORDER_SQL = (
    "CASE "
    + " ".join(
        f"WHEN '{role}' = ANY(e.roles) THEN {rank}"
        for role, rank in sorted(_ENTITY_ROLE_RANK.items(), key=lambda x: x[1])
    )
    + " ELSE 99 END"
)


@router.get("/entities", response_model=PaginatedResponse[EntitySummary])
async def list_entities(
    q: str | None = Query(None, description="Search canonical_name and aliases"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[EntitySummary]:
    """List entities from shared.entities with optional search and type filter."""
    pool = _any_pool(db)

    conditions: list[str] = [
        "(e.metadata->>'merged_into') IS NULL",
        "(e.metadata->>'deleted_at') IS NULL",
    ]
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
        conditions.append(f"e.entity_type = ${idx}")
        args.append(entity_type)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = (
        await pool.fetchval(
            f"SELECT count(*) FROM shared.entities e{where}",
            *args,
        )
        or 0
    )

    rows = await pool.fetch(
        f"SELECT e.id, e.canonical_name, e.entity_type, e.aliases,"
        f" e.created_at, e.updated_at,"
        f" (SELECT c.id FROM shared.contacts c"
        f"  WHERE c.entity_id = e.id LIMIT 1"
        f" ) AS linked_contact_id,"
        f" e.roles AS linked_contact_roles,"
        f" COALESCE((e.metadata->>'unidentified')::boolean, false) AS unidentified,"
        f" e.metadata->>'source_butler' AS source_butler,"
        f" e.metadata->>'source_scope' AS source_scope"
        f" FROM shared.entities e{where}"
        f" ORDER BY {_ENTITY_ROLE_ORDER_SQL} ASC, e.canonical_name ASC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
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

    data = [
        EntitySummary(
            id=str(r["id"]),
            canonical_name=r["canonical_name"],
            entity_type=r["entity_type"],
            aliases=list(r["aliases"]) if r["aliases"] else [],
            roles=list(r["linked_contact_roles"]) if r["linked_contact_roles"] else [],
            fact_count=fact_counts.get(str(r["id"]), 0),
            linked_contact_id=str(r["linked_contact_id"]) if r["linked_contact_id"] else None,
            unidentified=r["unidentified"],
            source_butler=r["source_butler"],
            source_scope=r["source_scope"],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

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
        " (SELECT c.id FROM shared.contacts c"
        "  WHERE c.entity_id = e.id LIMIT 1"
        " ) AS linked_contact_id,"
        " (SELECT c.name FROM shared.contacts c"
        "  WHERE c.entity_id = e.id LIMIT 1"
        " ) AS linked_contact_name,"
        " e.roles AS linked_contact_roles"
        " FROM shared.entities e"
        " WHERE e.id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Facts live in per-butler schemas — fan out across all memory pools.
    async def _query_entity_facts(_: str, fpool: object) -> tuple[int, list[object]]:
        count = (
            await fpool.fetchval(
                "SELECT count(*) FROM facts WHERE entity_id = $1 AND validity = 'active'",
                eid,
            )
            or 0
        )
        rows = await fpool.fetch(
            "SELECT id, subject, predicate, content, importance, confidence,"
            " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            " validity, scope, reference_count, created_at, last_referenced_at,"
            " last_confirmed_at, tags, metadata"
            " FROM facts WHERE entity_id = $1 AND validity = 'active'"
            " ORDER BY created_at DESC LIMIT 20",
            eid,
        )
        return count, list(rows)

    per_pool = await _fan_out_memory_queries(
        db, query_name="entity_facts", query_fn=_query_entity_facts
    )
    fact_count = sum(c for c, _ in per_pool)
    merged_fact_rows: list[object] = []
    for _, frows in per_pool:
        merged_fact_rows.extend(frows)
    merged_fact_rows = _sort_rows_by_created_at(merged_fact_rows)[:20]

    try:
        info_rows = await pool.fetch(
            "SELECT id, type, value, label, is_primary, secured"
            " FROM shared.entity_info"
            " WHERE entity_id = $1"
            " ORDER BY type",
            eid,
        )
    except Exception:
        info_rows = []

    recent_facts = [_row_to_fact(f) for f in merged_fact_rows]

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
        linked_contact_id=str(row["linked_contact_id"]) if row["linked_contact_id"] else None,
        linked_contact_name=row["linked_contact_name"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        recent_facts=recent_facts,
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

    if body.aliases is not None:
        sets.append(f"aliases = ${idx}")
        args.append(body.aliases)
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
            # Merge patch into existing metadata (JSONB || operator)
            sets.append(f"metadata = COALESCE(metadata, '{{}}'::jsonb) || ${idx}::jsonb")
            args.append(json.dumps(allowed_metadata))
            idx += 1

    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")

    sets.append("updated_at = now()")

    row = await pool.fetchrow(
        f"UPDATE shared.entities SET {', '.join(sets)}"
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
    """Link a contact to this entity by setting entity_id on the contact."""
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)
    cid = _uuid.UUID(body.contact_id)

    # Verify entity exists
    entity = await pool.fetchval("SELECT id FROM shared.entities WHERE id = $1", eid)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Verify contact exists
    contact = await pool.fetchval("SELECT id FROM shared.contacts WHERE id = $1", cid)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    await pool.execute(
        "UPDATE shared.contacts SET entity_id = $1, updated_at = now() WHERE id = $2",
        eid,
        cid,
    )

    return {"entity_id": str(eid), "contact_id": str(cid)}


# ---------------------------------------------------------------------------
# POST /api/memory/entities/{entity_id}/merge
# ---------------------------------------------------------------------------


class _MergeEntityRequest(BaseModel):
    source_entity_id: str


class _MergeEntityResponse(BaseModel):
    target_entity_id: str
    source_entity_id: str
    facts_repointed: int


@router.post("/entities/{entity_id}/merge", response_model=_MergeEntityResponse)
async def merge_entity(
    entity_id: str,
    body: _MergeEntityRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> _MergeEntityResponse:
    """Merge source entity into target entity (this entity).

    Re-points all facts from source to target, unions aliases/metadata/roles,
    tombstones the source entity, and re-links any contacts.
    """
    import uuid as _uuid

    from butlers.modules.memory.tools.entities import entity_merge

    pool = _any_pool(db)
    target_id = str(_uuid.UUID(entity_id))
    source_id = str(_uuid.UUID(body.source_entity_id))

    if target_id == source_id:
        raise HTTPException(status_code=400, detail="Cannot merge entity into itself")

    # Prevent merging owner entities — merge tombstones the source, which would
    # bypass the deletion restriction on owner entities (mirrors delete_entity guard).
    for eid, label in [(source_id, "source"), (target_id, "target")]:
        row = await pool.fetchrow(
            "SELECT roles FROM shared.entities WHERE id = $1",
            _uuid.UUID(eid),
        )
        if row and "owner" in (list(row["roles"]) if row["roles"] else []):
            raise HTTPException(
                status_code=403,
                detail=f"Cannot merge owner entity ({label})",
            )

    try:
        result = await entity_merge(pool, source_id, target_id, tenant_id="shared")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Target entity not found")

    # Re-link contacts from source entity to target entity
    await pool.execute(
        "UPDATE shared.contacts SET entity_id = $1, updated_at = now() WHERE entity_id = $2",
        _uuid.UUID(target_id),
        _uuid.UUID(source_id),
    )

    return _MergeEntityResponse(
        target_entity_id=target_id,
        source_entity_id=source_id,
        facts_repointed=result["facts_repointed"],
    )


# ---------------------------------------------------------------------------
# DELETE /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


@router.delete("/entities/{entity_id}", status_code=204)
async def delete_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete an entity by setting metadata.deleted_at.

    Unlinks any contacts pointing to this entity.  Owner entities cannot be
    deleted (returns 403).  Entities with active facts cannot be deleted
    (returns 409) — reassign or retire the facts first.
    """
    import uuid as _uuid
    from datetime import datetime

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    row = await pool.fetchrow(
        "SELECT id, roles FROM shared.entities WHERE id = $1",
        eid,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    roles = list(row["roles"]) if row["roles"] else []
    if "owner" in roles:
        raise HTTPException(status_code=403, detail="Cannot delete owner entity")

    # Block soft-delete when active facts reference this entity.
    # Fan out across all memory pools to get a global count.
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
        raise HTTPException(
            status_code=409,
            detail=(
                f"Entity has {total_active_facts} active fact(s). "
                "Reassign or retire all active facts before deleting this entity."
            ),
        )

    deleted_at = datetime.now(UTC).isoformat()
    await pool.execute(
        "UPDATE shared.entities"
        " SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,"
        " updated_at = now()"
        " WHERE id = $1",
        eid,
        json.dumps({"deleted_at": deleted_at}),
    )

    # Unlink any contacts referencing this entity
    await pool.execute(
        "UPDATE shared.contacts SET entity_id = NULL, updated_at = now() WHERE entity_id = $1",
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
    """Unlink the contact from this entity by clearing entity_id on the contact."""
    import uuid as _uuid

    pool = _any_pool(db)
    eid = _uuid.UUID(entity_id)

    await pool.execute(
        "UPDATE shared.contacts SET entity_id = NULL, updated_at = now() WHERE entity_id = $1",
        eid,
    )


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
        "UPDATE shared.entities"
        " SET metadata = metadata - 'unidentified',"
        " updated_at = now()"
        " WHERE id = $1 AND (metadata->>'unidentified')::boolean IS TRUE"
        " RETURNING id, canonical_name, entity_type, aliases, roles,"
        " metadata, created_at, updated_at",
        eid,
    )

    if updated_row is None:
        # No rows updated — either the entity doesn't exist or it isn't unidentified.
        exists = await pool.fetchval("SELECT 1 FROM shared.entities WHERE id = $1", eid)
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
