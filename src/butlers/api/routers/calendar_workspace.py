"""Calendar workspace read/meta/sync endpoints."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
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
    CalendarAccountEntry,
    CalendarAccountHealth,
    CalendarAccountsResponse,
    CalendarAuditEntry,
    CalendarAuditResponse,
    CalendarConflictEntry,
    CalendarSourceToggleRequest,
    CalendarSourceToggleResponse,
    CalendarSuggestedSlot,
    CalendarUndoResponse,
    CalendarWorkspaceFindTimeRequest,
    CalendarWorkspaceFindTimeResponse,
    CalendarWorkspaceLaneDefinition,
    CalendarWorkspaceMetaResponse,
    CalendarWorkspaceMutationResponse,
    CalendarWorkspaceReadResponse,
    CalendarWorkspaceSearchResponse,
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
    CalendarOverlayRow,
    CalendarProposalRow,
    query_calendar_event_search,
    query_calendar_overlays,
    query_calendar_proposals,
    query_calendar_sources,
    query_calendar_workspace,
    query_calendar_workspace_entry,
)
from butlers.api.routers.audit import log_audit_entry
from butlers.google_account_registry import list_google_accounts
from butlers.modules.calendar import classify_sync_error_kind

router = APIRouter(prefix="/api/calendar/workspace", tags=["calendar", "workspace"])
logger = logging.getLogger(__name__)

_WORKSPACE_STALE_THRESHOLD = timedelta(minutes=10)
_WORKSPACE_MAX_RANGE = timedelta(days=90)

# Keyset pagination bounds for the workspace read.
_WORKSPACE_DEFAULT_LIMIT = 200
_WORKSPACE_MAX_LIMIT = 1000

# Allowed values for the server-side facets. ``status`` mirrors the computed
# values produced by ``_entry_status`` / ``STATUS_SQL``; ``source_type`` mirrors
# ``_source_type`` / ``SOURCE_TYPE_SQL``. Unknown values yield a 400.
_WORKSPACE_STATUS_FACETS = frozenset({"active", "paused", "cancelled", "error", "completed"})
_WORKSPACE_SOURCE_TYPE_FACETS = frozenset(
    {"provider_event", "scheduled_task", "butler_reminder", "manual_butler_event"}
)

# Overlay contributions carry SGT calendar dates (the contribution jobs bucket
# every domain event onto its Asia/Singapore date). When no display timezone is
# requested, overlay entries are anchored at SGT midnight so they land on the
# same calendar day the specialist computed.
_OVERLAY_DEFAULT_TZ = ZoneInfo("Asia/Singapore")
# Namespace for deterministic, stable overlay entry IDs. Overlay entries have no
# natural DB id (they live inside a JSONB envelope), so the entry_id is derived
# from (butler, date, index, kind, label) — stable across reads of the same view.
_OVERLAY_ENTRY_NAMESPACE = uuid5(NAMESPACE_URL, "butlers/calendar/overlay-contribution")


def _encode_workspace_cursor(starts_at: datetime, entry_id: UUID) -> str:
    """Encode a ``(starts_at, id)`` keyset position into an opaque cursor token."""
    payload = json.dumps({"s": starts_at.isoformat(), "i": str(entry_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_workspace_cursor(raw: str) -> tuple[datetime, UUID]:
    """Decode an opaque workspace cursor into ``(starts_at, id)``.

    Raises :class:`ValueError` on any malformed/unparseable token so the caller
    can surface a 400.
    """
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        payload = json.loads(decoded)
        starts_at = _coerce_datetime(payload["s"])
        entry_id = UUID(str(payload["i"]))
    except (
        KeyError,
        ValueError,
        TypeError,
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise ValueError(f"Malformed cursor: {raw!r}") from exc
    if starts_at is None:
        raise ValueError(f"Malformed cursor: {raw!r}")
    return starts_at, entry_id


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

    source_metadata = _normalize_json_object(row.get("source_metadata"))
    sync_enabled = source_metadata.get("sync_enabled") is not False

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
        error_kind=classify_sync_error_kind(row.get("last_error")),
        sync_enabled=sync_enabled,
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
    status: str | None = None,
    source_type: str | None = None,
    editable: bool | None = None,
    cursor: tuple[datetime, UUID] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch calendar event-instance rows via the versioned read-model boundary.

    Delegates to
    :func:`~butlers.api.read_models.calendar_workspace_v1.query_calendar_workspace`
    and converts the typed
    :class:`~butlers.api.read_models.calendar_workspace_v1.CalendarWorkspaceRow`
    DTOs back to plain dicts for the existing downstream helpers
    (``_normalize_entry``, deduplication logic, etc.) that expect
    ``Mapping[str, Any]`` inputs.

    The ``status`` / ``source_type`` / ``editable`` facets, the keyset
    ``cursor``, and ``limit`` are applied **server-side** in the fan-out query.
    Rows from every butler schema are merged and re-sorted by the global keyset
    order ``(starts_at, id)`` so dedup and cursor pagination are deterministic
    across schemas.
    """
    import dataclasses

    workspace_dtos = await query_calendar_workspace(
        db,
        view=view,
        start=start,
        end=end,
        butlers=butlers,
        sources=sources,
        status=status,
        source_type=source_type,
        editable=editable,
        cursor=cursor,
        limit=limit,
    )
    flattened: list[dict[str, Any]] = [dataclasses.asdict(dto) for dto in workspace_dtos]

    # Re-sort across schemas by the global keyset order so dedup keeps the
    # lowest-id copy deterministically and cursor pagination is stable.
    flattened.sort(key=lambda r: (r["instance_starts_at"], r["instance_id"]))

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


