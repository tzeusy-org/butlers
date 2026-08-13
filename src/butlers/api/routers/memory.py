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
import re
import uuid as _uuid
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import Literal

from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.models import ApiMeta, ApiResponse, PaginatedResponse, PaginationMeta
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
    GraphHealthCoverage,
    GraphHealthPoolCoverage,
    MemoryActivity,
    MemoryCatalogSearchResult,
    MemoryInspectResult,
    MemoryLink,
    MemoryRetentionPolicy,
    MemoryStats,
    ReembedPendingCounts,
    ReembedRunRequest,
    ReembedRunResult,
    RetentionSourceObservation,
    Rule,
    UpdateEntityRequest,
    UpdateRetentionPoliciesRequest,
)
from butlers.api.routers import audit as _audit
from butlers.modules.memory.storage import get_links

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

_MEMORY_SCHEMA_RELATIONS = ("episodes", "facts", "rules", "memory_links", "episode_tombstones")
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _memory_schema_absent_at_start(db: DatabaseManager, butler_name: str) -> bool:
    """Return whether every required memory relation was absent at startup."""
    relation_marker = getattr(db, "relation_observed_since_start", None)
    if not callable(relation_marker):
        return False
    return all(
        relation_marker(butler_name, relation) is False for relation in _MEMORY_SCHEMA_RELATIONS
    )


def _memory_source_schema(db: DatabaseManager, butler_name: str) -> str | None:
    """Return the validated effective schema that owns memory relations.

    New dashboard managers retain a private ``modules.memory.memory_schema``
    override separately from the butler's domain pool schema.  The fallback
    keeps compatibility with older manager doubles and callers that only
    expose ``schema_for_butler``.
    """
    schema_for_memory = getattr(db, "memory_schema_for_butler", None)
    if not callable(schema_for_memory):
        schema_for_memory = getattr(db, "schema_for_butler", None)
    if not callable(schema_for_memory):
        return None
    try:
        schema = schema_for_memory(butler_name)
    except KeyError:
        return None
    if not isinstance(schema, str):
        return None
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(schema):
        raise ValueError(f"Unsafe schema identifier: {schema!r}")
    return schema


def _memory_relation(db: DatabaseManager, butler_name: str, relation: str) -> str:
    """Return the explicitly owned memory relation when a schema is configured.

    Per-butler API pools intentionally include ``public`` in their search path
    for shared data. Memory queries own their local tables, though, so a dropped
    private relation must not resolve to a same-named public table. Legacy pools
    without a configured schema keep their existing unqualified semantics.
    """
    schema = _memory_source_schema(db, butler_name)
    if schema is None:
        return relation
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(relation):
        raise ValueError(f"Unsafe relation identifier: {relation!r}")
    return f'"{schema}"."{relation}"'


def _source_episode_status_expression(
    *, record_alias: str, episode_alias: str, tombstone_alias: str
) -> str:
    """Return the bounded provenance state for one fact or rule record."""
    return (
        f"CASE WHEN {record_alias}.source_episode_id IS NULL THEN NULL "
        f"WHEN {episode_alias}.id IS NOT NULL THEN 'available' "
        f"WHEN {tombstone_alias}.episode_id IS NOT NULL THEN 'expired' "
        "ELSE 'unresolved' END AS source_episode_status"
    )


def _with_source_episode_status(
    record_query: str,
    *,
    episodes_relation: str,
    tombstones_relation: str,
) -> str:
    """Add an explicit source state without changing the caller's filters."""
    return (
        "SELECT record.*, "
        + _source_episode_status_expression(
            record_alias="record", episode_alias="source_episode", tombstone_alias="tombstone"
        )
        + f" FROM ({record_query}) record"
        + f" LEFT JOIN {episodes_relation} source_episode"
        " ON source_episode.id = record.source_episode_id"
        + f" LEFT JOIN {tombstones_relation} tombstone"
        " ON tombstone.episode_id = record.source_episode_id"
    )


def _is_missing_memory_schema_error(
    exc: Exception,
    *,
    schema_absent_at_start: bool,
    expected_absent_columns: tuple[str, ...] = (),
) -> bool:
    """Return whether *exc* indicates the pool simply lacks memory tables.

    This is the expected, common case only when every required memory table
    was absent when the dashboard started.  An ``UndefinedTableError`` after
    a table was present at startup is schema loss and must be tracked via
    *tracker* in ``_fan_out_memory_queries`` rather than folded into the same
    graceful skip.
    """
    if isinstance(exc, UndefinedTableError):
        return schema_absent_at_start
    msg = str(exc).lower()
    if "does not exist" in msg and ("relation" in msg or "table" in msg):
        return schema_absent_at_start
    # Re-embedding probes columns that only memory-enabled schemas own.  Keep
    # this exemption opt-in per query so a missing column elsewhere remains a
    # genuine query fault rather than silently looking like an absent schema.
    return (
        bool(expected_absent_columns)
        and "column" in msg
        and "does not exist" in msg
        and any(f'"{column.lower()}"' in msg for column in expected_absent_columns)
    )


