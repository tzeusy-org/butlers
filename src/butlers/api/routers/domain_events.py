"""Domain-event bus — read-only dashboard discovery endpoints.

bu-317s5 (domain-event bus slice 2). Exposes ``public.butler_subscriptions``
and ``public.domain_event_deliveries`` so a butler's standing subscriptions
and recent fan-out deliveries are discoverable outside its own MCP session --
previously only reachable via ``list_my_subscriptions()`` from inside the
subscribing butler itself, or a direct psql query. See
``src/butlers/core/domain_events.py`` for the writer/reader this router
delegates to.

Mirrors ``butlers.api.routers.delegation``'s shape exactly: both
``public.butler_subscriptions``/``public.domain_event_deliveries`` and
``public.delegation_ledger`` are cross-butler tables reachable from any
pool, so this is deliberately NOT a per-butler fan-out.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as PathParam

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.domain_events import (
    ContractEntry,
    DeliveryEntry,
    ReactionEntry,
    ReactionSummary,
    SubscriptionEntry,
)
from butlers.core.domain_event_reactions import (
    latest_reactions_for_events,
    list_reactions_for_event,
)
from butlers.core.domain_events import (
    VALID_DELIVERY_STATUSES,
    list_recent_deliveries,
    list_subscriptions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/domain-events", tags=["domain-events"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _any_pool(db: DatabaseManager) -> object:
    """Return any available pool — the domain-event-bus tables are public,
    reachable from every butler's pool."""
    for name in sorted(db.butler_names):
        try:
            return db.pool(name)
        except KeyError:
            continue
    raise HTTPException(status_code=503, detail="No database pools available")