def _normalize_proposal_entry(
    proposal: CalendarProposalRow,
    *,
    display_tz: ZoneInfo | None,
) -> UnifiedCalendarEntry:
    """Project a pending ``calendar_event_proposals`` row into a unified entry.

    The entry is tagged ``source_type="proposed_event"`` and is non-editable in
    place (``editable=False``).  The proposal provenance — ``confidence``,
    ``source_snippet`` and the ``source_event_id`` link — is carried in
    ``metadata`` for the proposals-lane UX (confidence chip + provenance).
    """
    start_at = _coerce_datetime(proposal.start_at)
    end_at = _coerce_datetime(proposal.end_at)
    if start_at is None or end_at is None:
        raise ValueError("proposal row missing start/end timestamps")

    if display_tz is not None:
        start_at = start_at.astimezone(display_tz)
        end_at = end_at.astimezone(display_tz)
        timezone_name = display_tz.key
    else:
        timezone_name = str(proposal.timezone or "UTC")

    entity_ids = proposal.entity_ids
    entity_id_strs = [str(eid) for eid in entity_ids] if entity_ids else []

    metadata: dict[str, Any] = {
        "source_type": "proposed_event",
        "confidence": proposal.confidence,
        "source_snippet": proposal.source_snippet,
        "source_event_id": proposal.source_event_id,
        "entity_ids": entity_id_strs,
        "description": proposal.description,
        "location": proposal.location,
        "proposal_status": proposal.status,
    }

    return UnifiedCalendarEntry(
        entry_id=proposal.proposal_id,
        view="proposals",
        source_type="proposed_event",
        source_key="proposals",
        title=str(proposal.title or "Untitled"),
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
        all_day=False,
        butler_name=proposal.butler_name,
        status="active",
        editable=False,
        metadata=metadata,
        source_butler=proposal.butler_name,
    )


async def _fetch_proposal_entries(
    db: DatabaseManager,
    *,
    start: datetime,
    end: datetime,
    butlers: list[str] | None,
    display_tz: ZoneInfo | None,
) -> list[UnifiedCalendarEntry]:
    """Fetch + project pending proposals, failing open to an empty list.

    The read MUST NEVER surface an HTTP 500: a missing
    ``calendar_event_proposals`` table or any query failure degrades to an
    empty entries list (the failure is logged in the read-model layer).
    """
    try:
        proposals = await query_calendar_proposals(db, start=start, end=end, butlers=butlers)
    except Exception:
        logger.warning("proposals projection query failed; returning empty", exc_info=True)
        return []

    entries: list[UnifiedCalendarEntry] = []
    for proposal in proposals:
        try:
            entries.append(_normalize_proposal_entry(proposal, display_tz=display_tz))
        except ValueError:
            continue
    return entries


