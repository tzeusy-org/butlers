"""Calendar workspace read/meta/sync endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse
from butlers.api.models.calendar import (
    CalendarWorkspaceButlerMutationRequest,
    CalendarWorkspaceUserMutationRequest,
)
from butlers.api.models.calendar_workspace import (
    CalendarWorkspaceLaneDefinition,
    CalendarWorkspaceMetaResponse,
    CalendarWorkspaceReadResponse,
    CalendarWorkspaceSourceFreshness,
    CalendarWorkspaceSyncRequest,
    CalendarWorkspaceSyncResponse,
    CalendarWorkspaceSyncTarget,
    CalendarWorkspaceWritableCalendar,
    UnifiedCalendarEntry,
)
from butlers.api.routers.audit import log_audit_entry

router = APIRouter(prefix="/api/calendar/workspace", tags=["calendar", "workspace"])
logger = logging.getLogger(__name__)

_WORKSPACE_STALE_THRESHOLD = timedelta(minutes=10)
_WORKSPACE_MAX_RANGE = timedelta(days=90)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _normalize_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _safe_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return UUID(normalized)
        except ValueError:
            return None
    return None


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _entry_status(raw_status: object, source_type: str, event_metadata: dict[str, Any]) -> str:
    status = str(raw_status or "").strip().lower()
    if source_type == "scheduled_task":
        enabled = event_metadata.get("enabled")
        if enabled is False:
            return "paused"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"error", "failed"}:
        return "error"
    if status in {"completed", "done"}:
        return "completed"
    return "active"


def _source_type(source_kind: object, event_metadata: dict[str, Any]) -> str:
    metadata_type = str(event_metadata.get("source_type") or "").strip().lower()
    kind = str(source_kind or "").strip().lower()

    if metadata_type in {
        "provider_event",
        "scheduled_task",
        "butler_reminder",
        "manual_butler_event",
    }:
        return metadata_type
    if kind == "provider_event":
        return "provider_event"
    if kind == "internal_scheduler":
        return "scheduled_task"
    if kind == "internal_reminders":
        return "butler_reminder"
    return "manual_butler_event"


def _sync_state(
    *,
    last_synced_at: datetime | None,
    last_success_at: datetime | None,
    last_error_at: datetime | None,
    last_error: str | None,
    full_sync_required: bool,
) -> str:
    if last_error and (
        last_success_at is None or (last_error_at and last_error_at >= last_success_at)
    ):
        return "failed"
    if full_sync_required:
        return "stale"
    if last_synced_at is None:
        return "stale"
    if datetime.now(UTC) - last_synced_at > _WORKSPACE_STALE_THRESHOLD:
        return "stale"
    return "fresh"


def _to_source_freshness(row: Mapping[str, Any]) -> CalendarWorkspaceSourceFreshness:
    last_synced_at = _coerce_datetime(row.get("last_synced_at"))
    last_success_at = _coerce_datetime(row.get("last_success_at"))
    last_error_at = _coerce_datetime(row.get("last_error_at"))
    full_sync_required = bool(row.get("full_sync_required") or False)
    now = datetime.now(UTC)
    staleness_ms = None
    if last_synced_at is not None:
        staleness_ms = max(int((now - last_synced_at).total_seconds() * 1000), 0)

    sync_state = _sync_state(
        last_synced_at=last_synced_at,
        last_success_at=last_success_at,
        last_error_at=last_error_at,
        last_error=row.get("last_error"),
        full_sync_required=full_sync_required,
    )

    return CalendarWorkspaceSourceFreshness(
        source_id=row["source_id"],
        source_key=row["source_key"],
        source_kind=row["source_kind"],
        lane=row["lane"],
        provider=row.get("provider"),
        calendar_id=row.get("calendar_id"),
        butler_name=row.get("butler_name"),
        display_name=row.get("display_name"),
        writable=bool(row.get("writable") or False),
        metadata=_normalize_json_object(row.get("source_metadata")),
        cursor_name=row.get("cursor_name"),
        last_synced_at=last_synced_at,
        last_success_at=last_success_at,
        last_error_at=last_error_at,
        last_error=row.get("last_error"),
        full_sync_required=full_sync_required,
        sync_state=sync_state,
        staleness_ms=staleness_ms,
    )


def _extract_mcp_result_text(result: object) -> str | None:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        return "\n".join(text_parts) if text_parts else None
    if isinstance(result, list):
        text_parts = []
        for block in result:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        return "\n".join(text_parts) if text_parts else None
    return None


def _parse_mcp_payload(raw_text: str | None) -> object:
    if raw_text is None:
        return None
    try:
        return json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        return raw_text


def _sync_detail(parsed: object) -> str | None:
    if parsed is None:
        return None
    if isinstance(parsed, dict | list):
        return json.dumps(parsed)
    return str(parsed)


def _build_lane_definitions(
    connected_sources: list[CalendarWorkspaceSourceFreshness],
) -> list[CalendarWorkspaceLaneDefinition]:
    by_butler: dict[str, list[str]] = defaultdict(list)
    for source in connected_sources:
        if source.lane != "butler":
            continue
        butler_name = source.butler_name
        if not butler_name:
            continue
        by_butler[butler_name].append(source.source_key)

    lanes: list[CalendarWorkspaceLaneDefinition] = []
    for butler_name in sorted(by_butler):
        lanes.append(
            CalendarWorkspaceLaneDefinition(
                lane_id=butler_name,
                butler_name=butler_name,
                title=butler_name.replace("_", " ").title(),
                source_keys=sorted(set(by_butler[butler_name])),
            )
        )
    return lanes


async def _fetch_sources(
    db: DatabaseManager,
    *,
    lane: str | None = None,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if lane is not None:
        conditions.append(f"s.lane = ${idx}")
        args.append(lane)
        idx += 1
    if butlers:
        conditions.append(f"COALESCE(s.butler_name, '') = ANY(${idx}::text[])")
        args.append(butlers)
        idx += 1
    if sources:
        conditions.append(f"s.source_key = ANY(${idx}::text[])")
        args.append(sources)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT
            s.id AS source_id,
            s.source_key,
            s.source_kind,
            s.lane,
            s.provider,
            s.calendar_id,
            s.butler_name,
            s.display_name,
            s.writable,
            s.metadata AS source_metadata,
            c.cursor_name,
            c.last_synced_at,
            c.last_success_at,
            c.last_error_at,
            c.last_error,
            c.full_sync_required
        FROM calendar_sources AS s
        LEFT JOIN LATERAL (
            SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                   full_sync_required, updated_at
            FROM calendar_sync_cursors
            WHERE source_id = s.id
            ORDER BY updated_at DESC
            LIMIT 1
        ) AS c ON TRUE
        {where}
        ORDER BY s.lane, s.source_kind, s.source_key
    """

    query_targets = sorted(set(butlers)) if butlers else None
    results = await db.fan_out(query, tuple(args), butler_names=query_targets)
    flattened: list[dict[str, Any]] = []
    for butler_name, rows in results.items():
        for row in rows:
            payload = dict(row)
            payload.setdefault(
                "butler_name", butler_name if payload.get("lane") == "butler" else None
            )
            payload["db_butler"] = butler_name
            flattened.append(payload)
    return flattened


