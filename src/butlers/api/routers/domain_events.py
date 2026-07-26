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

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.domain_events import DeliveryEntry, SubscriptionEntry
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


def _delivery_to_entry(row: dict) -> DeliveryEntry:
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
    return PaginatedResponse[DeliveryEntry](
        data=[_delivery_to_entry(r) for r in rows],
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