def _normalize_overlay_envelope(
    overlay: CalendarOverlayRow,
    *,
    start: datetime,
    end: datetime,
    display_tz: ZoneInfo | None,
) -> tuple[list[UnifiedCalendarEntry], bool] | None:
    """Project one overlay-contribution envelope into unified entries.

    Returns ``None`` when the envelope is malformed and must be skipped (the
    view's hardcoded ``butler`` column disagrees with the envelope's
    ``value->>'butler'``, or a required field — ``butler`` / ``date`` /
    ``has_entries`` — is missing). Otherwise returns ``(entries, in_range)`` for
    a valid envelope: ``in_range`` is whether the target date falls within
    ``[start, end)`` and ``entries`` is the (possibly empty) projection for this
    window — empty when out of range or when ``has_entries`` is false. A valid
    in-range envelope still signals ``has_domain_context`` even with no entries;
    the caller distinguishes the cases via ``in_range`` and the entry count.

    Computing range membership here (rather than re-parsing the date in a second
    pass) keeps the date/timezone logic in one place.

    Each entry in the envelope's ``entries`` array becomes a non-editable
    ``UnifiedCalendarEntry`` (``source_type="overlay_contribution"``) carrying
    ``kind`` / ``priority`` / ``source_butler`` / ``meta`` in ``metadata`` so the
    FE can render the domain ribbon/pill (amount badge, trip span, etc.).
    """
    source_butler = overlay.butler
    value = _normalize_json_object(overlay.value)

    envelope_butler = value.get("butler")
    date_str = value.get("date")
    has_entries = value.get("has_entries")

    # Required envelope fields and the butler-match guardrail (RFC 0010 #2): a
    # row whose payload ``butler`` disagrees with the view's hardcoded source
    # column is skipped (a contribution job wrote into the wrong schema).
    if envelope_butler is None or date_str is None or has_entries is None:
        logger.warning("overlay contribution missing required fields; skipping: %r", overlay.key)
        return None
    if source_butler is not None and envelope_butler != source_butler:
        logger.warning(
            "overlay contribution butler mismatch (column=%r payload=%r); skipping",
            source_butler,
            envelope_butler,
        )
        return None

    try:
        target_date = datetime.fromisoformat(str(date_str)).date()
    except ValueError:
        logger.warning("overlay contribution has unparseable date %r; skipping", date_str)
        return None

    tz = display_tz or _OVERLAY_DEFAULT_TZ
    start_at = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    end_at = start_at + timedelta(days=1)

    # Out-of-range envelopes contribute domain context (the specialist DID write
    # for nearby dates) but no entries for this window.
    in_range = start <= start_at < end
    if not in_range:
        return [], False

    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        return [], True

    entries: list[UnifiedCalendarEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            continue
        kind = raw_entry.get("kind")
        label = raw_entry.get("label")
        if not kind or not label:
            continue
        priority = raw_entry.get("priority")
        meta = raw_entry.get("meta") if isinstance(raw_entry.get("meta"), Mapping) else {}

        entry_id = uuid5(
            _OVERLAY_ENTRY_NAMESPACE,
            f"{envelope_butler}:{target_date.isoformat()}:{index}:{kind}:{label}",
        )
        entries.append(
            UnifiedCalendarEntry(
                entry_id=entry_id,
                view="overlays",
                source_type="overlay_contribution",
                source_key="overlays",
                title=str(label),
                start_at=start_at,
                end_at=end_at,
                timezone=tz.key,
                all_day=True,
                butler_name=envelope_butler,
                status="active",
                editable=False,
                metadata={
                    "source_type": "overlay_contribution",
                    "kind": kind,
                    "priority": priority,
                    "source_butler": envelope_butler,
                    "meta": dict(meta),
                    "date": target_date.isoformat(),
                },
                source_butler=envelope_butler,
            )
        )
    return entries, True


async def _fetch_overlay_entries(
    db: DatabaseManager,
    *,
    start: datetime,
    end: datetime,
    butlers: list[str] | None,
    display_tz: ZoneInfo | None,
) -> tuple[list[UnifiedCalendarEntry], bool]:
    """Fetch + project cached overlay contributions, failing open.

    Returns ``(entries, has_domain_context)``. ``has_domain_context`` is ``True``
    when at least one **valid** contribution envelope exists for a date within
    ``[start, end)`` — even when that envelope has no entries (``has_entries``
    false) — so the FE can distinguish "no domain context for this range" from
    "context temporarily unavailable". A missing view, a missing specialist
    ``state`` table, or any query failure yields ``([], False)`` — never a 500.
    """
    overlays = await query_calendar_overlays(db, butlers=butlers)

    entries: list[UnifiedCalendarEntry] = []
    has_domain_context = False
    for overlay in overlays:
        projected = _normalize_overlay_envelope(
            overlay, start=start, end=end, display_tz=display_tz
        )
        if projected is None:
            # Malformed envelope — skipped, contributes no domain context.
            continue
        projected_entries, in_range = projected
        if in_range:
            # A valid in-range envelope signals domain context for the range even
            # when it projects zero entries (has_entries=false → honest empty-state).
            has_domain_context = True
            entries.extend(projected_entries)
    return entries, has_domain_context


@router.get("", response_model=ApiResponse[CalendarWorkspaceReadResponse])
async def get_workspace(
    view: str = Query(..., pattern="^(user|butler|proposals|overlays)$"),
    start: datetime = Query(..., description="Inclusive ISO-8601 range start"),
    end: datetime = Query(..., description="Exclusive ISO-8601 range end"),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butlers: list[str] | None = Query(None, description="Optional butler-name filters"),
    sources: list[str] | None = Query(None, description="Optional source_key filters"),
    status: str | None = Query(None, description="Optional computed-status facet"),
    source_type: str | None = Query(None, description="Optional computed source_type facet"),
    editable: bool | None = Query(None, description="Optional writable-source facet"),
    limit: int = Query(
        _WORKSPACE_DEFAULT_LIMIT,
        ge=1,
        le=_WORKSPACE_MAX_LIMIT,
        description="Max entries per page (keyset pagination)",
    ),
    cursor: str | None = Query(None, description="Opaque keyset cursor from a prior page"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceReadResponse]:
    """Return normalized workspace entries for the requested time range.

    Supports optional server-side ``status`` / ``source_type`` / ``editable``
    facets (combined with AND; omitting one leaves that dimension unfiltered)
    and keyset (cursor) pagination over the ``(starts_at, id)`` order.
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if end - start > _WORKSPACE_MAX_RANGE:
        raise HTTPException(status_code=400, detail="Requested range exceeds 90 days")

    if status is not None and status not in _WORKSPACE_STATUS_FACETS:
        raise HTTPException(status_code=400, detail=f"Unknown status facet: {status}")
    if source_type is not None and source_type not in _WORKSPACE_SOURCE_TYPE_FACETS:
        raise HTTPException(status_code=400, detail=f"Unknown source_type facet: {source_type}")

    cursor_pos: tuple[datetime, UUID] | None = None
    if cursor is not None:
        try:
            cursor_pos = _decode_workspace_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed cursor") from exc

    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    if view == "proposals":
        # Proposals lane: project pending calendar_event_proposals rows. This
        # read fails open — a missing table or query failure yields an empty
        # entries list, never an HTTP 500. There are no provider sources or
        # lanes for inferred proposals, so freshness/lanes are empty.
        proposal_entries = await _fetch_proposal_entries(
            db, start=start, end=end, butlers=butlers, display_tz=display_tz
        )
        data = CalendarWorkspaceReadResponse(
            entries=proposal_entries,
            source_freshness=[],
            lanes=[],
            next_cursor=None,
            has_more=False,
        )
        return ApiResponse[CalendarWorkspaceReadResponse](data=data)

    if view == "overlays":
        # Overlays lane: project precomputed domain-context contributions from
        # the cached cross-schema view ``calendar.v_overlay_contributions``. This
        # is a pure read of the precomputed view — NO LLM session and NO
        # cross-schema fan-out at request time (RFC-0020 no-LLM variant). It
        # fails open: a missing view / state table / query failure yields an
        # empty entries list with ``has_domain_context=false``, never a 500.
        # Overlays are read-only domain context, not provider/butler sources, so
        # freshness/lanes are empty.
        overlay_entries, has_domain_context = await _fetch_overlay_entries(
            db, start=start, end=end, butlers=butlers, display_tz=display_tz
        )
        data = CalendarWorkspaceReadResponse(
            entries=overlay_entries,
            source_freshness=[],
            lanes=[],
            next_cursor=None,
            has_more=False,
            has_domain_context=has_domain_context,
        )
        return ApiResponse[CalendarWorkspaceReadResponse](data=data)

    # Fetch one extra row beyond the page so we can derive ``has_more`` without
    # a separate count query (keyset pagination, no ``total``).
    workspace_rows = await _fetch_workspace_rows(
        db,
        view=view,
        start=start,
        end=end,
        butlers=butlers,
        sources=sources,
        status=status,
        source_type=source_type,
        editable=editable,
        cursor=cursor_pos,
        limit=limit + 1,
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
    # Keep each entry paired with its source row so the keyset cursor can be
    # derived from the raw (un-tz-converted) ``instance_starts_at`` + id.
    pairs: list[tuple[dict[str, Any], UnifiedCalendarEntry]] = []
    for row in workspace_rows:
        try:
            pairs.append((row, _normalize_entry(row, view=view, display_tz=display_tz)))
        except ValueError:
            continue

    # Hide noisy events: if the same title appears >20 times on a single
    # day, suppress all instances of that title for that day.
    day_title_counts: Counter[tuple[str, str]] = Counter()
    for _row, entry in pairs:
        day_key = entry.start_at.date().isoformat()
        day_title_counts[(day_key, entry.title)] += 1

    noisy: set[tuple[str, str]] = {k for k, v in day_title_counts.items() if v > 20}
    if noisy:
        pairs = [(r, e) for r, e in pairs if (e.start_at.date().isoformat(), e.title) not in noisy]

    # Keyset pagination: we fetched ``limit + 1`` rows, so anything beyond the
    # page means more rows remain. The cursor encodes the last returned row's
    # raw ``(instance_starts_at, instance_id)`` keyset position.
    has_more = len(pairs) > limit
    page = pairs[:limit]
    next_cursor: str | None = None
    if has_more and page:
        last_row, _last_entry = page[-1]
        last_starts_at = _coerce_datetime(last_row.get("instance_starts_at"))
        last_instance_id = last_row.get("instance_id")
        if last_starts_at is not None and isinstance(last_instance_id, UUID):
            next_cursor = _encode_workspace_cursor(last_starts_at, last_instance_id)

    data = CalendarWorkspaceReadResponse(
        entries=[entry for _row, entry in page],
        source_freshness=source_freshness,
        lanes=_build_lane_definitions(source_freshness),
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return ApiResponse[CalendarWorkspaceReadResponse](data=data)


@router.get("/search", response_model=ApiResponse[CalendarWorkspaceSearchResponse])
async def search_workspace(
    q: str = Query("", description="Free-text query over title/description/location"),
    view: str = Query("user", pattern="^(user|butler)$"),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butlers: list[str] | None = Query(None, description="Optional butler-name filters"),
    sources: list[str] | None = Query(None, description="Optional source_key filters"),
    limit: int = Query(50, ge=1, le=200, description="Max matches to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceSearchResponse]:
    """Full-text search over the calendar projection, ranked by trigram relevance.

    Matches ``q`` against ``calendar_events`` title/description/location fanned
    out across butler schemas (honoring the ``view`` lane and ``butlers`` /
    ``sources`` scoping), and returns ``UnifiedCalendarEntry``-shaped rows
    carrying each match's date(s) so the UI can group by day and jump-to.

    A missing or blank ``q`` returns an empty list (NOT the whole calendar) and
    never errors. The search degrades fail-open when a schema lacks the
    ``pg_trgm`` extension/index (ILIKE fallback or skip), so it does not 500.
    """
    import dataclasses

    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    if not q.strip():
        return ApiResponse[CalendarWorkspaceSearchResponse](
            data=CalendarWorkspaceSearchResponse(entries=[])
        )

    matches = await query_calendar_event_search(
        db,
        q=q,
        view=view,
        butlers=butlers,
        sources=sources,
        limit=limit,
    )

    entries: list[UnifiedCalendarEntry] = []
    for match in matches:
        row = dataclasses.asdict(match.row)
        try:
            entries.append(_normalize_entry(row, view=view, display_tz=display_tz))
        except ValueError:
            continue

    data = CalendarWorkspaceSearchResponse(entries=entries)
    return ApiResponse[CalendarWorkspaceSearchResponse](data=data)


@router.get("/entries/{entry_id}", response_model=ApiResponse[UnifiedCalendarEntry])
async def get_entry_detail(
    entry_id: UUID,
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[UnifiedCalendarEntry]:
    """Fetch a single calendar workspace entry by instance ID."""
    import dataclasses

    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    dto = await query_calendar_workspace_entry(db, entry_id=entry_id)
    if dto is None:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

    row = dataclasses.asdict(dto)
    view = str(row.get("lane") or "user")
    try:
        entry = _normalize_entry(row, view=view, display_tz=display_tz)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Entry has missing timestamps") from exc

    return ApiResponse[UnifiedCalendarEntry](data=entry)


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
            # Skip sources disabled via POST /api/calendar/sources — a disabled
            # source is off and must not be polled by the sync loop. An explicit
            # single-source sync (scope="source") bypasses this filter so an
            # operator can still recover a specific source on demand.
            if _normalize_json_object(row.get("source_metadata")).get("sync_enabled") is False:
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
        # Forward the cursor-recovery flag to the MCP tool. ``full=False``
        # preserves today's incremental sync behavior.
        forwarded_args = {**call_args, "full": request.full}
        try:
            client = await mcp_manager.get_client(butler_name)
            result = await client.call_tool("calendar_force_sync", forwarded_args)
            parsed = _parse_mcp_payload(_extract_mcp_result_text(result))
            status = (
                parsed.get("status", "sync_triggered")
                if isinstance(parsed, dict)
                else "sync_triggered"
            )
            recovery = bool(parsed.get("recovery")) if isinstance(parsed, dict) else False
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status=status,
                detail=_sync_detail(parsed),
                recovery=recovery,
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
        full=request.full,
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
# Find time — POST /api/calendar/workspace/find-time
# ---------------------------------------------------------------------------


@router.post("/find-time", response_model=ApiResponse[CalendarWorkspaceFindTimeResponse])
async def find_time(
    body: CalendarWorkspaceFindTimeRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarWorkspaceFindTimeResponse]:
    """Return ranked open time slots for the workspace "Find time" panel.

    Read-only: dispatches the ``calendar_find_free_slots`` MCP tool, which queries
    free/busy and ranks open slots honoring the owner's scheduling preferences. No
    event is created here — selecting a slot drives a separate create call.
    """
    arguments: dict[str, Any] = {
        "duration_minutes": body.duration_minutes,
        "search_start": body.search_start.isoformat(),
        "search_end": body.search_end.isoformat(),
        "limit": body.limit,
    }
    if body.calendar_ids:
        arguments["calendar_ids"] = body.calendar_ids
    if body.constraints is not None:
        arguments["constraints"] = body.constraints.model_dump(exclude_none=True)

    summary = {"duration_minutes": body.duration_minutes, "limit": body.limit}
    try:
        result = await _call_mcp_tool(mgr, body.butler_name, "calendar_find_free_slots", arguments)

        slots: list[CalendarSuggestedSlot] = []
        raw_slots = result.get("slots")
        if isinstance(raw_slots, list):
            for slot in raw_slots:
                if not isinstance(slot, dict):
                    continue
                try:
                    slots.append(CalendarSuggestedSlot.model_validate(slot))
                except Exception:
                    logger.debug("Skipping malformed slot: %r", slot)

        raw_ids = result.get("calendar_ids")
        calendar_ids = (
            [str(c) for c in raw_ids]
            if isinstance(raw_ids, list)
            else list(body.calendar_ids or [])
        )

        response_payload = CalendarWorkspaceFindTimeResponse(
            slots=slots,
            duration_minutes=body.duration_minutes,
            calendar_ids=calendar_ids,
        )
        await log_audit_entry(db, body.butler_name, "calendar.workspace.find_time", summary)
        return ApiResponse[CalendarWorkspaceFindTimeResponse](data=response_payload)
    except HTTPException:
        await log_audit_entry(
            db,
            body.butler_name,
            "calendar.workspace.find_time",
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


# ---------------------------------------------------------------------------
# Mutation undo — POST /api/calendar/workspace/undo/{action_id}
# ---------------------------------------------------------------------------

# Only user-lane mutations carry a reconstructable inverse; map each logged
# action_type to the existing calendar MCP tool that reverses it.
_UNDO_INVERSE_TOOL = {
    "workspace_user_update": "calendar_update_event",
    "workspace_user_delete": "calendar_create_event",
    "workspace_user_create": "calendar_delete_event",
}

# Per-inverse-tool dispatch statuses that mean the original action is now
# reversed. For an undo-of-create delete, "not_found" means the event is already
# gone — effectively undone. For an update-restore, only "updated" counts (a
# "not_found" there means the event vanished and the restore did NOT happen, so
# the action stays undoable).
_UNDO_SUCCESS_STATUSES = {
    "calendar_update_event": frozenset({"updated"}),
    "calendar_create_event": frozenset({"created"}),
    "calendar_delete_event": frozenset({"deleted", "not_found"}),
}

_UNDO_LOOKUP_SQL = """
    SELECT
        id,
        action_type,
        action_status,
        origin_ref,
        action_payload,
        action_result
    FROM calendar_action_log
    WHERE id = $1
"""


def _undo_update_args(pre_state: dict[str, Any]) -> dict[str, Any]:
    """Build inverse calendar_update_event args that restore the pre-state."""
    return {
        "event_id": pre_state.get("event_id"),
        "title": pre_state.get("title"),
        "start_at": pre_state.get("start_at"),
        "end_at": pre_state.get("end_at"),
        "timezone": pre_state.get("timezone"),
        "description": pre_state.get("description"),
        "body": pre_state.get("body"),
        "location": pre_state.get("location"),
        "attendees": pre_state.get("attendees"),
        "recurrence_rule": pre_state.get("recurrence_rule"),
        "color_id": pre_state.get("color_id"),
        "calendar_id": pre_state.get("calendar_id"),
    }


def _undo_create_args(pre_state: dict[str, Any]) -> dict[str, Any]:
    """Build inverse calendar_create_event args that recreate the deleted event."""
    return {
        "title": pre_state.get("title"),
        "start_at": pre_state.get("start_at"),
        "end_at": pre_state.get("end_at"),
        "timezone": pre_state.get("timezone"),
        "description": pre_state.get("description"),
        "body": pre_state.get("body"),
        "location": pre_state.get("location"),
        "attendees": pre_state.get("attendees"),
        "recurrence_rule": pre_state.get("recurrence_rule"),
        "color_id": pre_state.get("color_id"),
        "calendar_id": pre_state.get("calendar_id"),
    }


def _created_event_id(action_result: dict[str, Any], origin_ref: object) -> str | None:
    """Resolve the created event's provider id for an undo-of-create delete."""
    event = action_result.get("event")
    if isinstance(event, dict):
        candidate = event.get("event_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    if isinstance(origin_ref, str) and origin_ref.strip():
        return origin_ref.strip()
    return None


async def _find_action_owner(
    db: DatabaseManager, action_id: UUID
) -> tuple[str, dict[str, Any]] | None:
    """Locate the butler schema and row owning *action_id*.

    Action ids are globally unique UUIDs, so the first calendar butler with a
    matching ``calendar_action_log`` row owns it.
    """
    targets = db.butlers_with_module("calendar")
    results = await db.fan_out(_UNDO_LOOKUP_SQL, (action_id,), butler_names=targets)
    for butler_name, rows in results.items():
        if rows:
            return butler_name, dict(rows[0])
    return None


@router.post("/undo/{action_id}", response_model=ApiResponse[CalendarUndoResponse])
async def undo_calendar_mutation(
    action_id: UUID,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarUndoResponse]:
    """Reverse a single previously-applied calendar mutation.

    Synthesizes the inverse mutation from the logged ``calendar_action_log``
    row (``action_payload`` plus the captured pre-mutation ``pre_state`` in
    ``action_result``) and dispatches it through the existing calendar MCP
    tools with a freshly generated ``request_id`` — no new MCP tool, no
    provider call from the API layer itself.

    Fail-fast guards:
    - unknown ``action_id`` → 404
    - status not ``applied`` (pending/failed/noop) → 409
    - already undone → 409
    - applied but missing/expired pre-state (or unreversible type) → 422
    """
    owner = await _find_action_owner(db, action_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"Unknown calendar action: {action_id}")
    butler_name, row = owner

    action_type = str(row["action_type"])
    action_status = str(row["action_status"])
    action_result = _normalize_json_object(row["action_result"])

    if action_status != "applied":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Action {action_id} has status '{action_status}'; "
                "only an 'applied' mutation can be undone."
            ),
        )

    if isinstance(action_result.get("undo"), dict):
        raise HTTPException(
            status_code=409,
            detail=f"Action {action_id} was already undone.",
        )

    inverse_tool = _UNDO_INVERSE_TOOL.get(action_type)
    if inverse_tool is None:
        raise HTTPException(
            status_code=422,
            detail={
                "action_id": str(action_id),
                "action_type": action_type,
                "reason": "Action type has no reconstructable inverse mutation.",
            },
        )

    pre_state_raw = action_result.get("pre_state")
    pre_state = pre_state_raw if isinstance(pre_state_raw, dict) else None

    if action_type == "workspace_user_create":
        event_id = _created_event_id(action_result, row["origin_ref"])
        if event_id is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "action_id": str(action_id),
                    "action_type": action_type,
                    "reason": "Created event id is unavailable; cannot synthesize delete.",
                },
            )
        # The created event's home calendar can live on the pre-state (absent for
        # creates), the create result top-level, or the original action payload.
        payload = _normalize_json_object(row["action_payload"])
        calendar_id = None
        for candidate in (
            pre_state.get("calendar_id") if pre_state else None,
            action_result.get("calendar_id"),
            payload.get("calendar_id"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                calendar_id = candidate.strip()
                break
        arguments: dict[str, Any] = {"event_id": event_id}
        if calendar_id:
            arguments["calendar_id"] = calendar_id
    else:
        if pre_state is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "action_id": str(action_id),
                    "action_type": action_type,
                    "reason": (
                        "Captured pre-state is missing or expired; cannot reconstruct inverse."
                    ),
                },
            )
        arguments = (
            _undo_update_args(pre_state)
            if action_type == "workspace_user_update"
            else _undo_create_args(pre_state)
        )

    request_id = f"undo-{uuid4().hex}"
    arguments["request_id"] = request_id

    summary = {
        "undo_of": str(action_id),
        "action_type": action_type,
        "inverse_tool": inverse_tool,
        "request_id": request_id,
    }
    try:
        mutation_result = await _call_mcp_tool(mgr, butler_name, inverse_tool, arguments)
    except HTTPException:
        await log_audit_entry(
            db,
            butler_name,
            "calendar.workspace.undo",
            summary,
            result="error",
            error="MCP call failed",
        )
        raise

    undone = str(mutation_result.get("status")) in _UNDO_SUCCESS_STATUSES[inverse_tool]
    if undone:
        # Mark the original row so a repeated undo fails fast (409) rather than
        # dispatching a second inverse. jsonb concat merges the marker in place.
        marker = json.dumps(
            {
                "undo": {
                    "request_id": request_id,
                    "inverse_tool": inverse_tool,
                    "status": mutation_result.get("status"),
                }
            }
        )
        try:
            await db.pool(butler_name).execute(
                """
                UPDATE calendar_action_log
                SET action_result = COALESCE(action_result, '{}'::jsonb) || $2::jsonb,
                    updated_at = now()
                WHERE id = $1
                """,
                action_id,
                marker,
            )
        except Exception:  # pragma: no cover - defensive; undo already dispatched
            logger.warning(
                "Undo dispatched for action %s but marker write failed", action_id, exc_info=True
            )

    await log_audit_entry(db, butler_name, "calendar.workspace.undo", summary)

    data = CalendarUndoResponse(
        action_id=action_id,
        action_type=action_type,
        inverse_tool=inverse_tool,
        request_id=request_id,
        undone=undone,
        result=mutation_result,
    )
    return ApiResponse[CalendarUndoResponse](data=data)