async def _fetch_workspace_rows(
    db: DatabaseManager,
    *,
    view: str,
    start: datetime,
    end: datetime,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = [
        "s.lane = $1",
        "i.starts_at < $2",
        "i.ends_at > $3",
    ]
    args: list[Any] = [view, end, start]
    idx = 4

    if butlers:
        conditions.append(
            f"COALESCE(s.butler_name, e.metadata->>'butler_name', '') = ANY(${idx}::text[])"
        )
        args.append(butlers)
        idx += 1
    if sources:
        conditions.append(f"s.source_key = ANY(${idx}::text[])")
        args.append(sources)
        idx += 1

    where = " AND ".join(conditions)
    query = f"""
        SELECT
            i.id AS instance_id,
            i.origin_instance_ref,
            i.timezone AS instance_timezone,
            i.starts_at AS instance_starts_at,
            i.ends_at AS instance_ends_at,
            i.status AS instance_status,
            i.metadata AS instance_metadata,
            e.id AS event_id,
            e.origin_ref,
            e.title,
            e.description,
            e.location,
            e.timezone AS event_timezone,
            e.all_day,
            e.status AS event_status,
            e.visibility,
            e.recurrence_rule,
            e.metadata AS event_metadata,
            s.id AS source_id,
            s.source_key,
            s.source_kind,
            s.lane,
            s.provider,
            s.calendar_id,
            s.butler_name,
            s.display_name,
            s.writable,
            s.metadata AS source_metadata,
            c.cursor_name,
            c.last_synced_at,
            c.last_success_at,
            c.last_error_at,
            c.last_error,
            c.full_sync_required
        FROM calendar_event_instances AS i
        JOIN calendar_events AS e ON e.id = i.event_id
        JOIN calendar_sources AS s ON s.id = i.source_id
        LEFT JOIN LATERAL (
            SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                   full_sync_required, updated_at
            FROM calendar_sync_cursors
            WHERE source_id = s.id
            ORDER BY updated_at DESC
            LIMIT 1
        ) AS c ON TRUE
        WHERE {where}
        ORDER BY i.starts_at ASC, i.id ASC
    """

    query_targets = sorted(set(butlers)) if butlers else None
    results = await db.fan_out(query, tuple(args), butler_names=query_targets)
    flattened: list[dict[str, Any]] = []
    for butler_name, rows in results.items():
        for row in rows:
            payload = dict(row)
            payload["db_butler"] = butler_name
            flattened.append(payload)
    return flattened


def _normalize_entry(
    row: Mapping[str, Any],
    *,
    view: str,
    display_tz: ZoneInfo | None,
) -> UnifiedCalendarEntry:
    event_metadata = _normalize_json_object(row.get("event_metadata"))
    instance_metadata = _normalize_json_object(row.get("instance_metadata"))
    source_metadata = _normalize_json_object(row.get("source_metadata"))

    source_type = _source_type(row.get("source_kind"), event_metadata)
    start_at = _coerce_datetime(row.get("instance_starts_at")) or _coerce_datetime(
        row.get("event_starts_at")
    )
    end_at = _coerce_datetime(row.get("instance_ends_at")) or _coerce_datetime(
        row.get("event_ends_at")
    )
    if start_at is None or end_at is None:
        raise ValueError("workspace row missing start/end timestamps")

    if display_tz is not None:
        start_at = start_at.astimezone(display_tz)
        end_at = end_at.astimezone(display_tz)
        timezone_name = display_tz.key
    else:
        timezone_name = str(row.get("instance_timezone") or row.get("event_timezone") or "UTC")

    origin_ref = str(row.get("origin_ref") or "")
    butler_name = row.get("butler_name") or event_metadata.get("butler_name")

    schedule_id = _safe_uuid(origin_ref) if source_type == "scheduled_task" else None
    reminder_id = _safe_uuid(origin_ref) if source_type == "butler_reminder" else None
    provider_event_id = origin_ref if source_type == "provider_event" else None

    until_at = _coerce_datetime(event_metadata.get("until_at"))
    sync_state = _sync_state(
        last_synced_at=_coerce_datetime(row.get("last_synced_at")),
        last_success_at=_coerce_datetime(row.get("last_success_at")),
        last_error_at=_coerce_datetime(row.get("last_error_at")),
        last_error=row.get("last_error"),
        full_sync_required=bool(row.get("full_sync_required") or False),
    )

    metadata = {
        "source_kind": row.get("source_kind"),
        "provider": row.get("provider"),
        "display_name": row.get("display_name"),
        "origin_ref": origin_ref or None,
        "origin_instance_ref": row.get("origin_instance_ref"),
        "description": row.get("description"),
        "location": row.get("location"),
        "visibility": row.get("visibility"),
        "source_metadata": source_metadata,
        "event_metadata": event_metadata,
        "instance_metadata": instance_metadata,
    }

    cron_value = event_metadata.get("cron")
    if not isinstance(cron_value, str):
        cron_value = None

    return UnifiedCalendarEntry(
        entry_id=row["instance_id"],
        view=view,
        source_type=source_type,
        source_key=row["source_key"],
        title=str(row.get("title") or "Untitled"),
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
        all_day=bool(row.get("all_day") or False),
        calendar_id=row.get("calendar_id"),
        provider_event_id=provider_event_id,
        butler_name=butler_name,
        schedule_id=schedule_id,
        reminder_id=reminder_id,
        rrule=row.get("recurrence_rule"),
        cron=cron_value,
        until_at=until_at,
        status=_entry_status(
            row.get("instance_status") or row.get("event_status"), source_type, event_metadata
        ),
        sync_state=sync_state,
        editable=bool(row.get("writable") or False),
        metadata=metadata,
    )


@router.get("", response_model=ApiResponse[CalendarWorkspaceReadResponse])
async def get_workspace(
    view: str = Query(..., pattern="^(user|butler)$"),
    start: datetime = Query(..., description="Inclusive ISO-8601 range start"),
    end: datetime = Query(..., description="Exclusive ISO-8601 range end"),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butlers: list[str] | None = Query(None, description="Optional butler-name filters"),
    sources: list[str] | None = Query(None, description="Optional source_key filters"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceReadResponse]:
    """Return normalized workspace entries for the requested time range."""
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if end - start > _WORKSPACE_MAX_RANGE:
        raise HTTPException(status_code=400, detail="Requested range exceeds 90 days")

    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    workspace_rows = await _fetch_workspace_rows(
        db,
        view=view,
        start=start,
        end=end,
        butlers=butlers,
        sources=sources,
    )
    source_rows = await _fetch_sources(
        db,
        lane=view,
        butlers=butlers,
        sources=sources,
    )
    if not source_rows and workspace_rows:
        deduped: dict[UUID, dict[str, Any]] = {}
        for row in workspace_rows:
            source_id = row.get("source_id")
            if not isinstance(source_id, UUID):
                continue
            deduped[source_id] = {
                "source_id": source_id,
                "source_key": row.get("source_key"),
                "source_kind": row.get("source_kind"),
                "lane": row.get("lane"),
                "provider": row.get("provider"),
                "calendar_id": row.get("calendar_id"),
                "butler_name": row.get("butler_name"),
                "display_name": row.get("display_name"),
                "writable": row.get("writable"),
                "source_metadata": row.get("source_metadata"),
                "cursor_name": row.get("cursor_name"),
                "last_synced_at": row.get("last_synced_at"),
                "last_success_at": row.get("last_success_at"),
                "last_error_at": row.get("last_error_at"),
                "last_error": row.get("last_error"),
                "full_sync_required": row.get("full_sync_required"),
            }
        source_rows = list(deduped.values())

    source_freshness = [_to_source_freshness(row) for row in source_rows]
    entries: list[UnifiedCalendarEntry] = []
    for row in workspace_rows:
        try:
            entries.append(_normalize_entry(row, view=view, display_tz=display_tz))
        except ValueError:
            continue

    data = CalendarWorkspaceReadResponse(
        entries=entries,
        source_freshness=source_freshness,
        lanes=_build_lane_definitions(source_freshness),
    )
    return ApiResponse[CalendarWorkspaceReadResponse](data=data)


@router.get("/meta", response_model=ApiResponse[CalendarWorkspaceMetaResponse])
async def get_workspace_meta(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceMetaResponse]:
    """Return workspace metadata: capabilities, sources, lanes, writable calendars."""
    source_rows = await _fetch_sources(db)
    connected_sources = [_to_source_freshness(row) for row in source_rows]

    writable_calendars: list[CalendarWorkspaceWritableCalendar] = []
    for source in connected_sources:
        if source.lane != "user" or not source.writable or not source.calendar_id:
            continue
        writable_calendars.append(
            CalendarWorkspaceWritableCalendar(
                source_key=source.source_key,
                provider=source.provider,
                calendar_id=source.calendar_id,
                display_name=source.display_name,
                butler_name=source.butler_name,
            )
        )

    # Dedup sources by source_key — fan_out across butler schemas can return
    # the same provider source from multiple schemas.
    seen_keys: set[str] = set()
    deduped_sources: list[CalendarWorkspaceSourceFreshness] = []
    for source in connected_sources:
        if source.source_key in seen_keys:
            continue
        seen_keys.add(source.source_key)
        deduped_sources.append(source)

    # Rebuild writable calendars from deduped list.
    writable_calendars = []
    for source in deduped_sources:
        if source.lane != "user" or not source.writable or not source.calendar_id:
            continue
        writable_calendars.append(
            CalendarWorkspaceWritableCalendar(
                source_key=source.source_key,
                provider=source.provider,
                calendar_id=source.calendar_id,
                display_name=source.display_name,
                butler_name=source.butler_name,
            )
        )

    data = CalendarWorkspaceMetaResponse(
        connected_sources=deduped_sources,
        writable_calendars=writable_calendars,
        lane_definitions=_build_lane_definitions(deduped_sources),
        default_timezone="Asia/Singapore",
    )
    return ApiResponse[CalendarWorkspaceMetaResponse](data=data)


@router.post("/sync", response_model=ApiResponse[CalendarWorkspaceSyncResponse])
async def sync_workspace(
    request: CalendarWorkspaceSyncRequest,
    db: DatabaseManager = Depends(_get_db_manager),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[CalendarWorkspaceSyncResponse]:
    """Trigger projection/provider sync for all sources or one selected source."""
    target_rows: list[dict[str, Any]] = []
    scope = "all"
    if request.source_key is not None or request.source_id is not None:
        scope = "source"
        source_rows = await _fetch_sources(
            db,
            butlers=[request.butler] if request.butler else None,
            sources=[request.source_key] if request.source_key else None,
        )
        if request.source_id is not None:
            source_rows = [row for row in source_rows if row.get("source_id") == request.source_id]
        if not source_rows:
            raise HTTPException(status_code=404, detail="Requested source was not found")
        target_rows = source_rows
    else:
        if request.butler:
            if request.butler not in db.butler_names:
                raise HTTPException(status_code=404, detail=f"Unknown butler: {request.butler}")
            target_rows = [{"db_butler": request.butler}]
        else:
            target_rows = [{"db_butler": name} for name in db.butler_names]

    async def _sync_target(
        *,
        butler_name: str,
        call_args: dict[str, Any],
        source_key: str | None = None,
        calendar_id: str | None = None,
    ) -> CalendarWorkspaceSyncTarget:
        try:
            client = await mcp_manager.get_client(butler_name)
            result = await client.call_tool("calendar_force_sync", call_args)
            parsed = _parse_mcp_payload(_extract_mcp_result_text(result))
            status = (
                parsed.get("status", "sync_triggered")
                if isinstance(parsed, dict)
                else "sync_triggered"
            )
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status=status,
                detail=_sync_detail(parsed),
            )
        except ButlerUnreachableError as exc:
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status="failed",
                error=str(exc),
            )

    targets: list[CalendarWorkspaceSyncTarget]
    if scope == "all":
        targets = list(
            await asyncio.gather(
                *[
                    _sync_target(butler_name=str(target["db_butler"]), call_args={})
                    for target in target_rows
                ]
            )
        )
    else:
        targets = list(
            await asyncio.gather(
                *[
                    _sync_target(
                        butler_name=str(source["db_butler"]),
                        call_args=(
                            {"calendar_id": source["calendar_id"]}
                            if source.get("source_kind") == "provider_event"
                            and source.get("calendar_id")
                            else {}
                        ),
                        source_key=source.get("source_key"),
                        calendar_id=source.get("calendar_id"),
                    )
                    for source in target_rows
                ]
            )
        )

    data = CalendarWorkspaceSyncResponse(
        scope=scope,
        requested_source_key=request.source_key,
        requested_source_id=request.source_id,
        targets=targets,
        triggered_count=sum(1 for target in targets if target.status != "failed"),
    )
    return ApiResponse[CalendarWorkspaceSyncResponse](data=data)


async def _call_mcp_tool(
    mgr: MCPClientManager,
    butler_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call a butler MCP tool and coerce response content into a dict payload."""
    try:
        client = await mgr.get_client(butler_name)
        result = await client.call_tool(tool_name, arguments)
    except ButlerUnreachableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{butler_name}' is unreachable: {exc}",
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive transport fallback
        logger.exception(
            "Unexpected MCP call failure for butler '%s' tool '%s'",
            butler_name,
            tool_name,
        )
        raise HTTPException(
            status_code=503,
            detail=f"MCP call to '{butler_name}' failed: {exc}",
        ) from exc

    parsed = _parse_mcp_payload(_extract_mcp_result_text(result))
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed if parsed is not None else str(result)}


def _projection_meta(
    projection_freshness: dict[str, Any] | None,
) -> tuple[str | None, int | None]:
    if not isinstance(projection_freshness, dict):
        return None, None
    projection_version = projection_freshness.get("last_refreshed_at")
    staleness_ms = projection_freshness.get("staleness_ms")
    return (
        str(projection_version) if isinstance(projection_version, str) else None,
        int(staleness_ms) if isinstance(staleness_ms, int) else None,
    )


async def _projection_freshness_after_mutation(
    *,
    mgr: MCPClientManager,
    butler_name: str,
    mutation_result: dict[str, Any],
    calendar_id: str | None = None,
) -> dict[str, Any] | None:
    existing = mutation_result.get("projection_freshness")
    if isinstance(existing, dict):
        return existing

    status_args: dict[str, Any] = {}
    if isinstance(calendar_id, str) and calendar_id.strip():
        status_args["calendar_id"] = calendar_id.strip()

    try:
        status = await _call_mcp_tool(mgr, butler_name, "calendar_sync_status", status_args)
    except HTTPException as exc:
        logger.warning(
            "Unable to fetch projection freshness for butler '%s': %s",
            butler_name,
            exc.detail,
            exc_info=True,
        )
        return None
    freshness = status.get("projection_freshness")
    return freshness if isinstance(freshness, dict) else None


@router.post("/user-events", response_model=ApiResponse[dict[str, Any]])
async def mutate_user_event(
    body: CalendarWorkspaceUserMutationRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict[str, Any]]:
    """Create/update/delete user-view provider events through calendar MCP tools."""
    tool_name = {
        "create": "calendar_create_event",
        "update": "calendar_update_event",
        "delete": "calendar_delete_event",
    }[body.action]

    arguments = dict(body.payload)
    if body.request_id is not None:
        arguments["request_id"] = body.request_id

    summary = {
        "action": body.action,
        "tool_name": tool_name,
        "request_id": body.request_id,
    }
    try:
        mutation_result = await _call_mcp_tool(mgr, body.butler_name, tool_name, arguments)
        freshness = await _projection_freshness_after_mutation(
            mgr=mgr,
            butler_name=body.butler_name,
            mutation_result=mutation_result,
            calendar_id=arguments.get("calendar_id")
            if isinstance(arguments.get("calendar_id"), str)
            else None,
        )
        projection_version, staleness_ms = _projection_meta(freshness)
        response_payload = {
            "action": body.action,
            "tool_name": tool_name,
            "request_id": body.request_id,
            "result": mutation_result,
            "projection_version": projection_version,
            "staleness_ms": staleness_ms,
            "projection_freshness": freshness,
        }
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.user_events.mutate",
            summary,
        )
        return ApiResponse[dict[str, Any]](data=response_payload)
    except HTTPException:
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.user_events.mutate",
            summary,
            result="error",
            error="MCP call failed",
        )
        raise


@router.post("/butler-events", response_model=ApiResponse[dict[str, Any]])
async def mutate_butler_event(
    body: CalendarWorkspaceButlerMutationRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict[str, Any]]:
    """Create/update/delete/toggle butler-view events through calendar MCP tools."""
    tool_name = {
        "create": "calendar_create_butler_event",
        "update": "calendar_update_butler_event",
        "delete": "calendar_delete_butler_event",
        "toggle": "calendar_toggle_butler_event",
    }[body.action]

    arguments = dict(body.payload)
    if body.action == "create":
        arguments.setdefault("butler_name", body.butler_name)
    if body.request_id is not None:
        arguments["request_id"] = body.request_id

    summary = {
        "action": body.action,
        "tool_name": tool_name,
        "request_id": body.request_id,
    }
    try:
        mutation_result = await _call_mcp_tool(mgr, body.butler_name, tool_name, arguments)
        freshness = await _projection_freshness_after_mutation(
            mgr=mgr,
            butler_name=body.butler_name,
            mutation_result=mutation_result,
        )
        projection_version, staleness_ms = _projection_meta(freshness)
        response_payload = {
            "action": body.action,
            "tool_name": tool_name,
            "request_id": body.request_id,
            "result": mutation_result,
            "projection_version": projection_version,
            "staleness_ms": staleness_ms,
            "projection_freshness": freshness,
        }
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.butler_events.mutate",
            summary,
        )
        return ApiResponse[dict[str, Any]](data=response_payload)
    except HTTPException:
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.butler_events.mutate",
            summary,
            result="error",
            error="MCP call failed",
        )
        raise