async def _fan_out_memory_queries(
    db: DatabaseManager,
    *,
    query_name: str,
    query_fn: Callable[[str, object], Awaitable[object | None]],
    butler_filter: str | None = None,
    tracker: DegradedSources,
    expected_absent_columns: tuple[str, ...] = (),
) -> list[object]:
    """Run a query across candidate pools and skip pools without memory schema.

    When *butler_filter* is provided the fan-out is restricted to the single
    pool owned by that butler.  If that butler is unknown the function returns
    immediately with an empty list, avoiding unnecessary pool probing.

    Every caller supplies *tracker*. A pool failing for any reason OTHER than
    "this pool has no memory tables" (see ``_is_missing_memory_schema_error``)
    is recorded on it -- callers surface ``tracker.names`` in the response
    envelope or choose an honest error response, so a genuinely unreachable
    pool is never indistinguishable from a truthful empty result.
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
        except Exception as exc:
            if not _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=_memory_schema_absent_at_start(db, name),
                expected_absent_columns=expected_absent_columns,
            ):
                tracker.mark(name, msg=f"memory query {query_name!r} failed")
            logger.debug(
                "Skipping pool %s for memory query %s (pool lacks memory tables or query failed)",
                name,
                query_name,
                exc_info=True,
            )
            return None

    results = await asyncio.gather(*(_run(name, pool) for name, pool in pools))
    return [result for result in results if result is not None]


def _raise_memory_detail_miss(
    *,
    resource: str,
    tracker: DegradedSources,
) -> None:
    """Raise a truthful error for an unresolved cross-pool detail lookup."""
    if tracker.failed:
        names = ", ".join(tracker.names)
        raise HTTPException(
            status_code=503,
            detail=(
                f"{resource} detail unavailable: {len(tracker.names)} butler database(s) "
                f"unreachable ({names}); the {resource.lower()} may live in a pool "
                "that could not be queried."
            ),
        )
    raise HTTPException(status_code=404, detail=f"{resource} not found")


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


def _row_to_memory_link(row: dict) -> MemoryLink:
    """Convert a storage link row to the typed, content-free API projection."""
    return MemoryLink(
        source_type=row["source_type"],
        source_id=str(row["source_id"]),
        target_type=row["target_type"],
        target_id=str(row["target_id"]),
        relation=row["relation"],
        created_at=str(row["created_at"]),
        source_episode_status=row["source_episode_status"],
        target_episode_status=row["target_episode_status"],
    )


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
    from butlers.modules.memory import storage as _storage

    catalog_tracker = DegradedSources(logger)
    retention_tracker = DegradedSources(logger)

    async def _catalog_drift_for_pool(butler_name: str, pool: object) -> dict[str, int]:
        # public.memory_catalog tags rows with the owning butler's schema
        # (source_schema), so scope the drift query to THIS pool's own
        # schema — resolved the same way the backfill job infers it (see
        # ``scheduled_jobs._infer_current_schema``) — to avoid every pool
        # double-counting the whole shared catalog table.
        memory_schema = _memory_source_schema(db, butler_name)
        if memory_schema is not None:
            schema = memory_schema
        else:
            try:
                schema = await pool.fetchval("SELECT current_schema()")
            except Exception as exc:
                if _is_missing_memory_schema_error(
                    exc,
                    schema_absent_at_start=_memory_schema_absent_at_start(db, butler_name),
                ):
                    logger.debug(
                        "Skipping catalog-drift gauge for butler %s (no memory schema)",
                        butler_name,
                        exc_info=True,
                    )
                    return {"live": 0, "stale": 0, "drifted": 0}
                logger.warning(
                    "Failed to resolve current_schema() for butler %s; omitting "
                    "catalog-drift gauge for this pool",
                    butler_name,
                    exc_info=True,
                )
                catalog_tracker.mark(butler_name, msg="catalog-drift current_schema() failed")
                return {"live": 0, "stale": 0, "drifted": 0}
        if not schema or schema == "public":
            # No real butler schema resolved (e.g. memory tables living
            # directly in public in a test topology) — legitimately absent,
            # not a degraded source.
            return {"live": 0, "stale": 0, "drifted": 0}
        try:
            return await _storage.get_catalog_drift_counts(
                pool,
                source_schema=schema,
                memory_schema=memory_schema,
            )
        except Exception as exc:
            if _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=_memory_schema_absent_at_start(db, butler_name),
            ):
                # This butler has no facts/rules tables (memory module not
                # enabled) — legitimately absent, not a degraded source.
                logger.debug(
                    "Skipping catalog-drift gauge for butler %s (no memory schema)",
                    butler_name,
                    exc_info=True,
                )
                return {"live": 0, "stale": 0, "drifted": 0}
            logger.warning(
                "Failed to compute catalog-drift counts for butler %s",
                butler_name,
                exc_info=True,
            )
            catalog_tracker.mark(butler_name, msg="catalog-drift query failed")
            return {"live": 0, "stale": 0, "drifted": 0}

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
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        facts_relation = _memory_relation(db, butler_name, "facts")
        rules_relation = _memory_relation(db, butler_name, "rules")
        return {
            "total_episodes": await pool.fetchval(f"SELECT count(*) FROM {episodes_relation}") or 0,
            "unconsolidated_episodes": await pool.fetchval(
                f"SELECT count(*) FROM {episodes_relation} WHERE consolidated = false"
            )
            or 0,
            "dead_letter_episodes": await pool.fetchval(
                f"SELECT count(*) FROM {episodes_relation} "
                "WHERE consolidation_status = 'dead_letter'"
            )
            or 0,
            "total_facts": await pool.fetchval(f"SELECT count(*) FROM {facts_relation}") or 0,
            "active_facts": await pool.fetchval(
                f"SELECT count(*) FROM {facts_relation} WHERE validity = 'active'"
            )
            or 0,
            "fading_facts": await pool.fetchval(
                f"SELECT count(*) FROM {facts_relation} WHERE validity = 'fading'"
            )
            or 0,
            "total_rules": await pool.fetchval(f"SELECT count(*) FROM {rules_relation}") or 0,
            # Maturity buckets exclude forgotten rules (metadata->>'forgotten' —
            # rules have no validity column, so this JSONB flag is the sole
            # soft-delete signal; see forget_memory/run_decay_sweep in storage.py).
            # A forgotten rule is not a live belief and must not inflate any
            # maturity count, matching the memory_stats MCP tool's convention.
            "candidate_rules": await pool.fetchval(
                f"SELECT count(*) FROM {rules_relation} WHERE maturity = 'candidate'"
                " AND (metadata->>'forgotten')::boolean IS NOT TRUE"
            )
            or 0,
            "established_rules": await pool.fetchval(
                f"SELECT count(*) FROM {rules_relation} WHERE maturity = 'established'"
                " AND (metadata->>'forgotten')::boolean IS NOT TRUE"
            )
            or 0,
            "proven_rules": await pool.fetchval(
                f"SELECT count(*) FROM {rules_relation} WHERE maturity = 'proven'"
                " AND (metadata->>'forgotten')::boolean IS NOT TRUE"
            )
            or 0,
            "anti_pattern_rules": await pool.fetchval(
                f"SELECT count(*) FROM {rules_relation} WHERE maturity = 'anti_pattern'"
                " AND (metadata->>'forgotten')::boolean IS NOT TRUE"
            )
            or 0,
            "last_consolidation_at": last_run["consolidated_at"] if last_run else None,
            "last_consolidation_facts_produced": (last_run["facts_produced"] if last_run else None),
        }

    async def _retention_for_pool(butler_name: str, pool: object) -> RetentionSourceObservation:
        """Observe the cleanup population without changing it.

        ``expired_retained_episodes`` counts exactly the episodes the cleanup
        sweep is *allowed* to delete but has not yet reaped — genuine cleanup
        lag — by reusing ``run_episode_cleanup``'s own reap predicate verbatim
        (:data:`REAPABLE_EXPIRED_EPISODE_SQL`). An expired episode the sweep is
        deliberately still holding for consolidation (pending, within the grace
        window) is therefore *not* counted, so a healthy steady state never
        reads as a degraded source. This is a read-only deadman, never a cleanup
        trigger or an alternate definition of expiry.
        """
        from butlers.modules.memory.consolidation import REAPABLE_EXPIRED_EPISODE_SQL

        episodes_relation = _memory_relation(db, butler_name, "episodes")
        row = await pool.fetchrow(
            f"SELECT "
            f"count(*) FILTER (WHERE {REAPABLE_EXPIRED_EPISODE_SQL}) "
            f"    AS expired_retained_episodes, "
            f"count(*) FILTER (WHERE expires_at IS NOT NULL) AS retention_eligible_episodes "
            f"FROM {episodes_relation}"
        )
        if row is None:
            raise RuntimeError("expired-retention aggregate query returned no row")
        expired_retained_episodes = int(row["expired_retained_episodes"] or 0)
        retention_eligible_episodes = int(row["retention_eligible_episodes"] or 0)
        return RetentionSourceObservation(
            source_butler=butler_name,
            source_schema=_memory_source_schema(db, butler_name),
            expired_retained_episodes=expired_retained_episodes,
            retention_eligible_episodes=retention_eligible_episodes,
            expired_retained_ratio=(
                expired_retained_episodes / retention_eligible_episodes
                if retention_eligible_episodes
                else None
            ),
        )

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="stats",
        query_fn=_stats_for_pool,
        tracker=tracker,
    )
    # Catalog drift is a separate fan-out (its own tracker) so a pool that
    # fails only the drift query doesn't drop that pool's episode/fact/rule
    # counts from `totals` above — see _catalog_drift_for_pool.
    catalog_per_pool = await _fan_out_memory_queries(
        db,
        query_name="catalog_drift",
        query_fn=_catalog_drift_for_pool,
        tracker=catalog_tracker,
    )
    retention_sources = await _fan_out_memory_queries(
        db,
        query_name="expired_retention",
        query_fn=_retention_for_pool,
        tracker=retention_tracker,
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

    # Catalog-drift gauge (bu-5ud8p.4): aggregate live/stale/drifted counts
    # across all butler schemas so the console can see catalog health at a
    # glance. ``catalog_drifted`` is the leading indicator — non-zero means
    # the shared discovery catalog is currently serving memories the owning
    # butler has since disowned (source gone, forgotten, or terminal), and
    # should trend toward zero as the backfill job's reconciliation phase
    # (``run_memory_catalog_backfill``) runs.
    catalog_live = sum(row["live"] for row in catalog_per_pool)
    catalog_stale = sum(row["stale"] for row in catalog_per_pool)
    catalog_drifted = sum(row["drifted"] for row in catalog_per_pool)

    if retention_tracker.failed:
        retention_status = "unknown"
    else:
        totals.expired_retained_episodes = sum(
            source.expired_retained_episodes for source in retention_sources
        )
        totals.retention_eligible_episodes = sum(
            source.retention_eligible_episodes for source in retention_sources
        )
        totals.expired_retained_ratio = (
            totals.expired_retained_episodes / totals.retention_eligible_episodes
            if totals.retention_eligible_episodes
            else None
        )
        retention_status = "degraded" if totals.expired_retained_episodes > 0 else "healthy"

    # Graph health is an additive compatibility read model, not a separate
    # graph/provenance measurement. Reuse the completed retention observations
    # exactly: their numerator is the owner-selected consolidation-aware cleanup
    # lag population, and their denominator is `expires_at IS NOT NULL`. A
    # stats/schema failure also invalidates a completed graph observation even
    # if the standalone retention aggregate could still read episodes.
    graph_health_unknown_names = set(tracker.names) | set(retention_tracker.names)
    graph_health_sources = [
        source
        for source in retention_sources
        if source.source_butler not in graph_health_unknown_names
    ]
    graph_health_pools = [
        GraphHealthPoolCoverage(
            source_butler=source.source_butler,
            source_schema=source.source_schema,
            coverage="complete",
            reapable_expired_episodes=source.expired_retained_episodes,
            retention_eligible_episodes=source.retention_eligible_episodes,
            reapable_expired_ratio=source.expired_retained_ratio,
        )
        for source in graph_health_sources
    ]
    graph_health_pools.extend(
        GraphHealthPoolCoverage(
            source_butler=butler_name,
            # The existing failed-source tracker only proves the butler name.
            # Keep the optional schema unknown rather than re-resolving state
            # outside the fan-out and risking a new failure while reporting one.
            source_schema=None,
            coverage="unknown",
            reapable_expired_episodes=None,
            retention_eligible_episodes=None,
            reapable_expired_ratio=None,
        )
        for butler_name in sorted(graph_health_unknown_names)
    )
    graph_health_pools.sort(key=lambda pool: pool.source_butler)
    if not graph_health_sources:
        graph_health_coverage = "unknown"
    elif graph_health_unknown_names:
        graph_health_coverage = "incomplete"
    else:
        graph_health_coverage = "complete"

    meta_fields: dict[str, object] = {
        "catalog_live": catalog_live,
        "catalog_stale": catalog_stale,
        "catalog_drifted": catalog_drifted,
        "retention_status": retention_status,
        "retention_sources": retention_sources,
        "graph_health": GraphHealthCoverage(
            coverage=graph_health_coverage,
            pools=graph_health_pools,
        ),
    }
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    if catalog_tracker.failed:
        meta_fields["catalog_pools_failed"] = catalog_tracker.names
    if retention_tracker.failed:
        meta_fields["retention_pools_failed"] = retention_tracker.names

    meta = ApiMeta(**meta_fields)
    return ApiResponse[MemoryStats](data=totals, meta=meta)


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

    async def _query_pool(butler_name: str, pool: object) -> tuple[int, list[object]]:
        relation = _memory_relation(db, butler_name, "episodes")
        total = await pool.fetchval(f"SELECT count(*) FROM {relation}{where}", *args) or 0
        rows = await pool.fetch(
            f"SELECT id, butler, session_id, content, importance, reference_count,"
            f" consolidated, consolidation_status, created_at, last_referenced_at,"
            f" expires_at, metadata"
            f" FROM {relation}{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}",
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="episodes",
        query_fn=_query_pool,
        butler_filter=butler,
        tracker=tracker,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_episode(r) for r in rows]

    meta_fields: dict[str, object] = {"total": total, "offset": offset, "limit": limit}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return PaginatedResponse[Episode](data=data, meta=PaginationMeta(**meta_fields))


# ---------------------------------------------------------------------------
# GET /api/memory/episodes/{episode_id}
# ---------------------------------------------------------------------------


@router.get("/episodes/{episode_id}", response_model=ApiResponse[Episode])
async def get_episode(
    episode_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Episode]:
    """Return a single episode by ID."""

    async def _query_pool(butler_name: str, pool: object):
        relation = _memory_relation(db, butler_name, "episodes")
        return await pool.fetchrow(
            "SELECT id, butler, session_id, content, importance, reference_count,"
            " consolidated, consolidation_status, created_at, last_referenced_at,"
            " expires_at, metadata"
            f" FROM {relation} WHERE id = $1",
            episode_id,
        )

    tracker = DegradedSources(logger)
    rows = await _fan_out_memory_queries(
        db,
        query_name="episode_by_id",
        query_fn=_query_pool,
        tracker=tracker,
    )
    if not rows:
        _raise_memory_detail_miss(resource="Episode", tracker=tracker)

    return ApiResponse[Episode](data=_row_to_episode(rows[0]))


# ---------------------------------------------------------------------------
# GET /api/memory/links/{memory_type}/{memory_id}
# ---------------------------------------------------------------------------


@router.get("/links/{memory_type}/{memory_id}", response_model=ApiResponse[list[MemoryLink]])
async def list_memory_links(
    memory_type: Literal["episode", "fact", "rule"],
    memory_id: _uuid.UUID,
    direction: Literal["incoming", "outgoing", "both"] = Query("both"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryLink]]:
    """Return content-free provenance links for one memory record.

    A link endpoint that names an episode carries its typed availability state;
    only an ``available`` endpoint is a live episode reference.
    """

    async def _query_pool(butler_name: str, pool: object) -> list[dict]:
        return await get_links(
            pool,
            memory_type,
            memory_id,
            direction=direction,
            memory_schema=_memory_source_schema(db, butler_name),
        )

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="memory_links",
        query_fn=_query_pool,
        tracker=tracker,
    )
    links = [link for pool_links in per_pool for link in pool_links]
    links.sort(key=lambda link: link["created_at"], reverse=True)

    meta_fields: dict[str, object] = {}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return ApiResponse[list[MemoryLink]](
        data=[_row_to_memory_link(link) for link in links],
        meta=ApiMeta(**meta_fields),
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

    async def _query_pool(butler_name: str, pool: object) -> tuple[int, list[object]]:
        relation = _memory_relation(db, butler_name, "facts")
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        total = await pool.fetchval(f"SELECT count(*) FROM {relation}{where}", *args) or 0
        record_query = (
            f"SELECT id, subject, predicate, content, importance, confidence,"
            f" decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
            f" entity_id, object_entity_id, validity, scope, reference_count,"
            f" created_at, last_referenced_at,"
            f" last_confirmed_at, tags, metadata"
            f" FROM {relation}{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}"
        )
        rows = await pool.fetch(
            _with_source_episode_status(
                record_query,
                episodes_relation=episodes_relation,
                tombstones_relation=tombstones_relation,
            ),
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="facts",
        query_fn=_query_pool,
        tracker=tracker,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_fact(r) for r in rows]
    data = await _resolve_entity_names(db, data)

    meta_fields: dict[str, object] = {"total": total, "offset": offset, "limit": limit}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return PaginatedResponse[Fact](data=data, meta=PaginationMeta(**meta_fields))


# ---------------------------------------------------------------------------
# GET /api/memory/facts/{fact_id}
# ---------------------------------------------------------------------------


@router.get("/facts/{fact_id}", response_model=ApiResponse[Fact])
async def get_fact(
    fact_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Fact]:
    """Return a single fact by ID."""

    async def _query_pool(butler_name: str, pool: object):
        relation = _memory_relation(db, butler_name, "facts")
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        row = await pool.fetchrow(
            _with_source_episode_status(
                "SELECT id, subject, predicate, content, importance, confidence,"
                " decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
                " entity_id, object_entity_id, validity, scope, reference_count,"
                " created_at, last_referenced_at,"
                " last_confirmed_at, tags, metadata"
                f" FROM {relation} WHERE id = $1",
                episodes_relation=episodes_relation,
                tombstones_relation=tombstones_relation,
            ),
            fact_id,
        )
        if row is None:
            return None
        # Reverse-lookup the fact that supersedes THIS one (if any).  Runs on the
        # same pool that owns the fact, so we never cross butler schemas.
        superseder = await pool.fetchrow(
            f"SELECT id FROM {relation} WHERE supersedes_id = $1 LIMIT 1",
            fact_id,
        )
        return (row, superseder)

    tracker = DegradedSources(logger)
    results = await _fan_out_memory_queries(
        db,
        query_name="fact_by_id",
        query_fn=_query_pool,
        tracker=tracker,
    )
    if not results:
        _raise_memory_detail_miss(resource="Fact", tracker=tracker)

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
)


def _fact_select_query(relation: str, *, episodes_relation: str, tombstones_relation: str) -> str:
    """Return the fact detail query pinned to its local memory relation."""
    return _with_source_episode_status(
        f"{_FACT_SELECT_COLUMNS} FROM {relation} WHERE id = $1",
        episodes_relation=episodes_relation,
        tombstones_relation=tombstones_relation,
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
    # updated row. A relation that was present at startup but later disappeared
    # is an unavailable source, not a reason to search_path-fall through to
    # public or report a misleading 404.
    tracker = DegradedSources(logger)
    for name, pool in pools:
        try:
            memory_schema = _memory_source_schema(db, name)
            relation = _memory_relation(db, name, "facts")
            episodes_relation = _memory_relation(db, name, "episodes")
            tombstones_relation = _memory_relation(db, name, "episode_tombstones")
            confirmed = await _storage.confirm_memory(
                pool,
                "fact",
                fact_uuid,
                memory_schema=memory_schema,
            )
            if not confirmed:
                continue
            row = await pool.fetchrow(
                _fact_select_query(
                    relation,
                    episodes_relation=episodes_relation,
                    tombstones_relation=tombstones_relation,
                ),
                fact_uuid,
            )
        except Exception as exc:
            if not _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=_memory_schema_absent_at_start(db, name),
            ):
                tracker.mark(name, msg="fact confirmation source unavailable")
            logger.debug(
                "Skipping pool %s while confirming fact %s (pool lacks memory tables or failed)",
                name,
                fact_id,
                exc_info=True,
            )
            continue
        if row is None:
            continue
        fact = _row_to_fact(row)
        await _resolve_entity_names(db, [fact])
        return ApiResponse[Fact](data=fact)

    _raise_memory_detail_miss(resource="Fact", tracker=tracker)


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
    # updated row. See confirm_fact for the schema-loss classification rule.
    tracker = DegradedSources(logger)
    for name, pool in pools:
        try:
            memory_schema = _memory_source_schema(db, name)
            relation = _memory_relation(db, name, "facts")
            episodes_relation = _memory_relation(db, name, "episodes")
            tombstones_relation = _memory_relation(db, name, "episode_tombstones")
            retracted = await _storage.forget_memory(
                pool,
                "fact",
                fact_uuid,
                memory_schema=memory_schema,
            )
            if not retracted:
                continue
            row = await pool.fetchrow(
                _fact_select_query(
                    relation,
                    episodes_relation=episodes_relation,
                    tombstones_relation=tombstones_relation,
                ),
                fact_uuid,
            )
        except Exception as exc:
            if not _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=_memory_schema_absent_at_start(db, name),
            ):
                tracker.mark(name, msg="fact retraction source unavailable")
            logger.debug(
                "Skipping pool %s while retracting fact %s (pool lacks memory tables or failed)",
                name,
                fact_id,
                exc_info=True,
            )
            continue
        if row is None:
            continue
        fact = _row_to_fact(row)
        await _resolve_entity_names(db, [fact])
        return ApiResponse[Fact](data=fact)

    _raise_memory_detail_miss(resource="Fact", tracker=tracker)


# ---------------------------------------------------------------------------
# GET /api/memory/rules
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=PaginatedResponse[Rule])
async def list_rules(
    q: str | None = Query(None, description="Text search query"),
    scope: str | None = Query(None, description="Filter by scope"),
    maturity: str | None = Query(None, description="Filter by maturity"),
    forgotten: bool | None = Query(
        None,
        description=(
            "Filter by forgotten (soft-deleted) status. Omit (default) to "
            "exclude forgotten rules — a forgotten rule is not a live "
            "standing order. Pass true to view only forgotten rules "
            "(audit), or false to be explicit about the default."
        ),
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Rule]:
    """List/search rules with optional filters, paginated.

    Forgotten rules (soft-deleted via ``memory_forget`` or decay expiry —
    ``metadata->>'forgotten' = 'true'``) are excluded by default so this
    register never counts a retracted rule as a live standing order. Pass
    ``?forgotten=true`` to audit forgotten rules explicitly.
    """
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

    if forgotten:
        conditions.append("(metadata->>'forgotten')::boolean IS TRUE")
    else:
        # Default (forgotten is None or explicitly False): live rules only.
        conditions.append("(metadata->>'forgotten')::boolean IS NOT TRUE")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    row_limit = offset + limit

    async def _query_pool(butler_name: str, pool: object) -> tuple[int, list[object]]:
        relation = _memory_relation(db, butler_name, "rules")
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        total = await pool.fetchval(f"SELECT count(*) FROM {relation}{where}", *args) or 0
        record_query = (
            f"SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
            f" effectiveness_score, applied_count, success_count, harmful_count,"
            f" source_episode_id, source_butler, created_at, last_applied_at,"
            f" last_evaluated_at, tags, metadata"
            f" FROM {relation}{where}"
            f" ORDER BY created_at DESC"
            f" OFFSET ${idx} LIMIT ${idx + 1}"
        )
        rows = await pool.fetch(
            _with_source_episode_status(
                record_query,
                episodes_relation=episodes_relation,
                tombstones_relation=tombstones_relation,
            ),
            *args,
            0,
            row_limit,
        )
        return total, list(rows)

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="rules",
        query_fn=_query_pool,
        tracker=tracker,
    )
    total = sum(pool_total for pool_total, _ in per_pool)
    merged_rows: list[object] = []
    for _, rows in per_pool:
        merged_rows.extend(rows)
    merged_rows = _sort_rows_by_created_at(merged_rows)
    rows = merged_rows[offset : offset + limit]

    data = [_row_to_rule(r) for r in rows]

    meta_fields: dict[str, object] = {"total": total, "offset": offset, "limit": limit}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return PaginatedResponse[Rule](data=data, meta=PaginationMeta(**meta_fields))


# ---------------------------------------------------------------------------
# GET /api/memory/rules/{rule_id}
# ---------------------------------------------------------------------------


@router.get("/rules/{rule_id}", response_model=ApiResponse[Rule])
async def get_rule(
    rule_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Rule]:
    """Return a single rule by ID."""

    async def _query_pool(butler_name: str, pool: object):
        relation = _memory_relation(db, butler_name, "rules")
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        return await pool.fetchrow(
            _with_source_episode_status(
                "SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
                " effectiveness_score, applied_count, success_count, harmful_count,"
                " source_episode_id, source_butler, created_at, last_applied_at,"
                " last_evaluated_at, tags, metadata"
                f" FROM {relation} WHERE id = $1",
                episodes_relation=episodes_relation,
                tombstones_relation=tombstones_relation,
            ),
            rule_id,
        )

    tracker = DegradedSources(logger)
    rows = await _fan_out_memory_queries(
        db,
        query_name="rule_by_id",
        query_fn=_query_pool,
        tracker=tracker,
    )
    if not rows:
        _raise_memory_detail_miss(resource="Rule", tracker=tracker)

    return ApiResponse[Rule](data=_row_to_rule(rows[0]))


# ---------------------------------------------------------------------------
# GET /api/memory/catalog/search — fleet-knowledge cross-butler discovery
# ---------------------------------------------------------------------------


def _row_to_catalog_result(r: dict, *, mode: str) -> MemoryCatalogSearchResult:
    """Convert a public.memory_catalog search result dict to the API model.

    ``r`` is a plain dict (``dict(asyncpg.Record)``, per ``search_catalog``)
    carrying the catalog's raw columns plus a mode-specific score key
    (``similarity``, ``rank``, or ``rrf_score``) — normalized here to a single
    ``score`` field regardless of which search mode produced the row.
    """
    if mode == "semantic":
        score = r.get("similarity")
    elif mode == "keyword":
        score = r.get("rank")
    else:
        score = r.get("rrf_score")

    valid_at = r.get("valid_at")
    return MemoryCatalogSearchResult(
        id=str(r["id"]),
        source_schema=r["source_schema"],
        source_table=r["source_table"],
        source_id=str(r["source_id"]),
        source_butler=r.get("source_butler"),
        memory_type=r["memory_type"],
        title=r.get("title"),
        summary=r.get("summary") or "",
        predicate=r.get("predicate"),
        scope=r.get("scope"),
        entity_id=str(r["entity_id"]) if r.get("entity_id") else None,
        object_entity_id=str(r["object_entity_id"]) if r.get("object_entity_id") else None,
        valid_at=valid_at.isoformat() if hasattr(valid_at, "isoformat") else valid_at,
        confidence=float(r["confidence"]) if r.get("confidence") is not None else None,
        importance=float(r["importance"]) if r.get("importance") is not None else None,
        retention_class=r.get("retention_class"),
        sensitivity=r.get("sensitivity"),
        score=float(score) if score is not None else None,
    )


@router.get("/catalog/search", response_model=ApiResponse[list[MemoryCatalogSearchResult]])
async def search_memory_catalog(
    query: str = Query(..., min_length=1, description="Search query text"),
    memory_type: str | None = Query(
        None, description="Optional filter: 'fact' or 'rule'. Omit to search both."
    ),
    limit: int = Query(10, ge=1, le=50, description="Max results to return"),
    mode: str = Query(
        "hybrid", description="Search mode: 'hybrid' (default), 'semantic', or 'keyword'."
    ),
    max_sensitivity: str = Query(
        "normal",
        description=(
            "Highest sensitivity level to view: 'normal' (default), 'pii', or "
            "'confidential'. Unknown values fail closed to 'normal'-only."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryCatalogSearchResult]]:
    """Fleet-knowledge search across all butlers via public.memory_catalog.

    Queries the shared discovery index directly. Unlike the other endpoints
    in this router, this is deliberately NOT a per-butler fan-out:
    ``public.memory_catalog`` already aggregates every butler's write-behind
    entries into one table reachable from any butler's connection pool, so a
    single query answers the whole fleet. A pool failure here is a genuine
    outage (surfaced as a normal error response), not a per-source
    degradation that needs folding into a degraded-envelope flag — there is
    only one source to begin with.

    Results are provenance pointers, not canonical memories — use
    ``source_schema``/``source_table``/``source_id`` to fetch the full record
    from the owning butler's own schema if the full item is needed.
    """
    from butlers.modules.memory.search import search_catalog
    from butlers.modules.memory.tools import get_embedding_engine

    pool = _any_pool(db)
    engine = get_embedding_engine(_DEFAULT_EMBEDDING_MODEL)
    try:
        rows = await search_catalog(
            pool,
            query,
            engine,
            memory_type=memory_type,
            limit=limit,
            mode=mode,
            max_sensitivity=max_sensitivity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = [_row_to_catalog_result(r, mode=mode) for r in rows]
    return ApiResponse[list[MemoryCatalogSearchResult]](data=results)


# ---------------------------------------------------------------------------
# GET /api/memory/activity
# ---------------------------------------------------------------------------


@router.get("/activity", response_model=ApiResponse[list[MemoryActivity]])
async def list_activity(
    limit: int = Query(50, ge=1, le=200, description="Max activity items to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[MemoryActivity]]:
    """Return recent memory activity interleaved from all three tables."""

    async def _query_pool(
        butler_name: str, pool: object
    ) -> tuple[list[object], list[object], list[object]]:
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        facts_relation = _memory_relation(db, butler_name, "facts")
        rules_relation = _memory_relation(db, butler_name, "rules")
        episode_rows = await pool.fetch(
            "SELECT id, butler, content, created_at"
            f" FROM {episodes_relation} ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        fact_rows = await pool.fetch(
            "SELECT id, subject, predicate, source_butler, created_at"
            f" FROM {facts_relation} ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        rule_rows = await pool.fetch(
            "SELECT id, content, source_butler, created_at"
            f" FROM {rules_relation} ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return list(episode_rows), list(fact_rows), list(rule_rows)

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="activity",
        query_fn=_query_pool,
        tracker=tracker,
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

    meta_fields: dict[str, object] = {}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return ApiResponse[list[MemoryActivity]](data=items, meta=ApiMeta(**meta_fields))


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
        "NOT (e.roles && ARRAY['google_account', 'steam_account'])",
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
    tracker = DegradedSources(logger)
    if entity_ids:

        async def _count_facts(butler_name: str, fpool: object) -> dict[str, int]:
            relation = _memory_relation(db, butler_name, "facts")
            fc_rows = await fpool.fetch(
                f"SELECT entity_id, count(*) AS cnt FROM {relation}"
                " WHERE entity_id = ANY($1) AND validity IN ('active', 'fading')"
                " GROUP BY entity_id",
                entity_ids,
            )
            return {str(r["entity_id"]): r["cnt"] for r in fc_rows}

        per_pool = await _fan_out_memory_queries(
            db,
            query_name="entity_fact_counts",
            query_fn=_count_facts,
            tracker=tracker,
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

    meta_fields: dict[str, object] = {"total": total, "offset": offset, "limit": limit}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return PaginatedResponse[EntitySummary](data=data, meta=PaginationMeta(**meta_fields))


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

    async def _query_entity_facts(butler_name: str, fpool: object) -> tuple[int, list[object]]:
        facts_relation = _memory_relation(db, butler_name, "facts")
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        count = (
            await fpool.fetchval(
                f"SELECT count(*) FROM {facts_relation}"
                " WHERE (entity_id = $1 OR object_entity_id = $1)"
                " AND validity IN ('active', 'fading')",
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
            ", CASE WHEN f.source_episode_id IS NULL THEN NULL"
            " WHEN ep.id IS NOT NULL THEN 'available'"
            " WHEN tombstone.episode_id IS NOT NULL THEN 'expired'"
            " ELSE 'unresolved' END AS source_episode_status"
            f" FROM {facts_relation} f"
            f" LEFT JOIN {episodes_relation} ep ON ep.id = f.source_episode_id"
            f" LEFT JOIN {tombstones_relation} tombstone"
            " ON tombstone.episode_id = f.source_episode_id"
            " WHERE (f.entity_id = $1 OR f.object_entity_id = $1)"
            " AND f.validity IN ('active', 'fading')"
            " ORDER BY f.created_at DESC"
            " OFFSET $2 LIMIT $3",
            eid,
            0,
            row_limit,
        )
        return count, list(rows)

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="entity_facts",
        query_fn=_query_entity_facts,
        tracker=tracker,
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

    meta_fields: dict[str, object] = {}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return ApiResponse[EntityDetail](data=detail, meta=ApiMeta(**meta_fields))


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

    async def _migrate_facts(butler_name: str, fpool: object) -> int:
        relation = _memory_relation(db, butler_name, "facts")
        result = await fpool.execute(
            f"UPDATE {relation} SET entity_id = $1"
            " WHERE (subject = $2 OR subject = $3)"
            " AND entity_id IS NULL"
            " AND validity IN ('active', 'fading')",
            eid,
            prefixed_subject,
            cid_str,
        )
        # asyncpg returns 'UPDATE N' — extract the count
        return int(result.split()[-1]) if result else 0

    tracker = DegradedSources(logger)
    counts = await _fan_out_memory_queries(
        db,
        query_name="migrate_contact_facts",
        query_fn=_migrate_facts,
        tracker=tracker,
    )
    if tracker.failed:
        names = ", ".join(tracker.names)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Contact fact migration incomplete: {len(tracker.names)} butler database(s) "
                f"unreachable ({names}); one or more pools may not have migrated their facts."
            ),
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

    Owner entities cannot be deleted (returns 403).  When active or fading facts
    exist, returns 409 with the count unless ``retire_facts=true`` is passed, in
    which case all active/fading facts are retired (validity → 'retracted') first.
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

    # Check active/fading facts referencing this entity across all memory pools.
    # 'fading' facts are still live (not yet superseded/expired/retracted) and
    # must block/retire the same as 'active' ones, or deleting an entity that
    # only has fading facts would silently orphan them.
    async def _count_active_facts(butler_name: str, fpool: object) -> int:
        relation = _memory_relation(db, butler_name, "facts")
        return (
            await fpool.fetchval(
                f"SELECT count(*) FROM {relation}"
                " WHERE entity_id = $1 AND validity IN ('active', 'fading')",
                eid,
            )
            or 0
        )

    precheck_tracker = DegradedSources(logger)
    per_pool_counts = await _fan_out_memory_queries(
        db,
        query_name="delete_entity_fact_check",
        query_fn=_count_active_facts,
        tracker=precheck_tracker,
    )
    if precheck_tracker.failed:
        names = ", ".join(precheck_tracker.names)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Entity deletion unavailable: {len(precheck_tracker.names)} butler database(s) "
                f"unreachable ({names}); unable to verify whether the entity has active facts."
            ),
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
        async def _retire_facts(butler_name: str, fpool: object) -> int:
            relation = _memory_relation(db, butler_name, "facts")
            await fpool.execute(
                f"UPDATE {relation} SET validity = 'retracted'"
                " WHERE entity_id = $1 AND validity IN ('active', 'fading')",
                eid,
            )
            return 0

        retire_tracker = DegradedSources(logger)
        await _fan_out_memory_queries(
            db,
            query_name="delete_entity_retire_facts",
            query_fn=_retire_facts,
            tracker=retire_tracker,
        )
        if retire_tracker.failed:
            names = ", ".join(retire_tracker.names)
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Entity deletion incomplete: {len(retire_tracker.names)} butler database(s) "
                    f"unreachable ({names}); fact retirement may have only partially completed."
                ),
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
    source_episode_id = str(r["source_episode_id"]) if r["source_episode_id"] else None
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
        source_episode_id=source_episode_id,
        source_episode_status=(
            str(r["source_episode_status"])
            if r.get("source_episode_status")
            else ("unresolved" if source_episode_id else None)
        ),
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
    source_episode_id = str(r["source_episode_id"]) if r["source_episode_id"] else None
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
        source_episode_id=source_episode_id,
        source_episode_status=(
            str(r["source_episode_status"])
            if r.get("source_episode_status")
            else ("unresolved" if source_episode_id else None)
        ),
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
    - 503: A memory table that was present at startup is unavailable, or its
      startup presence is unknown.
    """
    if name not in db.butler_names:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    # Run one batched query per table concurrently: each fetchrow returns (total, count_24h)
    # using COUNT(*) FILTER (...) to fetch both counts in a single round-trip.
    # A table is only an expected zero when every memory relation was absent at
    # startup. A post-start drop or unknown startup state fails closed instead
    # of turning a broken private source into a calm all-zero response.

    _INTERVAL = "NOW() - INTERVAL '1 day'"

    schema_absent_at_start = _memory_schema_absent_at_start(db, name)

    async def _count_memory_table(table: str) -> tuple[int, int]:
        relation = _memory_relation(db, name, table)
        try:
            row = await pool.fetchrow(
                f"SELECT"
                f"  count(*) AS total,"
                f"  count(*) FILTER (WHERE created_at > {_INTERVAL}) AS recent"
                f" FROM {relation}"
            )
            return (row["total"] or 0, row["recent"] or 0) if row else (0, 0)
        except Exception as exc:
            if _is_missing_memory_schema_error(
                exc,
                schema_absent_at_start=schema_absent_at_start,
            ):
                logger.debug("%s table not available for butler '%s'; returning zeros", table, name)
                return (0, 0)
            logger.warning("%s table unavailable for butler '%s'", table, name, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Memory stats unavailable for butler '{name}': {table} table is unavailable"
                ),
            ) from exc

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

    (
        (total_episodes, episodes_24h),
        (total_facts, facts_24h),
        (total_entities, entities_24h),
        (total_rules, rules_24h),
    ) = await asyncio.gather(
        _count_memory_table("episodes"),
        _count_memory_table("facts"),
        _count_entities(),
        _count_memory_table("rules"),
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
    """Bulk-update retention policies; one audit entry per changed row.

    Each row's UPSERT and its audit entry run inside the same transaction
    (acquired connection + ``conn.transaction()``) so that if the audit table
    is unavailable, that row's state change rolls back too instead of
    persisting un-audited. ``AuditTableNotAvailableError`` is intentionally
    NOT caught here — it propagates to the app-level handler, which returns
    ``503 {"error": "audit_unavailable"}`` (dashboard-audit-log spec).
    """
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
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
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
            await _audit.append(
                conn,
                "owner",
                "memory.retention_policy",
                target=f"kind:{entry.kind}",
                note=f"ttl_days={entry.ttl_days} max_rows={entry.max_rows}",
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
    async def _query_pool(butler_name: str, pool: object) -> list[dict]:
        results: list[dict] = []
        episodes_relation = _memory_relation(db, butler_name, "episodes")
        tombstones_relation = _memory_relation(db, butler_name, "episode_tombstones")
        facts_relation = _memory_relation(db, butler_name, "facts")
        rules_relation = _memory_relation(db, butler_name, "rules")

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
                f" FROM {episodes_relation}{ep_cond}"
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
                _with_source_episode_status(
                    f"SELECT id, subject, predicate, content, importance, confidence,"
                    f" decay_rate, permanence, source_butler, source_episode_id, supersedes_id,"
                    f" entity_id, object_entity_id, validity, scope, reference_count,"
                    f" created_at, last_referenced_at,"
                    f" last_confirmed_at, tags, metadata"
                    f" FROM {facts_relation}{fact_cond}"
                    f" ORDER BY created_at DESC"
                    f" LIMIT ${idx}",
                    episodes_relation=episodes_relation,
                    tombstones_relation=tombstones_relation,
                ),
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
            # Forgotten rules are excluded unconditionally here (no override,
            # unlike GET /rules) — this is the inspect search bar, and the MCP
            # recall/keyword_search paths (search.py) already hard-exclude
            # forgotten rules from search results the same way.
            forgotten_clause = "(metadata->>'forgotten')::boolean IS NOT TRUE"
            rule_args: list[object] = []
            idx = 1
            if q:
                rule_cond = (
                    f" WHERE search_vector @@ plainto_tsquery('english', ${idx})"
                    f" AND {forgotten_clause}"
                )
                rule_args.append(q)
                idx += 1
            else:
                rule_cond = f" WHERE {forgotten_clause}"
            rule_rows = await pool.fetch(
                _with_source_episode_status(
                    f"SELECT id, content, scope, maturity, confidence, decay_rate, permanence,"
                    f" effectiveness_score, applied_count, success_count, harmful_count,"
                    f" source_episode_id, source_butler, created_at, last_applied_at,"
                    f" last_evaluated_at, tags, metadata"
                    f" FROM {rules_relation}{rule_cond}"
                    f" ORDER BY created_at DESC"
                    f" LIMIT ${idx}",
                    episodes_relation=episodes_relation,
                    tombstones_relation=tombstones_relation,
                ),
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

    tracker = DegradedSources(logger)
    per_pool = await _fan_out_memory_queries(
        db,
        query_name="inspect",
        query_fn=_query_pool,
        tracker=tracker,
    )

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
    meta_fields: dict[str, object] = {"total": total, "offset": offset, "limit": limit}
    if tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return PaginatedResponse[MemoryInspectResult](data=data, meta=PaginationMeta(**meta_fields))


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

    tracker = DegradedSources(logger)
    if butler is not None:
        try:
            pool = db.pool(butler)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No pool available for butler '{butler}'")
        memory_schema = _memory_source_schema(db, butler)
        if _memory_schema_absent_at_start(db, butler):
            counts = dict.fromkeys(_reembedding.ALL_TIERS, 0)
        else:
            try:
                counts = await _reembedding.count_pending(
                    pool,
                    current_model,
                    memory_schema=memory_schema,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                if _is_missing_memory_schema_error(
                    exc,
                    schema_absent_at_start=_memory_schema_absent_at_start(db, butler),
                    expected_absent_columns=("embedding", "embedding_model_version"),
                ):
                    counts = dict.fromkeys(_reembedding.ALL_TIERS, 0)
                else:
                    logger.warning(
                        "Re-embedding pending counts unavailable for butler %s",
                        butler,
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail=f"Re-embedding source unavailable for butler '{butler}'",
                    ) from exc
    else:
        results = await _fan_out_memory_queries(
            db,
            query_name="reembed_pending",
            query_fn=lambda name, pool: _reembedding.count_pending(
                pool,
                current_model,
                memory_schema=_memory_source_schema(db, name),
            ),
            tracker=tracker,
            expected_absent_columns=("embedding", "embedding_model_version"),
        )
        counts = dict.fromkeys(_reembedding.ALL_TIERS, 0)
        for result in results:
            for tier, count in result.items():
                counts[tier] = counts.get(tier, 0) + count

    meta_fields: dict[str, object] = {}
    if butler is None and tracker.failed:
        meta_fields["pools_failed"] = tracker.names
    return ApiResponse[ReembedPendingCounts](
        data=ReembedPendingCounts(
            counts=counts,
            total=sum(counts.values()),
            current_model=current_model,
        ),
        meta=ApiMeta(**meta_fields),
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

    if _memory_schema_absent_at_start(db, body.butler):
        tier_names = list(_reembedding.ALL_TIERS) if body.tiers is None else list(body.tiers)
        unknown_tiers = [tier for tier in tier_names if tier not in _reembedding.ALL_TIERS]
        if unknown_tiers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown tiers: {unknown_tiers}. Must be from: "
                    f"{sorted(_reembedding.ALL_TIERS)}"
                ),
            )
        return ApiResponse[ReembedRunResult](
            data=ReembedRunResult(
                dry_run=body.dry_run,
                current_model=body.current_model,
                tiers_processed=tier_names,
                counts=dict.fromkeys(tier_names, 0),
                total=0,
                errors=[],
            )
        )

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
            memory_schema=_memory_source_schema(db, body.butler),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "Re-embedding source unavailable for butler %s",
            body.butler,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Re-embedding source unavailable for butler '{body.butler}'",
        ) from exc

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
