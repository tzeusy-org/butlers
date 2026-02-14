"""Memory system endpoints — episodes, facts, rules, stats, activity.

Provides read-only endpoints for browsing memory data through the dashboard API.
Data is read from the configured ``memory`` pool as the current projection source.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.memory import Episode, Fact, MemoryActivity, MemoryStats, Rule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

BUTLER_DB = "memory"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the dashboard memory projection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Memory butler database is not available",
        )


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


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=ApiResponse[MemoryStats])
async def get_memory_stats(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[MemoryStats]:
    """Return aggregated counts across all memory tiers."""
    pool = _pool(db)

    # Episode counts
    total_episodes = await pool.fetchval("SELECT count(*) FROM episodes") or 0
    unconsolidated = (
        await pool.fetchval("SELECT count(*) FROM episodes WHERE consolidated = false") or 0
    )

    # Fact counts
    total_facts = await pool.fetchval("SELECT count(*) FROM facts") or 0
    active_facts = await pool.fetchval("SELECT count(*) FROM facts WHERE validity = 'active'") or 0
    fading_facts = await pool.fetchval("SELECT count(*) FROM facts WHERE validity = 'fading'") or 0

    # Rule counts
    total_rules = await pool.fetchval("SELECT count(*) FROM rules") or 0
    candidate_rules = (
        await pool.fetchval("SELECT count(*) FROM rules WHERE maturity = 'candidate'") or 0
    )
    established_rules = (
        await pool.fetchval("SELECT count(*) FROM rules WHERE maturity = 'established'") or 0
    )
    proven_rules = await pool.fetchval("SELECT count(*) FROM rules WHERE maturity = 'proven'") or 0
    anti_pattern_rules = (
        await pool.fetchval("SELECT count(*) FROM rules WHERE maturity = 'anti_pattern'") or 0
    )

    stats = MemoryStats(
        total_episodes=total_episodes,
        unconsolidated_episodes=unconsolidated,
        total_facts=total_facts,
        active_facts=active_facts,
        fading_facts=fading_facts,
        total_rules=total_rules,
        candidate_rules=candidate_rules,
        established_rules=established_rules,
        proven_rules=proven_rules,
        anti_pattern_rules=anti_pattern_rules,
    )

    return ApiResponse[MemoryStats](data=stats)


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
    pool = _pool(db)

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

    total = await pool.fetchval(f"SELECT count(*) FROM episodes{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, butler, session_id, content, importance, reference_count,"
        f" consolidated, created_at, last_referenced_at, expires_at, metadata"
        f" FROM episodes{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

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
    pool = _pool(db)

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
        offset,
        limit,
    )

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
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id, subject, predicate, content, importance, confidence,"
        " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
        " validity, scope, reference_count, created_at, last_referenced_at,"
        " last_confirmed_at, tags, metadata"
        " FROM facts WHERE id = $1",
        fact_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Fact not found")

    return ApiResponse[Fact](data=_row_to_fact(row))


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
    pool = _pool(db)

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
        offset,
        limit,
    )

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
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
        " effectiveness_score, applied_count, success_count, harmful_count,"
        " source_episode_id, source_butler, created_at, last_applied_at,"
        " last_evaluated_at, tags, metadata"
        " FROM rules WHERE id = $1",
        rule_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    return ApiResponse[Rule](data=_row_to_rule(row))


# ---------------------------------------------------------------------------
# GET /api/memory/activity
# ---------------------------------------------------------------------------


@router.get("/activity", response_model=ApiResponse[list[MemoryActivity]])
async def list_activity(
    limit: int = Query(50, ge=1, le=200, description="Max activity items to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryActivity]]:
    """Return recent memory activity interleaved from all three tables."""
    pool = _pool(db)

    # Fetch recent items from each table
    episode_rows = await pool.fetch(
        "SELECT id, butler, content, created_at FROM episodes ORDER BY created_at DESC LIMIT $1",
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

    items: list[MemoryActivity] = []

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