# ===========================================================================
# Accounts control plane + per-calendar source toggle
#
# These endpoints live under /api/calendar (NOT /api/calendar/workspace) so a
# separate router is exported. ``deps.wire_db_dependencies`` overrides the
# shared ``_get_db_manager`` stub for this module, which both routers reference.
# ===========================================================================

accounts_router = APIRouter(prefix="/api/calendar", tags=["calendar", "accounts"])

_GOOGLE_CALENDAR_CONNECTOR_TYPE = "google_calendar"


def _shared_pool(db: DatabaseManager) -> Any | None:
    """Return the shared credential pool (for ``public.google_accounts``), or None."""
    try:
        return db.credential_shared_pool()
    except Exception:
        names = getattr(db, "butler_names", [])
        if not names:
            return None
        try:
            return db.pool(names[0])
        except Exception:
            logger.debug("calendar accounts: no shared/fallback pool available", exc_info=True)
            return None


def _switchboard_pool(db: DatabaseManager) -> Any | None:
    """Return the switchboard pool (for ``connector_registry`` health), or None."""
    try:
        return db.pool("switchboard")
    except Exception:
        logger.debug("calendar accounts: switchboard pool unavailable", exc_info=True)
        return None


def _map_health_state(raw_state: object) -> str:
    state = str(raw_state or "").strip().lower()
    if state in {"healthy", "ok", "running", "connected"}:
        return "healthy"
    if state in {"error", "failed"}:
        return "error"
    if state in {"degraded", "stale", "warning"}:
        return "degraded"
    return "unknown"


