"""Calendar workspace read/meta/sync endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
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
    CalendarAuditEntry,
    CalendarAuditResponse,
    CalendarConflictEntry,
    CalendarSuggestedSlot,
    CalendarWorkspaceLaneDefinition,
    CalendarWorkspaceMetaResponse,
    CalendarWorkspaceMutationResponse,
    CalendarWorkspaceReadResponse,
    CalendarWorkspaceSourceFreshness,
    CalendarWorkspaceSyncRequest,
    CalendarWorkspaceSyncResponse,
    CalendarWorkspaceSyncTarget,
    CalendarWorkspaceWritableCalendar,
    SetPrimaryCalendarRequest,
    SetPrimaryCalendarResponse,
    UnifiedCalendarEntry,
)
from butlers.api.read_models.calendar_workspace_v1 import (
    query_calendar_sources,
    query_calendar_workspace,
)
from butlers.api.routers.audit import log_audit_entry

router = APIRouter(prefix="/api/calendar/workspace", tags=["calendar", "workspace"])
logger = logging.getLogger(__name__)

_WORKSPACE_STALE_THRESHOLD = timedelta(minutes=10)
_WORKSPACE_MAX_RANGE = timedelta(days=90)

# Regex for hashed Google Calendar IDs like "ae06dba...@group.calendar.google.com"
_GOOGLE_GROUP_CALENDAR_RE = re.compile(r"^[a-f0-9]{20,}@group\.calendar\.google\.com$", re.I)


def _titleize(value: str) -> str:
    """Titleize a raw identifier: replace separators, capitalize words."""
    result = value.replace("_", " ").replace("-", " ")
    if result == result.lower():
        result = result.title()
    return result


def _format_writable_calendar_label(
    *,
    butler_name: str | None,
    display_name: str | None,
    calendar_id: str | None,
    provider: str | None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Build a user-friendly label for a writable calendar source.

    Uses ``metadata["butler_specific"]`` to distinguish butler-owned calendars
    (configured via credential store) from the shared project calendar
    (auto-discovered "Butlers").

    Format:
      [Butler] Health          — butler-owned calendar, label is the butler name
      [Google] Butlers         — shared project calendar or generic Google calendar
    """
    is_butler_cal = bool((metadata or {}).get("butler_specific"))

    raw = display_name or calendar_id or "Calendar"
    # Truncate ugly hashed Google Calendar IDs.
    if _GOOGLE_GROUP_CALENDAR_RE.match(raw):
        raw = raw[:8] + "\u2026"
    formatted = _titleize(raw)

    if butler_name and is_butler_cal:
        label = _titleize(butler_name)
        return f"[Butler] {label}"

    if provider == "google":
        return f"[Google] {formatted}"
    return formatted


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


def _resolve_primary_calendar_id(
    sources: list[CalendarWorkspaceSourceFreshness],
) -> str | None:
    """Identify the primary provider calendar from connected sources.

    Google stamps the user's primary calendar with an ``id`` equal to the
    account email, and calendar discovery records that email in each source's
    ``metadata["account_email"]``. The primary calendar is therefore the
    user-lane source whose ``calendar_id`` matches its own ``account_email``.

    This is resolved from the DB-backed sources rather than a live MCP call,
    which can return null when the calendar module's in-memory primary has not
    been hydrated. Returns ``None`` when no primary can be identified.
    """
    for source in sources:
        if source.lane != "user" or not source.calendar_id:
            continue
        account_email = (source.metadata or {}).get("account_email")
        if isinstance(account_email, str) and account_email == source.calendar_id:
            return source.calendar_id
    return None