def _subscription_to_entry(row: dict) -> SubscriptionEntry:
    return SubscriptionEntry(
        id=str(row["id"]),
        subscriber_butler=row["subscriber_butler"],
        event_type=row["event_type"],
        active=row["active"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _decode_json_list(value: object) -> list:
    """asyncpg hands JSONB back as a str on pools without the codec registered."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
        return decoded if isinstance(decoded, list) else []
    return list(value) if isinstance(value, list) else []


def _reaction_to_entry(row: dict) -> ReactionEntry:
    return ReactionEntry(
        id=str(row["id"]),
        event_id=str(row["event_id"]),
        subscriber_butler=row["subscriber_butler"],
        status=row["status"],
        session_id=row.get("session_id"),
        task_name=row.get("task_name"),
        note=row.get("note"),
        evidence=_decode_json_list(row.get("evidence")),
        recorded_at=str(row["recorded_at"]),
    )


def _reaction_to_summary(row: dict) -> ReactionSummary:
    return ReactionSummary(
        status=row["status"],
        session_id=row.get("session_id"),
        note=row.get("note"),
        recorded_at=str(row["recorded_at"]),
    )


def _contract_to_entry(row: dict) -> ContractEntry:
    return ContractEntry(
        event_type=row["event_type"],
        publisher=row["publisher"],
        schema_version=row["schema_version"],
        summary=row["summary"],
        retention_policy=row["retention_policy"],
        reaction_expectation=row["reaction_expectation"],
        reaction_contract=row["reaction_contract"],
        permitted_subscribers=_decode_json_list(row.get("permitted_subscribers")),
        required_fields=_decode_json_list(row.get("required_fields")),
        optional_fields=_decode_json_list(row.get("optional_fields")),
        materialized_at=str(row["materialized_at"]),
    )


def _delivery_to_entry(row: dict, reaction: dict | None = None) -> DeliveryEntry:
    return DeliveryEntry(
        id=str(row["id"]),
        event_id=str(row["event_id"]),
        subscriber_butler=row["subscriber_butler"],
        status=row["status"],
        task_id=str(row["task_id"]) if row.get("task_id") else None,
        task_name=row.get("task_name"),
        error_message=row.get("error_message"),
        attempt_count=row.get("attempt_count") or 0,
        delivered_at=str(row["delivered_at"]) if row.get("delivered_at") else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        event_type=row["event_type"],
        source_butler=row["source_butler"],
        occurred_at=str(row["occurred_at"]),
        reaction=_reaction_to_summary(reaction) if reaction is not None else None,
    )


@router.get("/subscriptions", response_model=ApiResponse[list[SubscriptionEntry]])
async def list_domain_event_subscriptions(
    subscriber_butler: str | None = Query(None, description="Filter by the subscribing butler."),
    event_type: str | None = Query(None, description="Filter by event type."),
    active_only: bool = Query(False, description="When true, only return active subscriptions."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[SubscriptionEntry]]:
    """List standing ``(subscriber_butler, event_type)`` subscriptions.

    Unbounded (no pagination): the subscription table is bounded by
    butler-count x event-type-count, never approaching list-endpoint scale.
    """
    pool = _any_pool(db)
    rows = await list_subscriptions(
        pool,
        subscriber_butler=subscriber_butler,
        event_type=event_type,
        active_only=active_only,
    )
    return ApiResponse[list[SubscriptionEntry]](data=[_subscription_to_entry(r) for r in rows])


@router.get("/deliveries", response_model=PaginatedResponse[DeliveryEntry])
async def list_domain_event_deliveries(
    subscriber_butler: str | None = Query(None, description="Filter by the fanned-out-to butler."),
    source_butler: str | None = Query(None, description="Filter by the publishing butler."),
    status: str | None = Query(
        None, description=f"Filter by status. One of: {', '.join(sorted(VALID_DELIVERY_STATUSES))}."
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[DeliveryEntry]:
    """List fan-out deliveries joined with their event, most-recent first."""
    if status is not None and status not in VALID_DELIVERY_STATUSES:
        allowed = ", ".join(sorted(VALID_DELIVERY_STATUSES))
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}. Must be one of: {allowed}",
        )

    pool = _any_pool(db)
    total, rows = await list_recent_deliveries(
        pool,
        subscriber_butler=subscriber_butler,
        source_butler=source_butler,
        status=status,
        offset=offset,
        limit=limit,
    )
    # One batched lookup for the page, not one per row: the outcome is a
    # separate ledger, so it is a separate read rather than a join that would
    # let a missing receipt masquerade as a missing delivery.
    reactions = await latest_reactions_for_events(pool, [r["event_id"] for r in rows])
    return PaginatedResponse[DeliveryEntry](
        data=[
            _delivery_to_entry(r, reactions.get((str(r["event_id"]), str(r["subscriber_butler"]))))
            for r in rows
        ],
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/events/{event_id}/reactions", response_model=ApiResponse[list[ReactionEntry]])
async def list_domain_event_reactions(
    event_id: str = PathParam(..., description="The public.domain_events row id."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ReactionEntry]]:
    """Return the full collaboration trace for one event, oldest step first.

    Every step every subscriber recorded -- ``scheduled``, ``running``, and
    the terminal outcome. Not paginated: one event fans out to at most the
    butler count, and each wake contributes a handful of rows.
    """
    try:
        uuid.UUID(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid event id {event_id!r}") from exc

    pool = _any_pool(db)
    rows = await list_reactions_for_event(pool, event_id=event_id)
    return ApiResponse[list[ReactionEntry]](data=[_reaction_to_entry(r) for r in rows])


@router.get("/contracts", response_model=ApiResponse[list[ContractEntry]])
async def list_domain_event_contracts(
    publisher: str | None = Query(None, description="Filter by the declaring butler."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ContractEntry]]:
    """List the materialized publisher-owned contracts.

    A read surface only. The declarations in ``roster/<butler>/
    domain_events.toml`` are the source of truth and are what admission
    checks; this table is each butler's published copy of its own, refreshed
    at startup.
    """
    pool = _any_pool(db)
    if publisher is not None:
        rows = await pool.fetch(
            """
            SELECT event_type, publisher, schema_version, summary, retention_policy,
                   reaction_expectation, reaction_contract, permitted_subscribers,
                   required_fields, optional_fields, materialized_at
            FROM public.domain_event_contracts
            WHERE publisher = $1
            ORDER BY event_type
            """,
            publisher,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT event_type, publisher, schema_version, summary, retention_policy,
                   reaction_expectation, reaction_contract, permitted_subscribers,
                   required_fields, optional_fields, materialized_at
            FROM public.domain_event_contracts
            ORDER BY publisher, event_type
            """
        )
    return ApiResponse[list[ContractEntry]](data=[_contract_to_entry(dict(r)) for r in rows])