async def _fetch_calendar_heartbeats_by_email(
    switchboard_pool: Any,
) -> dict[str, dict[str, Any]] | None:
    """Return per-email Google Calendar connector heartbeat rows.

    The connector registers under ``endpoint_identity = "google_calendar:user:<email>"``.
    Returns a dict mapping email → most-recent heartbeat row, or ``None`` when the
    connector health surface cannot be reached (so the caller can mark
    ``health_available = False`` and degrade gracefully).
    """
    if switchboard_pool is None:
        return None
    try:
        rows = await switchboard_pool.fetch(
            "SELECT cr.state, cr.last_heartbeat_at, cr.endpoint_identity,"
            " cr.metadata, cr.error_message"
            " FROM connector_registry cr"
            " WHERE cr.connector_type = $1"
            " ORDER BY cr.last_heartbeat_at DESC NULLS LAST",
            _GOOGLE_CALENDAR_CONNECTOR_TYPE,
        )
    except Exception:
        logger.debug("connector_registry query failed for google_calendar", exc_info=True)
        return None
    by_email: dict[str, dict[str, Any]] = {}
    for row in rows:
        eid = row.get("endpoint_identity") or ""
        parts = eid.split(":", 2)
        if len(parts) != 3 or parts[0] != "google_calendar" or parts[1] != "user":
            continue
        email = parts[2]
        if email and email not in by_email:
            by_email[email] = dict(row)
    return by_email