async def _fetch_sources(
    db: DatabaseManager,
    *,
    lane: str | None = None,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch calendar source rows via the versioned read-model boundary.

    Delegates to :func:`~butlers.api.read_models.calendar_workspace_v1.query_calendar_sources`
    and converts the typed :class:`~butlers.api.read_models.calendar_workspace_v1.CalendarSourceRow`
    DTOs back to plain dicts for the existing downstream helpers
    (``_to_source_freshness``, ``_normalize_entry``, etc.) that expect
    ``Mapping[str, Any]`` inputs.
    """
    import dataclasses

    source_rows = await query_calendar_sources(db, lane=lane, butlers=butlers, sources=sources)
    return [dataclasses.asdict(row) for row in source_rows]


async def _fetch_workspace_rows(
    db: DatabaseManager,
    *,
    view: str,
    start: datetime,
    end: datetime,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch calendar event-instance rows via the versioned read-model boundary.

    Delegates to
    :func:`~butlers.api.read_models.calendar_workspace_v1.query_calendar_workspace`
    and converts the typed
    :class:`~butlers.api.read_models.calendar_workspace_v1.CalendarWorkspaceRow`
    DTOs back to plain dicts for the existing downstream helpers
    (``_normalize_entry``, deduplication logic, etc.) that expect
    ``Mapping[str, Any]`` inputs.
    """
    import dataclasses

    workspace_dtos = await query_calendar_workspace(
        db, view=view, start=start, end=end, butlers=butlers, sources=sources
    )
    flattened: list[dict[str, Any]] = [dataclasses.asdict(dto) for dto in workspace_dtos]

    # Deduplicate across butler databases: the same Google Calendar event
    # is synced into every butler's projection tables.  Keep only one
    # instance per unique event.
    #
    # Pass 1 — exact event identity: key on (origin_ref, starts_epoch).
    # We deliberately exclude calendar_id because Google treats "primary"
    # and the explicit email address as aliases for the same calendar, so
    # the same event is returned under different calendar_id values.
    # Epoch-ms avoids timezone-serialization differences across DBs.
    #
    # Pass 2 — cross-calendar copies: events duplicated to a group
    # calendar get a new origin_ref from Google.  Collapse those via
    # (title, starts_epoch) so each real-world event shows once.
    seen_ref: set[tuple[str, int]] = set()
    after_pass1: list[dict[str, Any]] = []
    for row in flattened:
        origin_ref = row.get("origin_ref") or ""
        starts_at = _coerce_datetime(row.get("instance_starts_at"))
        starts_epoch = int(starts_at.timestamp() * 1000) if starts_at else 0
        key = (origin_ref, starts_epoch)
        if key in seen_ref:
            continue
        seen_ref.add(key)
        after_pass1.append(row)

    seen_title: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for row in after_pass1:
        title = (row.get("title") or "").strip().lower()
        starts_at = _coerce_datetime(row.get("instance_starts_at"))
        starts_epoch = int(starts_at.timestamp() * 1000) if starts_at else 0
        title_key = (title, starts_epoch)
        if title_key in seen_title:
            continue
        seen_title.add(title_key)
        deduped.append(row)
    return deduped


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
        source_butler=str(row.get("source_butler")) if row.get("source_butler") else None,
        source_session_id=(
            str(row.get("source_session_id")) if row.get("source_session_id") else None
        ),
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

    # Dedup sources by source_key — fan_out across butler schemas can return
    # the same provider source from multiple schemas.
    seen_source_keys: set[str] = set()
    deduped_source_rows: list[dict[str, Any]] = []
    for row in source_rows:
        sk = row.get("source_key")
        if sk in seen_source_keys:
            continue
        seen_source_keys.add(sk)
        deduped_source_rows.append(row)
    source_freshness = [_to_source_freshness(row) for row in deduped_source_rows]
    entries: list[UnifiedCalendarEntry] = []
    for row in workspace_rows:
        try:
            entries.append(_normalize_entry(row, view=view, display_tz=display_tz))
        except ValueError:
            continue

    # Hide noisy events: if the same title appears >20 times on a single
    # day, suppress all instances of that title for that day.
    day_title_counts: Counter[tuple[str, str]] = Counter()
    for entry in entries:
        day_key = entry.start_at.date().isoformat()
        day_title_counts[(day_key, entry.title)] += 1

    noisy: set[tuple[str, str]] = {k for k, v in day_title_counts.items() if v > 20}
    if noisy:
        entries = [e for e in entries if (e.start_at.date().isoformat(), e.title) not in noisy]

    data = CalendarWorkspaceReadResponse(
        entries=entries,
        source_freshness=source_freshness,
        lanes=_build_lane_definitions(source_freshness),
    )
    return ApiResponse[CalendarWorkspaceReadResponse](data=data)


@router.get("/meta", response_model=ApiResponse[CalendarWorkspaceMetaResponse])
async def get_workspace_meta(
    db: DatabaseManager = Depends(_get_db_manager),
    mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[CalendarWorkspaceMetaResponse]:
    """Return workspace metadata: capabilities, sources, lanes, writable calendars."""
    source_rows = await _fetch_sources(db)
    connected_sources = [_to_source_freshness(row) for row in source_rows]

    # Dedup sources by source_key — fan_out across butler schemas can return
    # the same provider source from multiple schemas.
    seen_keys: set[str] = set()
    deduped_sources: list[CalendarWorkspaceSourceFreshness] = []
    for source in connected_sources:
        if source.source_key in seen_keys:
            continue
        seen_keys.add(source.source_key)
        deduped_sources.append(source)

    # Build writable calendars from the deduped list with formatted display
    # names. Only include *submittable* calendars — those that resolve to an
    # owning butler — so the create-event dropdown cannot offer a calendar that
    # fails at submit with "Could not resolve owning butler". For user-lane
    # provider calendars the owning butler is the schema the source lives in;
    # ``_fetch_sources`` backfills ``butler_name`` from that schema.
    writable_calendars: list[CalendarWorkspaceWritableCalendar] = []
    for source in deduped_sources:
        if source.lane != "user" or not source.writable or not source.calendar_id:
            continue
        if not source.butler_name:
            # Unsubmittable: no owning butler could be resolved.
            continue
        writable_calendars.append(
            CalendarWorkspaceWritableCalendar(
                source_key=source.source_key,
                provider=source.provider,
                calendar_id=source.calendar_id,
                display_name=_format_writable_calendar_label(
                    butler_name=source.butler_name,
                    display_name=source.display_name,
                    calendar_id=source.calendar_id,
                    provider=source.provider,
                    metadata=source.metadata,
                ),
                butler_name=source.butler_name,
            )
        )

    primary_calendar_id = _resolve_primary_calendar_id(deduped_sources)

    # Fall back to a live MCP lookup only when the DB sources do not identify a
    # primary (e.g. discovery has not yet stamped ``account_email`` metadata).
    if primary_calendar_id is None:
        calendar_butlers = db.butlers_with_module("calendar")
        if calendar_butlers:
            try:
                status = await _call_mcp_tool(mgr, calendar_butlers[0], "calendar_sync_status", {})
                primary_calendar_id = status.get("calendar_id")
            except Exception:
                logger.debug("Unable to resolve primary calendar ID from MCP", exc_info=True)

    data = CalendarWorkspaceMetaResponse(
        connected_sources=deduped_sources,
        writable_calendars=writable_calendars,
        lane_definitions=_build_lane_definitions(deduped_sources),
        default_timezone="Asia/Singapore",
        primary_calendar_id=primary_calendar_id,
    )
    return ApiResponse[CalendarWorkspaceMetaResponse](data=data)


@router.put("/primary", response_model=ApiResponse[SetPrimaryCalendarResponse])
async def set_primary_calendar(
    body: SetPrimaryCalendarRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SetPrimaryCalendarResponse]:
    """Set the primary calendar for a butler via the calendar_set_primary MCP tool."""
    result = await _call_mcp_tool(
        mgr, body.butler_name, "calendar_set_primary", {"calendar_id": body.calendar_id}
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    response = SetPrimaryCalendarResponse(
        old_calendar_id=result.get("old_calendar_id"),
        new_calendar_id=result.get("new_calendar_id", body.calendar_id),
        persisted=bool(result.get("persisted")),
    )
    await log_audit_entry(
        db,
        body.butler_name,
        "calendar.workspace.set_primary",
        {"old": response.old_calendar_id, "new": response.new_calendar_id},
    )
    return ApiResponse[SetPrimaryCalendarResponse](data=response)


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
        # Fetch all provider_event sources so we sync every registered calendar,
        # not just each butler's single default resolved calendar ID.
        source_rows = await _fetch_sources(
            db,
            butlers=[request.butler] if request.butler else None,
        )
        if request.butler and request.butler not in db.butler_names:
            raise HTTPException(status_code=404, detail=f"Unknown butler: {request.butler}")
        # Keep only provider_event sources with a calendar_id; deduplicate by
        # (butler, calendar_id) so we don't hit the same Google API twice.
        seen: set[tuple[str, str]] = set()
        for row in source_rows:
            if row.get("source_kind") != "provider_event" or not row.get("calendar_id"):
                continue
            key = (str(row["db_butler"]), str(row["calendar_id"]))
            if key in seen:
                continue
            seen.add(key)
            target_rows.append(row)

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
                    _sync_target(
                        butler_name=str(source["db_butler"]),
                        call_args=(
                            {"calendar_id": source["calendar_id"]}
                            if source.get("calendar_id")
                            else {}
                        ),
                        source_key=source.get("source_key"),
                        calendar_id=source.get("calendar_id"),
                    )
                    for source in target_rows
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


def _extract_conflicts(
    mutation_result: dict[str, Any],
) -> tuple[list[CalendarConflictEntry], list[CalendarSuggestedSlot]]:
    """Extract typed conflict and suggested-slot lists from a raw MCP mutation result.

    The calendar MCP tools embed ``conflicts`` and ``suggested_slots`` inside
    the returned dict when the conflict policy fires.  This helper coerces them
    into the typed Pydantic models so the API response carries first-class
    structured data instead of opaque dicts.
    """
    raw_conflicts = mutation_result.get("conflicts")
    raw_slots = mutation_result.get("suggested_slots")

    conflicts: list[CalendarConflictEntry] = []
    if isinstance(raw_conflicts, list):
        for entry in raw_conflicts:
            if not isinstance(entry, dict):
                continue
            try:
                conflicts.append(CalendarConflictEntry.model_validate(entry))
            except Exception:
                logger.debug("Skipping malformed conflict entry: %r", entry)

    suggested_slots: list[CalendarSuggestedSlot] = []
    if isinstance(raw_slots, list):
        for slot in raw_slots:
            if not isinstance(slot, dict):
                continue
            try:
                suggested_slots.append(CalendarSuggestedSlot.model_validate(slot))
            except Exception:
                logger.debug("Skipping malformed suggested slot: %r", slot)

    return conflicts, suggested_slots


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


@router.post("/user-events", response_model=ApiResponse[CalendarWorkspaceMutationResponse])
async def mutate_user_event(
    body: CalendarWorkspaceUserMutationRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceMutationResponse]:
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
        conflicts, suggested_slots = _extract_conflicts(mutation_result)
        response_payload = CalendarWorkspaceMutationResponse(
            action=body.action,
            tool_name=tool_name,
            request_id=body.request_id,
            result=mutation_result,
            conflicts=conflicts,
            suggested_slots=suggested_slots,
            projection_version=projection_version,
            staleness_ms=staleness_ms,
            projection_freshness=freshness,
        )
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.user_events.mutate",
            summary,
        )
        return ApiResponse[CalendarWorkspaceMutationResponse](data=response_payload)
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


@router.post("/butler-events", response_model=ApiResponse[CalendarWorkspaceMutationResponse])
async def mutate_butler_event(
    body: CalendarWorkspaceButlerMutationRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceMutationResponse]:
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
        conflicts, suggested_slots = _extract_conflicts(mutation_result)
        response_payload = CalendarWorkspaceMutationResponse(
            action=body.action,
            tool_name=tool_name,
            request_id=body.request_id,
            result=mutation_result,
            conflicts=conflicts,
            suggested_slots=suggested_slots,
            projection_version=projection_version,
            staleness_ms=staleness_ms,
            projection_freshness=freshness,
        )
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.butler_events.mutate",
            summary,
        )
        return ApiResponse[CalendarWorkspaceMutationResponse](data=response_payload)
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


# ---------------------------------------------------------------------------
# Audit trail read — GET /api/calendar/workspace/audit
# ---------------------------------------------------------------------------

_AUDIT_SQL = """
    SELECT
        cal.id,
        cal.idempotency_key,
        cal.request_id,
        cal.action_type,
        cal.action_status,
        cal.origin_ref,
        cal.action_payload,
        cal.error,
        cal.created_at,
        cal.updated_at,
        cal.applied_at,
        e.source_butler,
        e.source_session_id
    FROM calendar_action_log AS cal
    LEFT JOIN calendar_events AS e ON e.id = cal.event_id
    ORDER BY cal.created_at DESC
    LIMIT $1 OFFSET $2
"""

_AUDIT_COUNT_SQL = "SELECT count(*) FROM calendar_action_log"

_PAYLOAD_SUMMARY_KEYS = frozenset(
    {
        "title",
        "event_id",
        "start_at",
        "end_at",
        "timezone",
        "calendar_id",
        "action",
        "source_hint",
        "butler_name",
    }
)


def _extract_payload_summary(raw_payload: object) -> dict[str, Any]:
    """Return a condensed subset of the action_payload JSONB."""
    if not isinstance(raw_payload, dict):
        return {}
    return {k: v for k, v in raw_payload.items() if k in _PAYLOAD_SUMMARY_KEYS}


@router.get("/audit", response_model=ApiResponse[CalendarAuditResponse])
async def get_calendar_audit(
    limit: int = Query(50, ge=1, le=200, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Number of entries to skip"),
    butler: str | None = Query(None, description="Restrict to a single butler schema"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarAuditResponse]:
    """Return paginated calendar mutation audit log entries.

    Fans out across all calendar-enabled butler schemas and merges results,
    sorted newest first.  Each row comes from ``calendar_action_log`` and is
    enriched with ``source_butler`` / ``source_session_id`` from the linked
    ``calendar_events`` row (core_076 provenance columns).
    """
    query_targets: list[str] | None
    if butler:
        if butler not in db.butler_names:
            raise HTTPException(status_code=404, detail=f"Unknown butler: {butler}")
        query_targets = [butler]
    else:
        query_targets = db.butlers_with_module("calendar")

    # Fan out — gather raw rows from every calendar butler schema.
    results = await db.fan_out(_AUDIT_SQL, (limit + offset, 0), butler_names=query_targets)
    count_results = await db.fan_out(_AUDIT_COUNT_SQL, (), butler_names=query_targets)

    raw_rows: list[dict[str, Any]] = []
    for butler_rows in results.values():
        for row in butler_rows:
            payload = _normalize_json_object(row["action_payload"])
            raw_rows.append(
                {
                    "id": row["id"],
                    "idempotency_key": row["idempotency_key"],
                    "request_id": row["request_id"],
                    "action_type": row["action_type"],
                    "action_status": row["action_status"],
                    "origin_ref": row["origin_ref"],
                    "payload_summary": _extract_payload_summary(payload),
                    "error": row["error"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "applied_at": row["applied_at"],
                    "source_butler": row["source_butler"],
                    "source_session_id": row["source_session_id"],
                }
            )

    # Total across all schemas
    total = sum(int(rows[0]["count"]) if rows else 0 for rows in count_results.values())

    # Sort newest-first across all schemas, then slice the requested page.
    raw_rows.sort(key=lambda r: r["created_at"], reverse=True)
    page = raw_rows[offset : offset + limit]

    entries = [CalendarAuditEntry.model_validate(row) for row in page]
    data = CalendarAuditResponse(entries=entries, total=total, offset=offset, limit=limit)
    return ApiResponse[CalendarAuditResponse](data=data)