def _build_account_health(heartbeat: dict[str, Any] | None) -> CalendarAccountHealth:
    if heartbeat is None:
        return CalendarAccountHealth(state="unknown")
    state = _map_health_state(heartbeat.get("state"))
    error_message = heartbeat.get("error_message")
    error_message = str(error_message).strip() if error_message else None
    last_ingest_at = None
    meta = _normalize_json_object(heartbeat.get("metadata"))
    raw_ingest = meta.get("last_ingest_at")
    if isinstance(raw_ingest, str):
        last_ingest_at = _coerce_datetime(raw_ingest)
    return CalendarAccountHealth(
        state=state,
        error_kind=classify_sync_error_kind(error_message if state != "healthy" else None),
        error_message=error_message if state != "healthy" else None,
        last_heartbeat_at=_coerce_datetime(heartbeat.get("last_heartbeat_at")),
        last_ingest_at=last_ingest_at,
    )


@accounts_router.get("/accounts", response_model=ApiResponse[CalendarAccountsResponse])
async def list_calendar_accounts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarAccountsResponse]:
    """List connected Google accounts joined with Google Calendar connector health.

    Read-only: this endpoint never connects or disconnects accounts (account
    lifecycle lives in the Google accounts surface). When the connector health
    surface is unavailable, accounts are still returned with an ``unknown``
    health indicator and ``health_available = false`` rather than failing.
    """
    shared_pool = _shared_pool(db)
    if shared_pool is None:
        return ApiResponse[CalendarAccountsResponse](
            data=CalendarAccountsResponse(accounts=[], health_available=False)
        )

    try:
        accounts = await list_google_accounts(shared_pool)
    except Exception:
        logger.warning("calendar accounts: failed to list google_accounts", exc_info=True)
        return ApiResponse[CalendarAccountsResponse](
            data=CalendarAccountsResponse(accounts=[], health_available=False)
        )

    heartbeats = await _fetch_calendar_heartbeats_by_email(_switchboard_pool(db))
    health_available = heartbeats is not None

    entries: list[CalendarAccountEntry] = []
    for account in accounts:
        heartbeat = (heartbeats or {}).get(account.email) if account.email else None
        entries.append(
            CalendarAccountEntry(
                account_id=account.id,
                email=account.email,
                display_name=account.display_name,
                is_primary=account.is_primary,
                status=account.status,
                health=_build_account_health(heartbeat),
            )
        )

    return ApiResponse[CalendarAccountsResponse](
        data=CalendarAccountsResponse(accounts=entries, health_available=health_available)
    )


@accounts_router.post("/sources", response_model=ApiResponse[CalendarSourceToggleResponse])
async def toggle_calendar_source(
    request: CalendarSourceToggleRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CalendarSourceToggleResponse]:
    """Enable or disable a single calendar as a sync source.

    Toggles the ``sync_enabled`` flag on the existing ``calendar_sources`` row
    (JSONB ``metadata``) in the owning butler schema — no new table. A disabled
    source is skipped by the sync loop and rendered "off" (not failed) in the
    workspace meta.
    """
    if request.butler not in db.butler_names:
        raise HTTPException(status_code=404, detail=f"Unknown butler: {request.butler}")

    pool = db.pool(request.butler)
    if request.source_id is not None:
        where_clause = "id = $2"
        where_arg: Any = request.source_id
    else:
        where_clause = "source_key = $2"
        where_arg = request.source_key

    row = await pool.fetchrow(
        f"""
        UPDATE calendar_sources
        SET metadata = COALESCE(metadata, '{{}}'::jsonb)
                || jsonb_build_object('sync_enabled', $1::boolean),
            updated_at = now()
        WHERE {where_clause}
        RETURNING id, source_key, calendar_id
        """,
        request.enabled,
        where_arg,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Requested calendar source was not found")

    await log_audit_entry(
        db,
        request.butler,
        "calendar.workspace.source_toggle",
        {"source_key": row["source_key"], "enabled": request.enabled},
    )

    return ApiResponse[CalendarSourceToggleResponse](
        data=CalendarSourceToggleResponse(
            butler=request.butler,
            source_key=row["source_key"],
            source_id=row["id"],
            calendar_id=row["calendar_id"],
            enabled=request.enabled,
        )
    )
