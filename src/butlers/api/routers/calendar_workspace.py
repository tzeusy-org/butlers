"""Calendar workspace read/meta/sync endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastmcp.exceptions import ToolError

from butlers.api.butler_tool_calls import tool_result_payload
from butlers.api.calendar.quick_add import parse_quick_add
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, Db, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse
from butlers.api.models.calendar import (
    CalendarWorkspaceButlerMutationRequest,
    CalendarWorkspaceUserMutationRequest,
)
from butlers.api.models.calendar_workspace import (
    CalendarAccountEntry,
    CalendarAccountHealth,
    CalendarAccountHealthState,
    CalendarAccountsResponse,
    CalendarAuditEntry,
    CalendarAuditResponse,
    CalendarButlerEventPreviewRequest,
    CalendarButlerEventPreviewResponse,
    CalendarChange,
    CalendarChangesResponse,
    CalendarConflictEntry,
    CalendarDayBriefingResponse,
    CalendarInvitationEntry,
    CalendarInvitationsResponse,
    CalendarLinkedPerson,
    CalendarOrganizerDecisionRequest,
    CalendarOrganizerDecisionResponse,
    CalendarPrepAttendee,
    CalendarPrepCommitment,
    CalendarPrepNote,
    CalendarPrepResponse,
    CalendarProposalAcceptRequest,
    CalendarProposalActionResponse,
    CalendarQuarantinedInvitation,
    CalendarRespondRequest,
    CalendarRespondResponse,
    CalendarSourceRoleRequest,
    CalendarSourceRoleResponse,
    CalendarSourceToggleRequest,
    CalendarSourceToggleResponse,
    CalendarSuggestedSlot,
    CalendarSuppressedGroup,
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
    ConflictEventRef,
    ConflictIssue,
    ConflictResolveRequest,
    ConflictResolveResponse,
    ConflictScanResponse,
    ConflictScanWindow,
    DayBriefingButlerGroup,
    DayBriefingKindGroup,
    DaySheetFreshness,
    DaySheetGap,
    DaySheetItem,
    DaySheetResponse,
    DayVerdictEntry,
    DayVerdictResponse,
    DayVerdictWindow,
    HouseholdEventRef,
    HouseholdSubjectGroup,
    HouseholdTimeResponse,
    OverlayFreshness,
    QuickAddDraft,
    QuickAddParseRequest,
    QuickAddParseResponse,
    ResolutionOption,
    SetPrimaryCalendarRequest,
    SetPrimaryCalendarResponse,
    UnifiedCalendarEntry,
)
from butlers.api.owner_time_bounds import owner_zoneinfo
from butlers.api.pagination import InvalidCursorError, decode_cursor, encode_cursor
from butlers.api.read_models.calendar_changes import CHANGES_MAX_AGE, query_calendar_changes
from butlers.api.read_models.calendar_day_sheet import (
    query_calendar_day_sheet,
    query_calendar_day_verdict,
)
from butlers.api.read_models.calendar_resolution import resolution_options_live
from butlers.api.read_models.calendar_workspace import (
    CalendarOverlayRow,
    CalendarPrepRow,
    CalendarProposalRow,
    _coerce_datetime,
    _dedup_workspace_rows,
    compute_overlay_freshness,
    load_conflict_acknowledgements,
    load_dedup_rules,
    load_keep_separate_keys,
    query_calendar_conflicts,
    query_calendar_entry_people,
    query_calendar_event_search,
    query_calendar_invitations,
    query_calendar_prep,
    query_calendar_proposal_by_id,
    query_calendar_sources,
    query_calendar_workspace,
    query_calendar_workspace_entry,
    query_household_time,
    query_scheduler_mirror_counts,
    record_conflict_acknowledgement,
    scan_calendar_overlays,
    scan_calendar_proposals,
    shallow_asdict,
    update_calendar_proposal_status,
)
from butlers.api.routers.audit import log_audit_entry
from butlers.config import list_butlers
from butlers.core.general_settings import load_configured_general_timezone
from butlers.core.liveness import derive_liveness
from butlers.core.temporal.day_sheet import render_day_sheet_text
from butlers.core.temporal.household_context import SUBJECT_ROLES, HouseholdEvent
from butlers.credential_store import CredentialStore
from butlers.google_account_registry import list_google_accounts
from butlers.modules.calendar import (
    CalendarModule,
    _cron_occurrences_in_window,
    classify_sync_error_kind,
)


def _calendar_core_enabled_butlers() -> set[str]:
    """Butler names whose calendar module exposes the ``core`` tool group.

    ``calendar_force_sync`` is registered under the ``core`` group, so a butler
    whose calendar module is group-restricted without ``core`` (e.g. finance:
    ``groups = ["butler_events"]``) never registers the tool and a call would
    raise ``ToolError``. Such butlers are excluded from the ``all`` fan-out so
    they do not surface as a failed target on every workspace sync. Reads the
    module config the same way ``group_enabled`` does.
    """
    enabled: set[str] = set()
    for cfg in list_butlers():
        cal = cfg.modules.get("calendar")
        if cal is None:
            continue
        groups = cal.get("groups")
        if not groups or "core" in groups:
            enabled.add(cfg.name)
    return enabled


def _calendar_response_enabled_butlers() -> set[str]:
    """Configured response owners; runtime admission remains independently gated."""
    enabled = set()
    try:
        configs = list_butlers()
    except Exception:
        return enabled
    for config in configs:
        calendar = config.modules.get("calendar") or {}
        approval = config.modules.get("approvals") or {}
        groups = calendar.get("groups")
        if (
            calendar.get("response_enabled") is True
            and (not groups or "response" in groups)
            and approval.get("enabled") is True
            and "calendar_respond" in (approval.get("gated_tools") or {})
        ):
            enabled.add(config.name)
    return enabled


router = APIRouter(prefix="/api/calendar/workspace", tags=["calendar", "workspace"])
logger = logging.getLogger(__name__)

_WORKSPACE_STALE_THRESHOLD = timedelta(minutes=10)
_WORKSPACE_MAX_RANGE = timedelta(days=90)

# Dry-run recurrence preview projects over the same 90-day window the workspace
# read uses, so the preview matches the dates the user will actually see.
_PREVIEW_WINDOW = timedelta(days=90)

# Human-readable units used when warning that a lossy RRULE->cron conversion
# collapses an INTERVAL down to the base frequency.
_RRULE_FREQ_UNIT = {
    "DAILY": "day",
    "WEEKLY": "week",
    "MONTHLY": "month",
    "YEARLY": "year",
}

# Keyset pagination bounds for the workspace read.
_WORKSPACE_DEFAULT_LIMIT = 200
_WORKSPACE_MAX_LIMIT = 1000
#: A title repeated more than this many times on one day is collapsed into a suppressed group.
_WORKSPACE_NOISY_GROUP_SIZE = 20

# Allowed values for the server-side facets. ``status`` mirrors the computed
# values produced by ``_entry_status`` / ``STATUS_SQL``; ``source_type`` mirrors
# ``_source_type`` / ``SOURCE_TYPE_SQL``. Unknown values yield a 400.
_WORKSPACE_STATUS_FACETS = frozenset({"active", "paused", "cancelled", "error", "completed"})
_WORKSPACE_SOURCE_TYPE_FACETS = frozenset(
    {"provider_event", "scheduled_task", "butler_reminder", "manual_butler_event"}
)

# Overlay contributions carry the owner's calendar dates (the contribution jobs bucket every
# domain event onto the owner-zone date, SGT when no zone was ever saved). The day-briefing read
# defaults to the owner zone when no display timezone is requested (``_overlay_owner_zone``); the
# SGT default below is only the read-side fallback when the owner zone cannot be read, and the
# anchor for the other overlay reads that take no owner zone.
_OVERLAY_DEFAULT_TZ = ZoneInfo("Asia/Singapore")


def _overlay_now() -> datetime:
    """The instant overlay freshness is judged at (a seam: tests pin it)."""
    return datetime.now(UTC)


# Namespace for deterministic, stable overlay entry IDs. Overlay entries have no
# natural DB id (they live inside a JSONB envelope), so the entry_id is derived
# from (butler, date, index, kind, label) — stable across reads of the same view.
_OVERLAY_ENTRY_NAMESPACE = uuid5(NAMESPACE_URL, "butlers/calendar/overlay-contribution")


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
        sync_applicable=row["source_kind"] == "provider_event",
        time_role=row.get("time_role") or "unclassified",
        role_basis=row.get("role_basis") or "provider_inferred",
    )


async def _with_mirror_health(
    db: DatabaseManager,
    freshness: list[CalendarWorkspaceSourceFreshness],
    *,
    butlers: list[str] | None,
) -> list[CalendarWorkspaceSourceFreshness]:
    """Mark a butler's internal scheduler source failed while its calendar mirrors are failing."""
    if not any(item.source_kind == "internal_scheduler" for item in freshness):
        return freshness
    counts = await query_scheduler_mirror_counts(db, butlers=butlers)
    result: list[CalendarWorkspaceSourceFreshness] = []
    for item in freshness:
        failed, retiring = counts.get(item.butler_name or "", (0, 0))
        if item.source_kind != "internal_scheduler" or not (failed or retiring):
            result.append(item)
            continue
        result.append(
            item.model_copy(
                update={
                    "mirror_failed": failed,
                    "mirror_retiring": retiring,
                    "sync_state": "failed",
                    "last_error": f"{failed + retiring} calendar mirror(s) failing",
                    "error_kind": "transient",
                }
            )
        )
    return result


def _calendar_source_owner_rank(
    row: Mapping[str, Any],
    *,
    core_calendar_butlers: set[str],
) -> tuple[int, int, int, float, str, str]:
    """Return a stable preference order for duplicate physical source rows.

    ``query_calendar_sources`` intentionally returns every schema-local ledger
    row. The dashboard must collapse only its *logical* view, choosing an
    owner that can actually expose ``calendar_force_sync`` before looking at
    source health. Otherwise Finance's alphabetically first, non-core copy can
    make a healthy General source look permanently unsynced.
    """
    owner = str(row.get("db_butler") or row.get("butler_name") or "")
    metadata = _normalize_json_object(row.get("source_metadata"))
    enabled_rank = 0 if metadata.get("sync_enabled") is not False else 1
    core_rank = 0 if owner in core_calendar_butlers else 1
    source_freshness = _to_source_freshness(row)
    state_rank = {"fresh": 0, "stale": 1, "failed": 2}.get(source_freshness.sync_state, 3)
    observed_at = source_freshness.last_success_at or source_freshness.last_synced_at
    observed_timestamp = observed_at.timestamp() if observed_at is not None else float("-inf")
    return (
        enabled_rank,
        core_rank,
        state_rank,
        -observed_timestamp,
        owner,
        str(row.get("source_id") or ""),
    )


def _select_canonical_source_rows(
    source_rows: list[dict[str, Any]],
    *,
    core_calendar_butlers: set[str],
) -> list[dict[str, Any]]:
    """Select one operational owner per logical source without hiding raw evidence.

    This policy intentionally lives above the v1 read-model boundary: callers
    that need physical ledger evidence still receive all fan-out rows from
    ``query_calendar_sources``. Dashboard read/meta/sync paths get one
    deterministic, operational source owner per ``source_key`` instead.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        source_key = row.get("source_key")
        group_key = (
            str(source_key)
            if isinstance(source_key, str) and source_key
            else f"source-id:{row.get('source_id')}"
        )
        grouped[group_key].append(row)

    return [
        min(
            grouped[source_key],
            key=lambda row: _calendar_source_owner_rank(
                row,
                core_calendar_butlers=core_calendar_butlers,
            ),
        )
        for source_key in sorted(grouped)
    ]


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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch calendar source rows via the versioned read-model boundary.

    Delegates to :func:`~butlers.api.read_models.calendar_workspace.query_calendar_sources`
    and converts the typed :class:`~butlers.api.read_models.calendar_workspace.CalendarSourceRow`
    DTOs back to plain dicts for the existing downstream helpers
    (``_to_source_freshness``, ``_normalize_entry``, etc.) that expect
    ``Mapping[str, Any]`` inputs.

    Returns ``(rows, failed_butlers)`` so callers rendering a source-freshness /
    connected-sources verdict can flag a partial fan-out failure instead of reading
    an incomplete source list as a truthful all-clear (bu-sn71y).
    """
    source_rows, failed = await query_calendar_sources(
        db, lane=lane, butlers=butlers, sources=sources
    )
    return [shallow_asdict(row) for row in source_rows], failed


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
    dedup_strategy: str | None = None,
    keep_separate: set[str] | None = None,
    page_size: int | None = None,
) -> tuple[list[dict[str, Any]], list[str], tuple[bool, tuple[datetime, UUID] | None]]:
    """Fetch calendar event-instance rows via the versioned read-model boundary.

    Delegates to
    :func:`~butlers.api.read_models.calendar_workspace.query_calendar_workspace`
    and converts the typed
    :class:`~butlers.api.read_models.calendar_workspace.CalendarWorkspaceRow`
    DTOs back to plain dicts for the existing downstream helpers
    (``_normalize_entry``, deduplication logic, etc.) that expect
    ``Mapping[str, Any]`` inputs.

    The ``status`` / ``source_type`` / ``editable`` facets, the keyset
    ``cursor``, and ``limit`` are applied **server-side** in the fan-out query.
    Rows from every butler schema are merged and re-sorted by the global keyset
    order ``(starts_at, id)`` so dedup and cursor pagination are deterministic
    across schemas.

    ``dedup_strategy`` selects which collapse passes run (defaults to
    ``balanced``); ``keep_separate`` pins cluster keys the user chose not to
    collapse.  Both flow into :func:`_dedup_workspace_rows`.

    ``page_size`` pages the raw rows by keyset *before* dedup: the third return value is
    ``(has_more, last_raw_keyset)`` taken from the raw page, so a page that dedup shrinks (or whose
    rows are later dropped) still reports the rows behind it. Without it every row is returned.

    Returns ``(deduped_rows, failed_butlers, (has_more, last_raw_keyset))`` — ``failed_butlers``
    is threaded up so the endpoint can surface an honest degraded flag on a partial fan-out
    failure.
    """
    flattened, failed = await _fetch_flattened_workspace_rows(
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
    has_more = False
    last_keyset: tuple[datetime, UUID] | None = None
    if page_size is not None:
        has_more = len(flattened) > page_size
        flattened = flattened[:page_size]
        if has_more and flattened:
            last = flattened[-1]
            last_starts_at = _coerce_datetime(last.get("instance_starts_at"))
            last_id = last.get("instance_id")
            if last_starts_at is not None and isinstance(last_id, UUID):
                last_keyset = (last_starts_at, last_id)
    deduped, _clusters = _dedup_workspace_rows(
        flattened, strategy=dedup_strategy, keep_separate=keep_separate
    )
    return deduped, failed, (has_more, last_keyset)


async def _fetch_flattened_workspace_rows(
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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch pre-dedup workspace rows (flat dicts), globally sorted by keyset.

    Splits the fetch+flatten+sort half out of :func:`_fetch_workspace_rows` so
    both the deduped workspace read **and** the duplicate-review surface can share
    the same source rows (the surface needs the rows *before* collapse).

    Returns ``(flattened_rows, failed_butlers)`` — the failed list is threaded up
    from :func:`query_calendar_workspace` so the endpoint can flag a partial
    fan-out failure (silently-dropped schema) instead of rendering a short result
    as an honest empty grid.
    """
    workspace_dtos, failed = await query_calendar_workspace(
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
    flattened: list[dict[str, Any]] = [shallow_asdict(dto) for dto in workspace_dtos]

    # Re-sort across schemas by the global keyset order so dedup keeps the
    # lowest-id copy deterministically and cursor pagination is stable.
    flattened.sort(key=lambda r: (r["instance_starts_at"], r["instance_id"]))
    return flattened, failed


def _source_zone_name(row: Mapping[str, Any]) -> str | None:
    """The event's own zone: a real IANA zone the provider stated, else ``None`` (never a guess).

    Only a provider event whose payload carried a ``timeZone`` qualifies. Internal rows and
    scheduler projections store write-time defaults (the ``UTC`` follow-the-owner sentinel, the
    module config zone), which say nothing about where the event was authored.
    """
    metadata = _effective_event_metadata(row)
    if metadata.get("source_type") != "provider_event":
        return None
    if metadata.get("source_zone_from_payload") is not True:
        return None
    for key in ("instance_timezone", "event_timezone"):
        name = row.get(key)
        if isinstance(name, str) and name.strip():
            try:
                ZoneInfo(name.strip())
            except (ZoneInfoNotFoundError, ValueError):
                continue  # an unusable instance zone falls through to the event's
            return name.strip()
    return None


def _effective_event_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("event_metadata", "instance_metadata"):
        merged.update(_normalize_json_object(row.get(key)))
    return merged


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
        event_id=_safe_uuid(row.get("event_id")),
        view=view,
        source_type=source_type,
        source_key=row["source_key"],
        title=str(row.get("title") or "Untitled"),
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
        source_timezone=_source_zone_name(row),
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
) -> tuple[list[UnifiedCalendarEntry], bool, int]:
    """Fetch + project pending proposals: ``(entries, available, dropped_rows)``.

    The read MUST NEVER surface an HTTP 500, but a failure is no longer an empty list that
    reads as "nothing to review": ``available`` is ``False`` when any schema's read failed, and
    ``dropped_rows`` counts rows that could not be normalised (bu-5ii4gj.4).
    """
    try:
        scan = await scan_calendar_proposals(db, start=start, end=end, butlers=butlers)
    except Exception:
        logger.warning("proposals projection query failed", exc_info=True)
        return [], False, 0

    entries: list[UnifiedCalendarEntry] = []
    dropped = scan.unreadable_rows
    for proposal in scan.rows:
        try:
            entries.append(_normalize_proposal_entry(proposal, display_tz=display_tz))
        except ValueError:
            dropped += 1
    if dropped:
        logger.warning("proposals projection dropped %d malformed row(s)", dropped)
    return entries, scan.available, dropped


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
) -> tuple[list[UnifiedCalendarEntry], bool, bool, list[OverlayFreshness]]:
    """Fetch + project cached overlay contributions, never raising.

    Returns ``(entries, has_domain_context, available, freshness)``; ``available`` is ``False``
    when the view could not be read, which is not the same as "no domain context" (bu-5ii4gj.4).
    ``has_domain_context`` is ``True`` when at least one **valid** contribution envelope exists
    for a date within ``[start, end)`` — even when that envelope has no entries (``has_entries``
    false) — so the FE can distinguish "no domain context for this range" from
    "context temporarily unavailable". A missing view, a missing specialist
    ``state`` table, or any query failure yields ``([], False, False)`` — never a 500.
    """
    overlays, available = await scan_calendar_overlays(db, butlers=butlers)
    freshness = [
        OverlayFreshness(**item)
        for item in compute_overlay_freshness(overlays, available=available, now=_overlay_now())
    ]
    not_current = {f.butler: f for f in freshness if f.state in ("stale", "partial")}

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
            for entry in projected_entries:
                flagged = not_current.get(entry.butler_name or "")
                if flagged is not None:
                    # Never wear current-data authority: stale or incomplete producers are named.
                    entry.metadata = {
                        **(entry.metadata or {}),
                        flagged.state: True,
                        "stale_since": flagged.stale_since.isoformat()
                        if flagged.stale_since
                        else None,
                    }
            entries.extend(projected_entries)
    return entries, has_domain_context, available, freshness


def _group_overlay_entries_by_butler_kind(
    entries: list[UnifiedCalendarEntry],
) -> list[DayBriefingButlerGroup]:
    """Group projected overlay entries by ``source_butler`` then ``kind``.

    Produces the structured day-briefing grouping: one
    :class:`DayBriefingButlerGroup` per contributing specialist (deterministic
    butler order), each carrying its entries bucketed into
    :class:`DayBriefingKindGroup` rows by ``kind``.  Entry order within a kind is
    preserved from the input (already priority/index ordered by the projection).

    The butler/kind keys are read from each entry's ``metadata`` (the projection
    stamps ``source_butler`` and ``kind`` there), falling back to the entry's
    ``source_butler`` / ``butler_name`` so grouping never drops an entry.
    """
    # Preserve first-seen order of butlers/kinds while bucketing.
    by_butler: dict[str, dict[str, list[UnifiedCalendarEntry]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for entry in entries:
        md = entry.metadata or {}
        source_butler = (
            md.get("source_butler") or entry.source_butler or entry.butler_name or "unknown"
        )
        kind = md.get("kind") or "other"
        by_butler[str(source_butler)][str(kind)].append(entry)

    groups: list[DayBriefingButlerGroup] = []
    for butler_name in sorted(by_butler):
        kind_map = by_butler[butler_name]
        kind_groups = [
            DayBriefingKindGroup(kind=kind, entries=kind_map[kind]) for kind in sorted(kind_map)
        ]
        count = sum(len(kg.entries) for kg in kind_groups)
        groups.append(
            DayBriefingButlerGroup(source_butler=butler_name, count=count, kinds=kind_groups)
        )
    return groups


def _parse_prep_attendee(raw: Mapping[str, Any]) -> CalendarPrepAttendee | None:
    """Project one raw attendee object from a prep envelope into a typed model.

    Returns ``None`` when the attendee is malformed (missing ``entity_id`` /
    ``name``) so it is skipped rather than surfaced half-populated.
    """
    entity_id = raw.get("entity_id")
    name = raw.get("name")
    if not entity_id or not name:
        return None

    notes: list[CalendarPrepNote] = []
    for raw_note in raw.get("notes") or []:
        if not isinstance(raw_note, Mapping):
            continue
        kind = raw_note.get("kind")
        text = raw_note.get("text")
        if kind and text:
            notes.append(CalendarPrepNote(kind=str(kind), text=str(text)))

    message_context = [m for m in (raw.get("message_context") or []) if isinstance(m, Mapping)]
    commitments = [
        CalendarPrepCommitment.model_validate(raw_commitment)
        for raw_commitment in (raw.get("commitments") or [])
        if isinstance(raw_commitment, Mapping)
    ]

    tier = raw.get("dunbar_tier")
    return CalendarPrepAttendee(
        entity_id=str(entity_id),
        name=str(name),
        dunbar_tier=tier if isinstance(tier, int) else None,
        notes=notes,
        last_met=str(raw["last_met"]) if raw.get("last_met") else None,
        last_met_event=str(raw["last_met_event"]) if raw.get("last_met_event") else None,
        last_met_basis=str(raw["last_met_basis"]) if raw.get("last_met_basis") else None,
        last_scheduled=str(raw["last_scheduled"]) if raw.get("last_scheduled") else None,
        last_scheduled_event=(
            str(raw["last_scheduled_event"]) if raw.get("last_scheduled_event") else None
        ),
        message_context=[dict(m) for m in message_context],
        commitments=commitments,
    )


def _merge_prep_attendee(into: CalendarPrepAttendee, extra: CalendarPrepAttendee) -> None:
    """Merge ``extra``'s context into ``into`` (same attendee from another butler).

    Scalars (name, tier, last-met) keep the first non-empty value; list fields
    (notes, message_context) are concatenated. This lets the email/message-owning
    butlers contribute ``message_context`` for an attendee whose attendee/notes/
    last-met came from the relationship butler.
    """
    into.dunbar_tier = into.dunbar_tier if into.dunbar_tier is not None else extra.dunbar_tier
    if not into.last_met and extra.last_met:
        into.last_met = extra.last_met
        into.last_met_event = extra.last_met_event
        into.last_met_basis = extra.last_met_basis
    if not into.last_scheduled and extra.last_scheduled:
        into.last_scheduled = extra.last_scheduled
        into.last_scheduled_event = extra.last_scheduled_event
    into.notes.extend(extra.notes)
    into.message_context.extend(extra.message_context)
    into.commitments.extend(extra.commitments)


def _project_prep_contributions(
    prep_rows: list[CalendarPrepRow],
) -> tuple[list[CalendarPrepAttendee], list[str]]:
    """Merge prep-contribution envelopes across butlers into one attendee list.

    Returns ``(attendees, source_butlers)``. Envelopes whose payload ``butler``
    disagrees with the view's hardcoded ``butler`` source column are skipped
    (RFC 0010 guardrail #2). Attendees seen in more than one envelope are merged
    by ``entity_id`` so a single attendee carries relationship context AND any
    message context contributed by another butler.
    """
    by_entity: dict[str, CalendarPrepAttendee] = {}
    order: list[str] = []
    source_butlers: list[str] = []

    for row in prep_rows:
        value = _normalize_json_object(row.value)
        envelope_butler = value.get("butler")
        if row.butler is not None and envelope_butler != row.butler:
            # RFC 0010 guardrail #2: skip an envelope whose payload butler
            # disagrees with the view's hardcoded source column — including the
            # malformed missing/null payload-butler case (None != literal).
            logger.warning(
                "prep contribution butler mismatch (column=%r payload=%r); skipping",
                row.butler,
                envelope_butler,
            )
            continue

        butler_label = str(envelope_butler or row.butler or "unknown")
        if butler_label not in source_butlers:
            source_butlers.append(butler_label)

        for raw_attendee in value.get("attendees") or []:
            if not isinstance(raw_attendee, Mapping):
                continue
            attendee = _parse_prep_attendee(raw_attendee)
            if attendee is None:
                continue
            existing = by_entity.get(attendee.entity_id)
            if existing is None:
                by_entity[attendee.entity_id] = attendee
                order.append(attendee.entity_id)
            else:
                _merge_prep_attendee(existing, attendee)

    attendees = [by_entity[eid] for eid in order]
    return attendees, sorted(source_butlers)


def _prep_participant_state(prep_rows: list[CalendarPrepRow]) -> tuple[bool, int]:
    """(attendees unavailable, unresolved count) carried by the prep envelopes."""
    unavailable, unresolved = False, 0
    for row in prep_rows:
        value = _normalize_json_object(row.value)
        unavailable = unavailable or value.get("attendees_unavailable") is True
        count = value.get("unresolved_attendees")
        if isinstance(count, int) and not isinstance(count, bool):
            unresolved = max(unresolved, count)
    return unavailable, unresolved


@router.get("/prep/{event_id}", response_model=ApiResponse[CalendarPrepResponse])
async def get_meeting_prep(
    event_id: UUID,
    *,
    db: Db,
) -> ApiResponse[CalendarPrepResponse]:
    """Return the meeting-prep rail context for a selected calendar event.

    Reads the precomputed ``calendar.v_prep_contributions`` view for the single
    event and projects the cached prep envelopes (attendees + relationship notes
    + last-met, merged across contributing butlers). This is a pure read of the
    cached cross-schema view — **NO direct ``relationship.*`` / ``health.*``
    SELECT and NO per-open LLM session** (RFC-0020 no-LLM variant). The prep
    contributions are produced by deterministic scheduled jobs; any narrative
    would come only from a deferred batched pre-render, never from here.

    Honest empty-state: when no prep contribution exists for the event
    (co-attended-edge / contact-link coverage not yet populated — the expected
    state for most events today), ``has_prep_context`` is ``False`` with an empty
    ``attendees`` list. The read is fail-open — a missing view / specialist
    ``state`` table / query failure degrades to that empty-state, never an HTTP
    500, and does NOT use the ``aggregates_available`` Prometheus envelope.
    """
    prep_rows = await query_calendar_prep(db, event_id=event_id)

    attendees, source_butlers = _project_prep_contributions(prep_rows)
    attendees_unavailable, unresolved_attendees = _prep_participant_state(prep_rows)
    data = CalendarPrepResponse(
        event_id=str(event_id),
        # A contribution exists for this event when any (valid) envelope was read,
        # even if it resolved zero attendees (honest empty-state vs "no job ran").
        has_prep_context=bool(source_butlers),
        attendees=attendees,
        source_butlers=source_butlers,
        attendees_unavailable=attendees_unavailable,
        unresolved_attendees=unresolved_attendees,
    )
    return ApiResponse[CalendarPrepResponse](data=data)


async def _overlay_owner_zone(db: DatabaseManager) -> ZoneInfo:
    """The owner's saved zone (what the briefing jobs key by); SGT when none was ever saved.

    Fails open to the SGT default: this is a read of cached contributions, never a 500.
    """
    pool = _calendar_accounts_shared_pool(db)
    if pool is None:  # an empty fleet has no settings to read
        return _OVERLAY_DEFAULT_TZ
    try:
        name = await load_configured_general_timezone(pool)
        return ZoneInfo(name) if name is not None else _OVERLAY_DEFAULT_TZ
    except Exception:
        logger.warning("calendar day-briefing: owner zone unreadable; using SGT", exc_info=True)
        return _OVERLAY_DEFAULT_TZ


@router.get("/day-briefing", response_model=ApiResponse[CalendarDayBriefingResponse])
async def get_day_briefing(
    date: datetime = Query(
        ..., description="Target calendar date (ISO-8601; the date component is used)"
    ),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butlers: list[str] | None = Query(None, description="Optional butler-name filters"),
    *,
    db: Db,
) -> ApiResponse[CalendarDayBriefingResponse]:
    """Return the structured "tomorrow at a glance" day-briefing card for a date.

    Reads the precomputed ``calendar.v_overlay_contributions`` view for the
    single target date and groups that date's overlay entries by butler/kind.
    This is a pure read of the cached overlay view — **NO per-open LLM call** and
    no cross-schema fan-out at request time (RFC-0020 no-LLM variant). Any prose
    comes only from the deferred batched pre-render (cf5), never from here.

    Honest empty-state: ``has_domain_context`` is ``True`` when at least one
    specialist wrote a contribution for the date (even ``has_entries=false``), so
    the FE renders the card; ``False`` when no specialist contributed, so the FE
    renders "No domain context for this day". The read is fail-open — a missing
    view / specialist table / query failure degrades to the empty-state, never an
    HTTP 500, and does NOT use the ``aggregates_available`` Prometheus envelope.
    """
    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    # Anchor the day window in the timezone the contribution jobs bucketed the date in: the
    # requested one, else the owner's (the same zone ``briefing_day`` keys with).
    if display_tz is None:
        display_tz = await _overlay_owner_zone(db)
    tz = display_tz
    target_date = date.date()
    start_at = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    end_at = start_at + timedelta(days=1)

    (
        overlay_entries,
        has_domain_context,
        overlays_available,
        freshness,
    ) = await _fetch_overlay_entries(
        db, start=start_at, end=end_at, butlers=butlers, display_tz=display_tz
    )

    groups = _group_overlay_entries_by_butler_kind(overlay_entries)
    data = CalendarDayBriefingResponse(
        date=target_date.isoformat(),
        timezone=tz.key,
        has_domain_context=has_domain_context,
        overlays_available=overlays_available,
        overlay_freshness=freshness,
        has_entries=bool(overlay_entries),
        groups=groups,
        entries=overlay_entries,
    )
    return ApiResponse[CalendarDayBriefingResponse](data=data)


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
    *,
    db: Db,
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

    cursor_pos = (
        decode_cursor(cursor, (datetime, UUID), scope="calendar-workspace")
        if cursor is not None
        else None
    )

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
        proposal_entries, proposals_available, proposals_dropped = await _fetch_proposal_entries(
            db, start=start, end=end, butlers=butlers, display_tz=display_tz
        )
        data = CalendarWorkspaceReadResponse(
            entries=proposal_entries,
            source_freshness=[],
            lanes=[],
            next_cursor=None,
            has_more=False,
            proposals_available=proposals_available,
            proposals_dropped_rows=proposals_dropped,
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
        (
            overlay_entries,
            has_domain_context,
            overlays_available,
            freshness,
        ) = await _fetch_overlay_entries(
            db, start=start, end=end, butlers=butlers, display_tz=display_tz
        )
        data = CalendarWorkspaceReadResponse(
            entries=overlay_entries,
            source_freshness=[],
            lanes=[],
            next_cursor=None,
            has_more=False,
            has_domain_context=has_domain_context,
            overlays_available=overlays_available,
            overlay_freshness=freshness,
        )
        return ApiResponse[CalendarWorkspaceReadResponse](data=data)

    # Honor the persisted cross-source dedup rules + keep-separate overrides so
    # the live read collapses (or keeps apart) exactly what the duplicate-review
    # surface shows. Both loads fail open to defaults / no overrides.
    dedup_rules = await load_dedup_rules(db)
    keep_separate = await load_keep_separate_keys(db)

    # Fetch one extra row beyond the page so we can derive ``has_more`` without
    # a separate count query (keyset pagination, no ``total``).
    workspace_rows, workspace_failed, (has_more, last_keyset) = await _fetch_workspace_rows(
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
        dedup_strategy=dedup_rules.match_strategy,
        keep_separate=keep_separate,
        page_size=limit,
    )
    # Honesty flag: a partial events fan-out failure silently drops the failed
    # schema's entries, so a short grid would otherwise read as "nothing
    # scheduled". False whenever ANY targeted schema errored (core calendar
    # tables exist in every calendar butler, so a failure is genuine — never a
    # legitimately-absent table).
    entries_source_available = not workspace_failed
    source_rows, sources_failed = await _fetch_sources(
        db,
        lane=view,
        butlers=butlers,
        sources=sources,
    )
    # Honesty flag: a partial calendar_sources fan-out failure silently drops the
    # failed schema's sources, so source_freshness would otherwise read as a complete
    # all-clear. calendar_sources is a core calendar table present in every calendar
    # butler, so a failure is genuine — never a legitimately-absent table (bu-sn71y).
    sources_available = not sources_failed
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

    # Fan-out returns physical copies of a logical provider source from every
    # calendar-owning schema. Collapse only this dashboard view, preferring a
    # fresh owner that exposes calendar_force_sync over first-row-wins order.
    deduped_source_rows = _select_canonical_source_rows(
        source_rows,
        core_calendar_butlers=_calendar_core_enabled_butlers(),
    )
    source_freshness = [_to_source_freshness(row) for row in deduped_source_rows]
    source_freshness = await _with_mirror_health(db, source_freshness, butlers=butlers)
    # Keep each entry paired with its source row so the keyset cursor can be
    # derived from the raw (un-tz-converted) ``instance_starts_at`` + id.
    pairs: list[tuple[dict[str, Any], UnifiedCalendarEntry]] = []
    for row in workspace_rows:
        try:
            pairs.append((row, _normalize_entry(row, view=view, display_tz=display_tz)))
        except ValueError:
            continue

    # Keyset pagination: ``has_more`` and the cursor come from the raw page (before dedup, the
    # bad-timestamp drop and suppression), so a page that shrinks still reports the rows behind
    # it. The cursor encodes the last raw row's ``(instance_starts_at, instance_id)``.
    raw_page = pairs
    next_cursor: str | None = None
    if has_more and last_keyset is not None:
        next_cursor = encode_cursor(last_keyset, scope="calendar-workspace")

    # A title repeated more than the noise threshold on one display-timezone day is held out of
    # ``entries`` and disclosed, with its entries, in ``suppressed_groups``.
    day_title_groups: dict[tuple[str, str], list[UnifiedCalendarEntry]] = defaultdict(list)
    for _row, entry in raw_page:
        day_title_groups[(entry.start_at.date().isoformat(), entry.title)].append(entry)
    noisy = {k for k, v in day_title_groups.items() if len(v) > _WORKSPACE_NOISY_GROUP_SIZE}
    page = [(r, e) for r, e in raw_page if (e.start_at.date().isoformat(), e.title) not in noisy]
    suppressed_groups = [
        CalendarSuppressedGroup(
            title=title,
            day=day,
            count=len(day_title_groups[(day, title)]),
            entries=day_title_groups[(day, title)],
        )
        for (day, title) in sorted(noisy)
    ]

    # Hydrate linked people (bu-qs64f) for the events on THIS page only — one
    # batch-join over calendar_event_entities → public.entities, keyed by
    # event_id, scoped to exactly the schemas that produced the page rows (their
    # db_butler). No N+1 per entry; a resolution failure sets
    # ``people_source_available=false`` rather than silently dropping people.
    page_event_ids: list[UUID] = []
    page_schemas: set[str] = set()
    for row, entry in page:
        db_butler = row.get("db_butler")
        if isinstance(db_butler, str) and db_butler:
            page_schemas.add(db_butler)
        if isinstance(entry.event_id, UUID):
            page_event_ids.append(entry.event_id)
    people_source_available = True
    if page_event_ids:
        people_result = await query_calendar_entry_people(
            db,
            event_ids=page_event_ids,
            butlers=sorted(page_schemas) or None,
        )
        people_source_available = people_result.available
        for _row, entry in page:
            if not isinstance(entry.event_id, UUID):
                continue
            entry.attendees_unavailable = entry.event_id in people_result.attendees_unavailable
            entry.unresolved_attendees = people_result.unresolved.get(entry.event_id, 0)
            resolved = people_result.by_event.get(entry.event_id)
            if resolved:
                entry.linked_people = [
                    CalendarLinkedPerson(entity_id=p.entity_id, display_label=p.display_label)
                    for p in resolved
                ]

    data = CalendarWorkspaceReadResponse(
        entries=[entry for _row, entry in page],
        suppressed_groups=suppressed_groups,
        source_freshness=source_freshness,
        lanes=_build_lane_definitions(source_freshness),
        next_cursor=next_cursor,
        has_more=has_more,
        people_source_available=people_source_available,
        entries_source_available=entries_source_available,
        sources_available=sources_available,
    )
    return ApiResponse[CalendarWorkspaceReadResponse](data=data)


# ---------------------------------------------------------------------------
# Conflict & overcommitment radar — GET /api/calendar/workspace/conflicts
# ---------------------------------------------------------------------------


@router.get("/invitations", response_model=ApiResponse[CalendarInvitationsResponse])
async def get_workspace_invitations(
    start: datetime = Query(...),
    end: datetime = Query(...),
    timezone: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=2048),
    *,
    db: Db,
) -> ApiResponse[CalendarInvitationsResponse]:
    """Read only explicit unanswered projection truth in the visible window."""
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(400, "start and end must include a timezone")
    if end <= start or end - start > _WORKSPACE_MAX_RANGE:
        raise HTTPException(400, "Range must be ordered and at most 90 days")
    try:
        display_tz = ZoneInfo(timezone) if timezone is not None else None
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(400, "Invalid timezone") from exc

    # The cursor carries the window it was minted for, so replaying it against a
    # different window is a 400 rather than a seek into the wrong range.
    window = (start.isoformat(), end.isoformat(), timezone)
    position = None
    if cursor is not None:
        starts_at, instance_id, *cursor_window = decode_cursor(
            cursor, (datetime, UUID, str, str, str | None), scope="calendar-invitations"
        )
        if tuple(cursor_window) != window:
            raise InvalidCursorError()
        position = (starts_at, instance_id)
    invitation_read = await query_calendar_invitations(
        db,
        start=start,
        end=end,
        cursor=position,
        limit=limit + 1,
    )
    degraded = set(invitation_read.failed_butlers)
    if not invitation_read.status_available:
        degraded.add("calendar invitation status")
    try:
        source_rows, source_failed = await _fetch_sources(db, lane="user")
        degraded.update(source_failed)
        # The event survivor and logical source owner have independent freshness
        # rankings. A response must use the schema containing this exact entry.
        owners = {
            (row.get("db_butler"), row["source_key"]): row.get("db_butler") for row in source_rows
        }
    except Exception:
        owners = {}
        degraded.add("calendar sources")
    try:
        scan = await query_calendar_conflicts(db, start=start, end=end, display_tz=display_tz)
    except Exception:
        scan = None
    conflicts_available = scan is not None and scan.available
    if not conflicts_available:
        degraded.add("calendar conflict scan")
    observed_issues = scan.issues if scan is not None else []
    response_owners = _calendar_response_enabled_butlers()
    entries = []
    for row in invitation_read.rows[:limit]:
        owner = owners.get((row.get("db_butler"), row["source_key"]))
        if not owner:
            degraded.add("calendar source ownership")
        metadata = _normalize_json_object(row.get("event_metadata"))
        organizer = metadata.get("organizer")
        organizer_source = "event"
        if not isinstance(organizer, str) or not organizer.strip():
            organizer = None
            organizer_source = "unknown"
            for attendee in metadata.get("attendees", []):
                if not isinstance(attendee, Mapping) or attendee.get("organizer") is not True:
                    continue
                label = (
                    attendee.get("display_name")
                    or attendee.get("displayName")
                    or attendee.get("email")
                )
                if isinstance(label, str) and label.strip():
                    organizer, organizer_source = label.strip(), "attendee"
                    break
        entry_id = str(row["instance_id"])
        issues = [
            ConflictIssue(
                kind=issue.kind,
                date=issue.date,
                summary=issue.summary,
                severity=issue.severity,
                events=[ConflictEventRef(**vars(ref)) for ref in issue.events],
                proposal_ids=list(issue.proposal_ids),
            )
            for issue in observed_issues
            if any(ref.entry_id == entry_id for ref in issue.events)
        ]
        entries.append(
            CalendarInvitationEntry(
                entry_id=row["instance_id"],
                title=row.get("title") or "Untitled",
                start_at=row["instance_starts_at"],
                end_at=row["instance_ends_at"],
                timezone=row.get("instance_timezone") or row.get("event_timezone") or "UTC",
                organizer=organizer.strip() if isinstance(organizer, str) else None,
                organizer_source=organizer_source,
                butler_name=owner,
                conflict_issues=issues,
                response_configured=owner in response_owners and row.get("writable") is True,
            )
        )
    quarantined = []
    for row in invitation_read.quarantined:
        metadata = _normalize_json_object(row.get("event_metadata"))
        verdict = metadata.get("organizer_verdict")
        if verdict not in ("first_contact", "blocked"):
            # An unreadable or missing verdict quarantines as ``unknown`` and degrades the
            # envelope: the system cannot say the organizer is trustworthy or not.
            verdict = "unknown"
            degraded.add("calendar organizer verdict")
        badges = metadata.get("lure_badges")
        quarantined.append(
            CalendarQuarantinedInvitation(
                entry_id=row["instance_id"],
                start_at=row["instance_starts_at"],
                end_at=row["instance_ends_at"],
                timezone=row.get("instance_timezone") or row.get("event_timezone") or "UTC",
                verdict=verdict,
                lure_badges=[b for b in badges if isinstance(b, str)]
                if isinstance(badges, list)
                else [],
            )
        )
    has_more = len(invitation_read.rows) > limit
    next_cursor = None
    if has_more:
        last = invitation_read.rows[limit - 1]
        next_cursor = encode_cursor(
            (last["instance_starts_at"], last["instance_id"], *window), scope="calendar-invitations"
        )
    return ApiResponse(
        data=CalendarInvitationsResponse(
            entries=entries,
            quarantined=quarantined,
            issues_available=not degraded,
            conflicts_available=conflicts_available,
            sources_degraded=sorted(degraded),
            has_more=has_more,
            next_cursor=next_cursor,
        )
    )


@router.get("/conflicts", response_model=ApiResponse[ConflictScanResponse])
async def get_workspace_conflicts(
    start: datetime = Query(..., description="Inclusive ISO-8601 range start"),
    end: datetime = Query(..., description="Exclusive ISO-8601 range end"),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butler_name: str | None = Query(None, description="Optional single butler-schema filter"),
    back_to_back_gap_minutes: int = Query(
        15, ge=0, le=240, description="Gap below which adjacent events are 'back-to-back'"
    ),
    overloaded_day_hours: float = Query(
        6.0, gt=0, le=24, description="Daily meeting-hours budget before a day is 'overloaded'"
    ),
    *,
    db: Db,
) -> ApiResponse[ConflictScanResponse]:
    """Proactively scan the forward window for scheduling problems.

    Deterministic and read-only: queries the synced ``calendar_event_instances``
    / ``calendar_events`` tables (GIST(tstzrange)-served window) for overlaps,
    back-to-back density, and overloaded days — no provider API call, no LLM call
    at request time. ``proposal_ids`` lists any ``pending`` fix proposals already
    emitted for an overlap (empty until the fix-proposal session has run).

    Fails open: a DB fan-out failure yields ``issues_available=false`` with an
    empty issue list, never an HTTP 500. The window must satisfy
    ``end > start`` and ``end - start <= 90 days`` (HTTP 400 otherwise).
    """
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

    window = ConflictScanWindow(start=start, end=end)
    butlers = [butler_name] if butler_name else None

    try:
        scan = await query_calendar_conflicts(
            db,
            start=start,
            end=end,
            butlers=butlers,
            display_tz=display_tz,
            back_to_back_gap_minutes=back_to_back_gap_minutes,
            overloaded_day_hours=overloaded_day_hours,
        )
    except Exception:
        logger.warning("get_workspace_conflicts scan failed; degrading", exc_info=True)
        return ApiResponse[ConflictScanResponse](
            data=ConflictScanResponse(issues=[], scan_window=window, issues_available=False)
        )

    issues = [
        ConflictIssue(
            kind=issue.kind,  # type: ignore[arg-type]
            date=issue.date,
            summary=issue.summary,
            severity=issue.severity,  # type: ignore[arg-type]
            events=[
                ConflictEventRef(
                    entry_id=ref.entry_id,
                    title=ref.title,
                    start_at=ref.start_at,
                    end_at=ref.end_at,
                    timezone=ref.timezone,
                    status=ref.status,
                )
                for ref in issue.events
            ],
            proposal_ids=list(issue.proposal_ids),
            options=[
                ResolutionOption(
                    id=o.id,
                    kind=o.kind,
                    authority=o.authority,
                    exact_call=o.exact_call,
                    rationale_fields=o.rationale_fields,
                )
                for o in issue.options
            ],
            issue_digest=issue.issue_digest,
            options_reason=issue.options_reason,
        )
        for issue in scan.issues
    ]
    return ApiResponse[ConflictScanResponse](
        data=ConflictScanResponse(
            issues=issues,
            scan_window=window,
            issues_available=scan.available,
            unclassified_sources=scan.unclassified_sources,
            unclassified_events=scan.unclassified_events,
            household_context=[_household_ref(event) for event in scan.household_context],
        )
    )


def _require_resolution_live() -> None:
    """Dark: the route does not exist yet, so it answers 404 before it validates its body."""
    if not resolution_options_live():
        raise HTTPException(status_code=404, detail="conflict_resolution_unavailable")


@router.post(
    "/conflicts/resolve",
    response_model=ApiResponse[ConflictResolveResponse],
    dependencies=[Depends(_require_resolution_live)],
)
async def resolve_workspace_conflict(
    body: ConflictResolveRequest,
    *,
    db: Db,
) -> ApiResponse[ConflictResolveResponse]:
    """Record "keep both" for one overlap, bound to the digest the owner saw.

    Writes only the local acknowledgement: no provider call and no approval, so it is the one
    option that needs no park. The overlap is re-read here and its digest compared, so an
    acknowledgement can never attach to a changed overlap (409 ``issue_changed``). Options that
    write to a provider are not accepted by this route yet (422).
    """
    if body.end <= body.start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if body.end - body.start > _WORKSPACE_MAX_RANGE:
        raise HTTPException(status_code=400, detail="Requested range exceeds 90 days")

    scan = await query_calendar_conflicts(db, start=body.start, end=body.end)
    if not scan.available:
        raise HTTPException(status_code=503, detail="conflict_scan_degraded")
    issue = next((i for i in scan.issues if str(i.pair_id) == body.pair_id), None)
    if issue is None:
        if (await load_conflict_acknowledgements(db)).get(body.pair_id, ("", ""))[
            0
        ] == body.issue_digest:
            return ApiResponse(
                data=ConflictResolveResponse(
                    status="already_acknowledged", pair_id=body.pair_id, option_id=body.option_id
                )
            )
        raise HTTPException(status_code=409, detail="issue_changed")
    if issue.issue_digest != body.issue_digest:
        raise HTTPException(status_code=409, detail="issue_changed")
    option = next((o for o in issue.options if o.id == body.option_id), None)
    if option is None:
        raise HTTPException(status_code=422, detail="unknown_option")
    if option.kind != "keep_both":
        raise HTTPException(status_code=422, detail="option_not_supported")
    if not await record_conflict_acknowledgement(
        db,
        pair_id=body.pair_id,
        issue_digest=body.issue_digest,
        state_digest=issue.state_digest or "",
        option_id=option.id,
    ):
        raise HTTPException(status_code=503, detail="acknowledgement_unavailable")
    await log_audit_entry(
        db,
        "dashboard",
        "calendar.workspace.conflicts.acknowledge",
        {"pair_id": body.pair_id, "option_id": option.id},
    )
    return ApiResponse(
        data=ConflictResolveResponse(
            status="acknowledged", pair_id=body.pair_id, option_id=option.id
        )
    )


def _household_ref(event: HouseholdEvent) -> HouseholdEventRef:
    return HouseholdEventRef(
        role=event.role,  # type: ignore[arg-type]
        subject_entity_id=UUID(event.subject_entity_id) if event.subject_entity_id else None,
        subject_label=event.subject_label,
        start_at=event.starts_at,
        end_at=event.ends_at,
        all_day=event.all_day,
    )


@router.get("/changes", response_model=ApiResponse[CalendarChangesResponse])
async def get_workspace_changes(
    since: datetime | None = Query(
        None, description="Return changes observed after this instant (default: seven days ago)"
    ),
    *,
    db: Db,
) -> ApiResponse[CalendarChangesResponse]:
    """Organizer-made changes to start, end, status or location since ``since`` (N3).

    Read from the connector's revision journal through a narrow reader; the owner's own edits and
    events the owner declined are not returned. A failed read answers ``changes_available=false``
    with no items (never an empty "no changes"). ``since`` defaults to seven days ago; if given it
    must carry a timezone, must not be in the future and must be at most 14 days old (HTTP 400
    otherwise): the window is never shortened silently. Only events still ahead are returned, one
    item per organizer change across the owner's calendars, never a baseline or a new invitation;
    ``truncated`` says more exist than the cap.
    """
    now = datetime.now(UTC)
    if since is None:
        since = now - timedelta(days=7)
    if since.tzinfo is None:
        raise HTTPException(status_code=400, detail="since must include a timezone")
    if since > now:
        raise HTTPException(status_code=400, detail="since must not be in the future")
    if now - since > CHANGES_MAX_AGE:
        raise HTTPException(status_code=400, detail="since is older than 14 days")
    read = await query_calendar_changes(db, since=since)
    return ApiResponse[CalendarChangesResponse](
        data=CalendarChangesResponse(
            changes=[
                CalendarChange(
                    event_id=c.event_id,
                    calendar_id=c.calendar_id,
                    entry_id=UUID(c.entry_id) if c.entry_id else None,
                    title=c.title,
                    observed_at=c.observed_at,
                    prior_known=c.prior_known,
                    all_day=c.all_day,
                    fields=c.fields,
                    prior_start_at=c.prior_start,
                    new_start_at=c.new_start,
                    prior_end_at=c.prior_end,
                    new_end_at=c.new_end,
                    prior_status=c.prior_status,
                    new_status=c.new_status,
                    prior_location=c.prior_location,
                    new_location=c.new_location,
                    organizer_self=c.organizer_self,
                )
                for c in read.changes
            ],
            changes_available=read.available,
            truncated=read.truncated,
            since=since,
        )
    )


@router.get("/household-time", response_model=ApiResponse[HouseholdTimeResponse])
async def get_household_time(
    start: datetime = Query(..., description="Inclusive ISO-8601 range start"),
    end: datetime = Query(..., description="Exclusive ISO-8601 range end"),
    butler_name: str | None = Query(None, description="Optional single butler-schema filter"),
    *,
    db: Db,
) -> ApiResponse[HouseholdTimeResponse]:
    """Other people's events in the window, grouped by whose calendar, for context only.

    Opaque by construction: time window, role and subject, with no title, location or
    attendee. Nothing here counts toward owner load, and an unclassified source contributes
    nothing. Fails open: a failed read returns ``available=false``, never HTTP 500. The window
    must satisfy ``end > start`` and ``end - start <= 90 days`` (HTTP 400 otherwise).
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if end - start > _WORKSPACE_MAX_RANGE:
        raise HTTPException(status_code=400, detail="Requested range exceeds 90 days")
    window = ConflictScanWindow(start=start, end=end)
    read = await query_household_time(
        db, start=start, end=end, butlers=[butler_name] if butler_name else None
    )
    groups: dict[str | None, HouseholdSubjectGroup] = {}
    for event in read.events:
        group = groups.setdefault(
            event.subject_entity_id,
            HouseholdSubjectGroup(
                subject_entity_id=UUID(event.subject_entity_id)
                if event.subject_entity_id
                else None,
                subject_label=event.subject_label,
            ),
        )
        group.events.append(_household_ref(event))
    return ApiResponse[HouseholdTimeResponse](
        data=HouseholdTimeResponse(
            window=window, subjects=list(groups.values()), available=read.available
        )
    )


_DAY_SHEET_MIN_YEAR = 1900
_DAY_SHEET_MAX_YEAR = 2200


@router.get("/day-sheet", response_model=ApiResponse[DaySheetResponse])
async def get_day_sheet(
    date: date_type | None = Query(
        None, description="Owner-local calendar day (YYYY-MM-DD); defaults to today"
    ),
    timezone: str | None = Query(
        None, description="IANA zone to read the day in; defaults to the owner's zone"
    ),
    *,
    db: Db,
) -> ApiResponse[DaySheetResponse]:
    """One deterministic projection of an owner-local day (REQ-module-calendar-036).

    Never says a day is clear when it cannot know: an unreadable calendar schema or a stale sync
    is reported in ``freshness`` (and in ``text``) instead of an empty list. Read-only, no model.
    """
    if timezone is not None:
        try:
            zone = ZoneInfo(timezone.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc
    else:
        zone = await owner_zoneinfo(_calendar_accounts_shared_pool(db))
    local_date = date or datetime.now(zone).date()
    if not _DAY_SHEET_MIN_YEAR <= local_date.year <= _DAY_SHEET_MAX_YEAR:
        raise HTTPException(
            status_code=400,
            detail=f"date must be within {_DAY_SHEET_MIN_YEAR}-{_DAY_SHEET_MAX_YEAR}",
        )

    sheet = await query_calendar_day_sheet(db, local_date=local_date, zone=zone)
    return ApiResponse[DaySheetResponse](
        data=DaySheetResponse(
            date=sheet.date,
            zone=sheet.zone,
            freshness=DaySheetFreshness(
                state=sheet.freshness.state,
                as_of=sheet.freshness.as_of,
                sources_unreadable=sheet.freshness.sources_unreadable,
                sources_total=sheet.freshness.sources_total,
            ),
            items=[
                DaySheetItem(
                    ref=i.ref,
                    start=i.start,
                    end=i.end,
                    all_day=i.all_day,
                    kind=i.kind,
                    title=i.title,
                    label_class=i.label_class,
                    people_count=i.people_count,
                    conflict_ref=i.conflict_ref,
                    door=i.door,
                )
                for i in sheet.items
            ],
            gaps=[DaySheetGap(start=g.start, end=g.end) for g in sheet.gaps],
            attended_minutes=sheet.load.attended_minutes,
            attended_count=sheet.load.count,
            longest_gap=(
                DaySheetGap(start=sheet.longest_gap.start, end=sheet.longest_gap.end)
                if sheet.longest_gap
                else None
            ),
            digest=sheet.digest,
            text=render_day_sheet_text(sheet),
        )
    )


def _now() -> datetime:
    """The clock the verdict route reads; a seam so tests can pin the 18:00 rollover."""
    return datetime.now(UTC)


#: Local hour from which an unspecified verdict day is tomorrow: tonight's meetings are behind you.
_VERDICT_ROLLOVER_HOUR = 18


def _verdict_entry(item) -> DayVerdictEntry | None:
    if item is None:
        return None
    return DayVerdictEntry(
        ref=item.ref,
        start=item.start,
        end=item.end,
        title=item.title,
        people_count=item.people_count,
        door=item.door,
    )


@router.get("/day-verdict", response_model=ApiResponse[DayVerdictResponse])
async def get_day_verdict(
    date: date_type | None = Query(
        None,
        description="Owner-local day (YYYY-MM-DD); defaults to today, or tomorrow from 18:00 local",
    ),
    timezone: str | None = Query(
        None, description="IANA zone to read the day in; defaults to the owner's zone"
    ),
    *,
    db: Db,
) -> ApiResponse[DayVerdictResponse]:
    """The next commitment, committed hours and longest free window (REQ-module-calendar-051).

    Derived from the same projection as ``/day-sheet``. An unreadable calendar returns
    ``available=false`` rather than an empty or clear day. Read-only, no model.
    """
    if timezone is not None:
        try:
            zone = ZoneInfo(timezone.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc
    else:
        zone = await owner_zoneinfo(_calendar_accounts_shared_pool(db))
    now = _now().astimezone(zone)
    today = now.date()
    local_date = date or (
        today + timedelta(days=1) if now.hour >= _VERDICT_ROLLOVER_HOUR else today
    )
    if not _DAY_SHEET_MIN_YEAR <= local_date.year <= _DAY_SHEET_MAX_YEAR:
        raise HTTPException(
            status_code=400,
            detail=f"date must be within {_DAY_SHEET_MIN_YEAR}-{_DAY_SHEET_MAX_YEAR}",
        )

    verdict = await query_calendar_day_verdict(db, local_date=local_date, zone=zone, now=now)
    free = verdict.free_window
    return ApiResponse[DayVerdictResponse](
        data=DayVerdictResponse(
            available=verdict.available,
            date=local_date,
            zone=verdict.zone,
            relative=(
                "today"
                if local_date == today
                else "tomorrow"
                if local_date == today + timedelta(days=1)
                else "other"
            ),
            freshness=DaySheetFreshness(
                state=verdict.freshness.state,
                as_of=verdict.freshness.as_of,
                sources_unreadable=verdict.freshness.sources_unreadable,
                sources_total=verdict.freshness.sources_total,
            ),
            current=_verdict_entry(verdict.current),
            next=_verdict_entry(verdict.next),
            committed_minutes=verdict.committed_minutes,
            committed_count=verdict.committed_count,
            tentative_count=verdict.tentative_count,
            free_window=(
                DayVerdictWindow(
                    start=free.start,
                    end=free.end,
                    minutes=int((free.end.timestamp() - free.start.timestamp()) // 60),
                )
                if free is not None
                else None
            ),
            empty=verdict.empty,
        )
    )


@router.get("/search", response_model=ApiResponse[CalendarWorkspaceSearchResponse])
async def search_workspace(
    q: str = Query("", description="Free-text query over title/description/location"),
    view: str = Query("user", pattern="^(user|butler)$"),
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    butlers: list[str] | None = Query(None, description="Optional butler-name filters"),
    sources: list[str] | None = Query(None, description="Optional source_key filters"),
    limit: int = Query(50, ge=1, le=200, description="Max matches to return"),
    *,
    db: Db,
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
    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    if not q.strip():
        return ApiResponse[CalendarWorkspaceSearchResponse](
            data=CalendarWorkspaceSearchResponse(entries=[], available=True)
        )

    results = await query_calendar_event_search(
        db,
        q=q,
        view=view,
        butlers=butlers,
        sources=sources,
        limit=limit,
    )

    entries: list[UnifiedCalendarEntry] = []
    for match in results.matches:
        row = shallow_asdict(match.row)
        try:
            entries.append(_normalize_entry(row, view=view, display_tz=display_tz))
        except ValueError:
            continue

    data = CalendarWorkspaceSearchResponse(entries=entries, available=results.available)
    return ApiResponse[CalendarWorkspaceSearchResponse](data=data)


@router.get("/entries/{entry_id}", response_model=ApiResponse[UnifiedCalendarEntry])
async def get_entry_detail(
    entry_id: UUID,
    timezone: str | None = Query(None, description="Optional display timezone (IANA)"),
    *,
    db: Db,
) -> ApiResponse[UnifiedCalendarEntry]:
    """Fetch a single calendar workspace entry by instance ID."""
    display_tz: ZoneInfo | None = None
    if timezone is not None:
        try:
            display_tz = ZoneInfo(timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}") from exc

    dto, lookup_failed = await query_calendar_workspace_entry(db, entry_id=entry_id)
    if dto is None:
        if lookup_failed:
            # The entry may live in a schema whose fan-out query FAILED — a 404 would
            # dishonestly claim "does not exist" when the lookup was degraded (bu-sn71y).
            raise HTTPException(
                status_code=503,
                detail=(
                    "Entry lookup degraded — some calendar sources were unavailable: "
                    f"{', '.join(sorted(lookup_failed))}"
                ),
            )
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

    row = shallow_asdict(dto)
    view = str(row.get("lane") or "user")
    try:
        entry = _normalize_entry(row, view=view, display_tz=display_tz)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Entry has missing timestamps") from exc

    return ApiResponse[UnifiedCalendarEntry](data=entry)


@router.get("/meta", response_model=ApiResponse[CalendarWorkspaceMetaResponse])
async def get_workspace_meta(
    *,
    db: Db,
    mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[CalendarWorkspaceMetaResponse]:
    """Return workspace metadata: capabilities, sources, lanes, writable calendars."""
    source_rows, sources_failed = await _fetch_sources(db)
    # Honesty flag: a partial calendar_sources fan-out failure silently drops the
    # failed schema's sources, so connected_sources would otherwise render a truthful
    # "all sources healthy" all-clear (and the grid's freshness plaque would read
    # fresh). calendar_sources is a core calendar table present in every calendar
    # butler, so a failure is genuine — never a legitimately-absent table (bu-sn71y).
    sources_available = not sources_failed
    canonical_source_rows = _select_canonical_source_rows(
        source_rows,
        core_calendar_butlers=_calendar_core_enabled_butlers(),
    )
    deduped_sources = [_to_source_freshness(row) for row in canonical_source_rows]

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
        sources_available=sources_available,
    )
    return ApiResponse[CalendarWorkspaceMetaResponse](data=data)


@router.put("/primary", response_model=ApiResponse[SetPrimaryCalendarResponse])
async def set_primary_calendar(
    body: SetPrimaryCalendarRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
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


@router.post(
    "/sync",
    response_model=ApiResponse[CalendarWorkspaceSyncResponse],
    status_code=202,
)
async def sync_workspace(
    request: CalendarWorkspaceSyncRequest,
    *,
    db: Db,
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[CalendarWorkspaceSyncResponse]:
    """Queue projection/provider sync for all sources or one selected source.

    The API returns after a butler durably acknowledges its action-log command;
    provider pulls and Google mirror writes run in that butler's queue worker,
    never under the browser's 15-second request budget.
    """
    target_rows: list[dict[str, Any]] = []
    scope = "all"
    request_id = str(uuid4())
    core_calendar_butlers = _calendar_core_enabled_butlers()
    if request.source_key is not None or request.source_id is not None:
        scope = "source"
        # Sync targets only need the source rows; per-target failures already
        # surface as failed CalendarWorkspaceSyncTarget entries (bu-4xdno), so the
        # fan-out degraded list is not a separate signal here.
        source_rows, _ = await _fetch_sources(
            db,
            butlers=[request.butler] if request.butler else None,
            sources=[request.source_key] if request.source_key else None,
        )
        if request.source_id is not None:
            source_rows = [row for row in source_rows if row.get("source_id") == request.source_id]
        if not source_rows:
            raise HTTPException(status_code=404, detail="Requested source was not found")
        # A source-key-only request is logical rather than physical, so choose
        # its operational owner. Explicit source IDs and explicit butlers retain
        # the caller's exact schema target for recovery/debugging.
        target_rows = (
            _select_canonical_source_rows(
                source_rows,
                core_calendar_butlers=core_calendar_butlers,
            )
            if request.source_id is None and request.butler is None
            else source_rows
        )
    else:
        source_rows, _ = await _fetch_sources(
            db,
            butlers=[request.butler] if request.butler else None,
        )
        if request.butler and request.butler not in db.butler_names:
            raise HTTPException(status_code=404, detail=f"Unknown butler: {request.butler}")
        # One owner-wide no-id command performs the module's pull-all sync. Do
        # not submit every replicated source/calendar row: that was the 48-call
        # fan-out that rate-limited Google and exceeded the browser timeout.
        seen_owners: set[str] = set()
        for row in _select_canonical_source_rows(
            source_rows,
            core_calendar_butlers=core_calendar_butlers,
        ):
            if row.get("source_kind") != "provider_event" or not row.get("calendar_id"):
                continue
            owner = str(row["db_butler"])
            if owner not in core_calendar_butlers:
                continue
            # Skip sources disabled via POST /api/calendar/sources — a disabled
            # source is off and must not be polled by the sync loop. An explicit
            # single-source sync (scope="source") bypasses this filter so an
            # operator can still recover a specific source on demand.
            if _normalize_json_object(row.get("source_metadata")).get("sync_enabled") is False:
                continue
            if owner in seen_owners:
                continue
            seen_owners.add(owner)
            target_rows.append(row)

    async def _sync_target(
        *,
        butler_name: str,
        call_args: dict[str, Any],
        source_key: str | None = None,
        calendar_id: str | None = None,
    ) -> CalendarWorkspaceSyncTarget:
        # The module records this request before returning. ``full=False``
        # preserves incremental behavior; queue=true changes only transport
        # lifetime, not the sync semantics.
        forwarded_args = {
            **call_args,
            "full": request.full,
            "queue": True,
            "request_id": request_id,
        }
        try:
            client = await mcp_manager.get_client(butler_name)
            result = await client.call_tool("calendar_force_sync", forwarded_args)
            parsed = tool_result_payload(result)
            raw_status = parsed.get("status", "queued") if isinstance(parsed, dict) else "queued"
            status = "failed" if raw_status in {"error", "failed"} else str(raw_status)
            recovery = bool(parsed.get("recovery")) if isinstance(parsed, dict) else False
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status=status,
                detail=_sync_detail(parsed),
                error=(
                    str(parsed.get("error"))
                    if isinstance(parsed, dict) and parsed.get("error")
                    else None
                ),
                recovery=recovery,
                request_id=(
                    str(parsed.get("request_id"))
                    if isinstance(parsed, dict) and parsed.get("request_id")
                    else request_id
                ),
                coalesced=bool(parsed.get("coalesced")) if isinstance(parsed, dict) else False,
            )
        except ButlerUnreachableError as exc:
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status="failed",
                error=str(exc),
            )
        except ToolError as exc:
            # calendar_force_sync is not registered on this butler (its calendar
            # module does not expose the 'core' tool group). Degrade to a failed
            # target instead of 500-ing the entire fan-out.
            return CalendarWorkspaceSyncTarget(
                butler_name=butler_name,
                source_key=source_key,
                calendar_id=calendar_id,
                status="failed",
                error=f"calendar_force_sync unavailable: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            # One failing target must never blow up the aggregate sync response.
            logger.warning("calendar sync target failed for butler %s", butler_name, exc_info=True)
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
                        call_args={},
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
        request_id=request_id,
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
    *,
    closed_error: bool = False,
) -> dict[str, Any]:
    """Call a butler MCP tool and coerce response content into a dict payload."""
    try:
        client = await mgr.get_client(butler_name)
        result = await client.call_tool(tool_name, arguments)
    except ButlerUnreachableError as exc:
        if closed_error:
            raise HTTPException(status_code=503, detail="response_transport_unavailable") from None
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{butler_name}' is unreachable: {exc}",
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive transport fallback
        if closed_error:
            raise HTTPException(status_code=503, detail="response_transport_unavailable") from None
        logger.exception(
            "Unexpected MCP call failure for butler '%s' tool '%s'",
            butler_name,
            tool_name,
        )
        raise HTTPException(
            status_code=503,
            detail=f"MCP call to '{butler_name}' failed: {exc}",
        ) from exc

    parsed = tool_result_payload(result)
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


def _quick_add_shared_authority_pool(db: DatabaseManager):
    """Return quick-add's authoritative shared credential pool or raise 503.

    The quick-add parse resolves its model from ``public.model_catalog`` via the
    shared credential pool — the same pool the model-settings surface uses. It
    must not fall back to a schema-local pool because that pool cannot be
    promoted to a system-global Codex credential authority.
    """
    try:
        return db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        ) from exc


@router.post("/parse-quick-add", response_model=ApiResponse[QuickAddParseResponse])
async def parse_quick_add_event(
    body: QuickAddParseRequest,
    *,
    db: Db,
) -> ApiResponse[QuickAddParseResponse]:
    """Parse a natural-language string into a draft event for confirmation.

    Parse-only and read-only: this endpoint never creates a calendar event and
    performs no provider or projection write. It intentionally takes no MCP
    client dependency — there is structurally no write path. Event creation
    flows exclusively through ``POST /api/calendar/workspace/user-events`` on
    confirm, with a fresh ``request_id``.

    Degraded contract: when no cheap-tier model is configured (``resolve_model``
    returns ``None``) or the model output cannot be interpreted as a single
    event draft, the response is HTTP 200 with ``parse_available=false``, a
    human-readable ``reason``, and no ``draft`` — never a fabricated event.
    Blank input is rejected at the model boundary (HTTP 422).
    """
    pool = _quick_add_shared_authority_pool(db)
    outcome = await parse_quick_add(
        pool,
        text=body.text,
        butler_name=body.butler_name,
        timezone=body.timezone,
        now_iso=body.now,
        codex_auth_authority=CredentialStore(pool, system_global_pool=pool),
    )
    draft = QuickAddDraft.model_validate(outcome.draft) if outcome.draft is not None else None
    return ApiResponse[QuickAddParseResponse](
        data=QuickAddParseResponse(
            parse_available=outcome.parse_available,
            draft=draft,
            reason=outcome.reason,
        )
    )


async def _calendar_response_review_pool(
    db: DatabaseManager, action_id: UUID, owner: str
) -> tuple[Any, dict[str, Any]]:
    """Authenticate read selection by complete own-source and command binding.

    This read selector is not decision or execution authority. Messenger's
    separate notification-review discriminator keeps its existing admission.
    """
    from butlers.modules.calendar_response import _digest

    names = db.configured_butlers_with_module("approvals")
    if (
        not isinstance(names, list)
        or not names
        or len(names) > 64
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
        or owner not in names
    ):
        raise HTTPException(status_code=503, detail="response_review_census_unavailable")
    rows, failed = await db.fan_out_with_status(
        "SELECT id FROM pending_actions WHERE id=$1", (action_id,), butler_names=names
    )
    if any(
        len(values) > 1 or any(row.get("id") != action_id for row in values)
        for values in rows.values()
    ):
        raise HTTPException(status_code=409, detail="response_review_owner_unverified")
    matches = [name for name, values in rows.items() if values]
    if failed:
        raise HTTPException(status_code=503, detail="response_review_lookup_degraded")
    if set(rows) != set(names) or len(matches) != 1 or matches[0] != owner:
        raise HTTPException(status_code=409, detail="response_review_owner_unverified")
    pool = db.pool(owner)
    try:
        command_row = await pool.fetchrow(
            "SELECT action_type,action_payload FROM calendar_action_log WHERE id=$1", action_id
        )
        pending = await pool.fetchrow(
            "SELECT tool_name,tool_args FROM pending_actions WHERE id=$1", action_id
        )
    except Exception:
        raise HTTPException(status_code=503, detail="response_review_unavailable") from None
    payload = _normalize_json_object(command_row["action_payload"]) if command_row else {}
    command = _normalize_json_object(payload.get("command"))
    args = _normalize_json_object(pending["tool_args"]) if pending else {}
    expected = {
        **_normalize_json_object(command.get("arguments")),
        "_command_id": str(action_id),
        "_command_digest": payload.get("digest"),
    }
    if (
        not command_row
        or command_row["action_type"] != "workspace_user_respond"
        or command.get("owner") != owner
        or _digest(command) != payload.get("digest")
        or not pending
        or pending["tool_name"] != "calendar_respond"
        or args != expected
    ):
        raise HTTPException(status_code=409, detail="response_review_binding_unverified")
    # Carry the admitted immutable binding through the later dossier read.
    # That read uses another acquisition and must not display a changed action.
    return pool, expected


async def _read_response_command(
    db: DatabaseManager, command_id: UUID, owner: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Repeat the admitted immutable binding on the final receipt acquisition."""
    from butlers.modules.calendar_response import _digest

    pool, expected = await _calendar_response_review_pool(db, command_id, owner)
    try:
        row = await pool.fetchrow(
            "SELECT action_type,action_payload,action_result FROM calendar_action_log WHERE id=$1",
            command_id,
        )
        pending = await pool.fetchrow(
            "SELECT tool_name,tool_args,status FROM pending_actions WHERE id=$1", command_id
        )
    except Exception:
        raise HTTPException(503, "response_review_unavailable") from None
    if row is None or pending is None:
        raise HTTPException(503, "response_review_unavailable")
    payload = _normalize_json_object(row["action_payload"])
    command = _normalize_json_object(payload.get("command"))
    if (
        row["action_type"] != "workspace_user_respond"
        or command.get("owner") != owner
        or _digest(command) != payload.get("digest")
        or {
            **_normalize_json_object(command.get("arguments")),
            "_command_id": str(command_id),
            "_command_digest": payload.get("digest"),
        }
        != expected
        or pending["tool_name"] != "calendar_respond"
        or _normalize_json_object(pending["tool_args"]) != expected
    ):
        raise HTTPException(409, "response_review_binding_unverified")
    return payload, _normalize_json_object(row["action_result"]), pending["status"]


@router.get("/responses/{command_id}", response_model=ApiResponse[CalendarRespondResponse])
async def get_response_receipt(
    command_id: UUID,
    source_butler: str = Query(..., min_length=1, max_length=64),
    inverse: bool = Query(False),
    *,
    db: Db,
) -> ApiResponse[CalendarRespondResponse]:
    """Read the exact retained response or its server-bound inverse, without dispatch.

    The original identity survives a lost undo reply and removal of the inbox
    row. A reservation without a prepared approval is unavailable, never an
    applied receipt or permission to reserve another inverse.
    """
    payload, receipt, decision = await _read_response_command(db, command_id, source_butler)
    if inverse:
        original_id = command_id
        try:
            command_id = UUID(payload["inverse_claim"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(409, "response_inverse_unavailable") from None
        payload, receipt, decision = await _read_response_command(db, command_id, source_butler)
        if _normalize_json_object(payload.get("command")).get("inverse_of") != str(original_id):
            raise HTTPException(409, "response_inverse_binding_unverified")
    status = receipt.get("status")
    reason = receipt.get("reason")
    if not status:
        if payload.get("phase") in {"egress_started", "uncertain"}:
            status = "uncertain"
        elif decision in {"rejected", "expired"}:
            status, reason = "rejected", "response_approval_" + decision
        elif decision in {"pending", "approved"}:
            status = "pending_approval" if decision == "pending" else "approved"
        else:
            raise HTTPException(503, "response_receipt_unavailable")
    return ApiResponse(
        data=CalendarRespondResponse(
            status=status,
            command_id=command_id,
            approval_id=command_id,
            source_butler=source_butler,
            reason=reason,
            projection_available=receipt.get("projection_available") is True,
        )
    )


async def _respond_through_approval(
    *,
    arguments: dict[str, Any],
    butler_name: str,
    http_request: Request,
    mgr: MCPClientManager,
    db: DatabaseManager,
) -> CalendarRespondResponse:
    from butlers.api.audit_emit import authenticated_principal
    from butlers.api.routers.approvals import (
        _decision_actor_id,
        _dispatch_approved_action_outcome,
        approve_owning_action,
    )
    from butlers.modules.calendar_response import _digest

    authenticated_principal()
    result = await _call_mcp_tool(
        mgr,
        butler_name,
        "calendar_respond",
        {
            **arguments,
            "_why": "Owner requested this exact invitation response",
            "_evidence": [],
            "_blast_radius": "self",
            "_reversibility": "compensable",
        },
        closed_error=True,
    )
    if result.get("error"):
        raise HTTPException(status_code=409, detail=result["error"])
    try:
        command_id = UUID(result["command_id"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=503, detail="response_command_unavailable") from None
    pool = db.pool(butler_name)
    command_row = await pool.fetchrow(
        "SELECT action_payload,action_result FROM calendar_action_log WHERE id=$1", command_id
    )
    payload = _normalize_json_object(command_row["action_payload"]) if command_row else {}
    if payload.get("request_digest") != _digest(arguments):
        raise HTTPException(status_code=409, detail="response_command_binding_changed")
    command = _normalize_json_object(payload.get("command"))
    if command.get("owner") != butler_name:
        raise HTTPException(status_code=409, detail="response_command_owner_changed")
    if result["status"] in {"pending_approval", "approved"}:
        pending = await pool.fetchrow(
            "SELECT tool_name,tool_args,status FROM pending_actions WHERE id=$1", command_id
        )
        tool_args = _normalize_json_object(pending["tool_args"]) if pending else {}
        if (
            pending is None
            or pending["tool_name"] != "calendar_respond"
            or tool_args.get("_command_id") != str(command_id)
            or tool_args.get("_command_digest") != payload.get("digest")
        ):
            raise HTTPException(status_code=409, detail="response_approval_binding_changed")
        if pending["status"] == "pending":
            decision = await approve_owning_action(
                parsed_id=command_id,
                actor_id=_decision_actor_id(None, callback_authenticated=False),
                edits=None,
                action_butler=butler_name,
                target_pool=pool,
                db_mgr=db,
                mcp_mgr=mgr,
                expected_tool_args=tool_args,
            )
            result = {
                "status": "approved" if not decision.data.dispatched else "failed",
                "command_id": str(command_id),
            }
        elif pending["status"] in {"rejected", "expired"}:
            result = {"status": "rejected", "command_id": str(command_id)}
        else:
            if pending["status"] == "approved":
                # Recovery dispatches only this existing, exact owning command.
                # The executor/committed attempt fence prevents repeated egress.
                await _dispatch_approved_action_outcome(
                    mgr,
                    db,
                    pool,
                    str(command_id),
                    "calendar_respond",
                    tool_args,
                    butler_name,
                )
            result = {"status": "approved", "command_id": str(command_id)}
    # Dispatch/executor result is not provider attribution. Only the owning
    # durable command receipt, or committed start fence, determines the effect.
    current = await pool.fetchrow(
        "SELECT action_payload,action_result FROM calendar_action_log WHERE id=$1", command_id
    )
    receipt = _normalize_json_object(current["action_result"]) if current else {}
    current_payload = _normalize_json_object(current["action_payload"]) if current else {}
    phase = current_payload.get("phase")
    status = receipt.get("status") or (
        "uncertain" if phase in {"egress_started", "uncertain"} else result["status"]
    )
    return CalendarRespondResponse(
        status=status,
        command_id=command_id,
        approval_id=command_id,
        source_butler=butler_name,
        reason=receipt.get("reason") or result.get("reason"),
        projection_available=receipt.get("projection_available") is True,
    )


@router.post("/organizer-decision", response_model=ApiResponse[CalendarOrganizerDecisionResponse])
async def decide_organizer(
    body: CalendarOrganizerDecisionRequest,
    http_request: Request,
    *,
    db: Db,
) -> ApiResponse[CalendarOrganizerDecisionResponse]:
    """Owner-only Vouch or Block of a stored invitation's organizer (REQ-module-calendar-042).

    The organizer address comes from the stored event. The verb writes calendar-scoped ingestion
    rules (never a global one), then re-stamps that organizer's stored events at once.
    """
    from butlers.api.audit_emit import authenticated_principal, emit_dashboard_audit
    from butlers.modules.calendar.organizer_decisions import (
        NoCalendarEndpoint,
        apply_decision,
        normalize_address,
        restamp_organizer,
    )

    try:
        authenticated_principal()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="owner_principal_required") from exc
    targets = db.configured_butlers_with_module("calendar")
    if targets is None:
        raise HTTPException(status_code=503, detail="organizer_decision_census_unavailable")
    found, failed = await db.fan_out_with_status(
        "SELECT lower(btrim(COALESCE(e.metadata ->> 'organizer', ''))) AS organizer,"
        " lower(btrim(COALESCE(own.email, ''))) AS own_email"
        " FROM calendar_event_instances i JOIN calendar_events e ON e.id = i.event_id"
        " LEFT JOIN LATERAL ("
        "   SELECT att ->> 'email' AS email"
        "   FROM jsonb_array_elements(CASE WHEN jsonb_typeof(e.metadata -> 'attendees') = 'array'"
        "        THEN e.metadata -> 'attendees' ELSE '[]'::jsonb END) AS att"
        "   WHERE att ->> 'self' = 'true' LIMIT 1) AS own ON TRUE"
        " WHERE i.id = $1",
        (body.entry_id,),
        butler_names=targets,
    )
    rows = [row for result in found.values() for row in result]
    if failed:
        raise HTTPException(status_code=503, detail="organizer_decision_lookup_degraded")
    if len(rows) != 1:
        raise HTTPException(status_code=404 if not rows else 409, detail="entry_not_found")
    organizer = normalize_address(rows[0]["organizer"])
    if not organizer or "@" not in organizer:
        raise HTTPException(status_code=409, detail="entry_has_no_organizer")
    if organizer == normalize_address(rows[0]["own_email"]):
        raise HTTPException(status_code=409, detail="organizer_is_the_owner")

    try:
        switchboard = db.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="switchboard_unavailable") from exc
    try:
        decision = await apply_decision(switchboard, organizer, body.verb)
    except NoCalendarEndpoint as exc:
        raise HTTPException(status_code=409, detail="no_calendar_endpoint") from exc

    restamped = 0
    degraded = False
    for name in targets:
        try:
            restamped += await restamp_organizer(db.pool(name), organizer)
        except Exception:
            logger.warning("organizer decision: re-stamp failed for %s", name, exc_info=True)
            degraded = True  # the sweep restamps these rows on its next pass
    await emit_dashboard_audit(
        db,
        butler="switchboard",
        operation="calendar_organizer_decision",
        method="POST",
        path="/api/calendar/workspace/organizer-decision",
        body={
            "verb": body.verb,
            "rules_written": decision.rules_written,
            "rules_removed": decision.rules_removed,
        },
        response_status=200,
        request=http_request,
    )
    return ApiResponse(
        data=CalendarOrganizerDecisionResponse(
            verb=decision.verb,
            endpoints=decision.endpoints,
            rules_written=decision.rules_written,
            rules_removed=decision.rules_removed,
            events_restamped=restamped,
            restamp_degraded=degraded,
        )
    )


@router.post("/respond", response_model=ApiResponse[CalendarRespondResponse])
async def respond_to_invitation(
    body: CalendarRespondRequest,
    http_request: Request,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
) -> ApiResponse[CalendarRespondResponse]:
    """Compose an authenticated owner decision with the exact owning gated tool."""
    targets = db.configured_butlers_with_module("calendar")
    if targets is None:
        raise HTTPException(status_code=503, detail="response_owner_census_unavailable")
    owners, failed = await db.fan_out_with_status(
        "SELECT id FROM calendar_event_instances WHERE id=$1",
        (body.entry_id,),
        butler_names=targets,
    )
    matches = [name for name, rows in owners.items() if rows]
    if failed:
        raise HTTPException(status_code=503, detail="response_owner_lookup_degraded")
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="response_owner_unverified")
    return ApiResponse(
        data=await _respond_through_approval(
            arguments=body.model_dump(mode="json"),
            butler_name=matches[0],
            http_request=http_request,
            mgr=mgr,
            db=db,
        )
    )


@router.post("/user-events", response_model=ApiResponse[CalendarWorkspaceMutationResponse])
async def mutate_user_event(
    body: CalendarWorkspaceUserMutationRequest,
    http_request: Request,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
) -> ApiResponse[CalendarWorkspaceMutationResponse]:
    """Create a user-view provider event (quick-add confirmation) through calendar MCP.

    Side effects: dispatches the owning butler's ``calendar_create_event`` tool
    and records the workspace mutation audit entry. Idempotency: forwarded
    ``request_id`` keys replay handling in the calendar module. Failure: request
    validation rejects any action other than ``create`` with HTTP 422; MCP
    failures retain the existing HTTP error behavior.
    """
    # Owner decision bu-kf3n4i=A / PR4901 retired dashboard update/delete/undo;
    # this composes approval only for the surviving create surface. MCP
    # update/delete and instance handlers retain their own party gates.
    tool_name = "calendar_create_event"

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
        if mutation_result.get("status") == "request_changed":
            raise HTTPException(status_code=409, detail="Queued calendar request changed")
        if mutation_result.get("status") == "approval_required" and isinstance(
            mutation_result.get("args_digest"), str
        ):
            from butlers.api.audit_emit import authenticated_principal
            from butlers.api.routers.approvals import _decision_actor_id, approve_owning_action
            from butlers.modules.approvals.execution_context import approval_tool_args_digest

            # Keep the same provider/account owner. This exact owner submission
            # supplies a decision, never delegated-success promotion evidence.
            authenticated_principal()
            try:
                action_id = UUID(mutation_result["action_id"])
            except (KeyError, ValueError, TypeError):
                raise HTTPException(503, "create_approval_unavailable") from None
            pool = db.pool(body.butler_name)
            pending = await pool.fetchrow(
                "SELECT tool_name,tool_args,status FROM pending_actions WHERE id=$1", action_id
            )
            parked = _normalize_json_object(pending["tool_args"]) if pending else {}
            if (
                pending is None
                or pending["tool_name"] != tool_name
                or approval_tool_args_digest(parked) != mutation_result.get("args_digest")
            ):
                raise HTTPException(409, "create_approval_binding_changed")
            if pending["status"] == "pending":
                decision = await approve_owning_action(
                    parsed_id=action_id,
                    actor_id=_decision_actor_id(None, callback_authenticated=False),
                    edits=None,
                    action_butler=body.butler_name,
                    target_pool=pool,
                    db_mgr=db,
                    mcp_mgr=mgr,
                    expected_tool_args=parked,
                    expected_tool_name=tool_name,
                    promotion_eligible=False,
                )
                mutation_result = {
                    **mutation_result,
                    "status": "executed" if decision.data.dispatched else "approved",
                }
            else:
                mutation_result = {**mutation_result, "status": pending["status"]}
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


def _rrule_cron_lossy_notes(recurrence_rule: str) -> list[str]:
    """Return warnings for RRULE features the cron projection silently drops.

    Butler scheduled-task events project recurrence through
    ``CalendarModule._rrule_to_cron``, which only understands ``FREQ`` plus a
    weekly ``BYDAY`` and a monthly ``BYMONTHDAY``. Every other RRULE component is
    discarded, so the scheduler can fire on dates the author never intended. We
    surface those degradations as quiet notes the dialog renders before saving —
    the mitigation for the lossy-cron footgun.
    """
    components = CalendarModule._rrule_components(recurrence_rule)
    freq = (components.get("FREQ") or "").upper()
    unit = _RRULE_FREQ_UNIT.get(freq, "period")
    notes: list[str] = []

    interval = (components.get("INTERVAL") or "").strip()
    if interval and interval != "1":
        notes.append(
            f"INTERVAL={interval} is not supported by the butler scheduler — "
            f"the series will fire every {unit} instead of every {interval} {unit}s."
        )

    count = (components.get("COUNT") or "").strip()
    if count:
        notes.append(
            f"COUNT={count} is not supported — the series will not auto-stop after "
            f"{count} occurrences (set an 'until' date instead)."
        )

    if (components.get("BYSETPOS") or "").strip():
        notes.append("BYSETPOS is not supported by the butler scheduler and will be ignored.")

    byday = (components.get("BYDAY") or "").strip()
    if byday:
        has_ordinal = any(
            re.match(r"^[+-]?\d", token.strip()) for token in byday.split(",") if token.strip()
        )
        if has_ordinal:
            notes.append(
                "Ordinal BYDAY (e.g. '2MO', '-1FR') is not supported — the scheduler will "
                "fall back to the start date's day-of-month."
            )
        elif freq and freq != "WEEKLY":
            notes.append(
                f"BYDAY is only honoured for WEEKLY rules; on a {freq.title()} rule it is "
                "ignored and the start date's day is used."
            )

    bymonthday = (components.get("BYMONTHDAY") or "").strip()
    if bymonthday and freq and freq != "MONTHLY":
        notes.append(
            f"BYMONTHDAY is only honoured for MONTHLY rules; on a {freq.title()} rule it is "
            "ignored."
        )

    for extra in ("BYMONTH", "BYWEEKNO", "BYYEARDAY", "BYHOUR", "BYMINUTE", "BYSECOND"):
        if not (components.get(extra) or "").strip():
            continue
        # YEARLY rules already encode the month via the start date, so BYMONTH is
        # redundant rather than lossy there.
        if extra == "BYMONTH" and freq == "YEARLY":
            continue
        notes.append(f"{extra} is not supported by the butler scheduler and will be ignored.")

    return notes


def _build_butler_event_preview(
    body: CalendarButlerEventPreviewRequest,
) -> CalendarButlerEventPreviewResponse:
    """Dry-run a draft butler event's recurrence and project its firing dates.

    Reuses the scheduler's own ``_rrule_to_cron`` conversion and croniter
    expansion so the preview reflects exactly what the scheduler would fire —
    including any lossy degradation. Nothing is persisted and no LLM runs.
    """

    def _localize(dt: datetime) -> datetime:
        """Coerce a naive datetime to UTC via the request timezone; fail fast on a bad tz.

        Both ``start_at`` and ``until_at`` flow through here so a naive pair is
        always interpreted in the *same* zone — otherwise a naive ``until_at``
        would silently fall back to UTC while ``start_at`` honoured ``timezone``,
        producing off-by-hours truncation of the window.
        """
        if dt.tzinfo is None:
            if body.timezone:
                try:
                    dt = dt.replace(tzinfo=ZoneInfo(body.timezone))
                except ZoneInfoNotFoundError as exc:
                    raise HTTPException(
                        status_code=422, detail=f"Unknown timezone {body.timezone!r}"
                    ) from exc
            else:
                dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    start = _localize(body.start_at or datetime.now(UTC))

    window_start = start
    window_end = start + _PREVIEW_WINDOW

    until_bound: datetime | None = None
    if body.until_at is not None:
        until_bound = _localize(body.until_at)

    notes: list[str] = []
    if body.cron:
        effective_cron = body.cron.strip()
    else:
        rrule = (body.rrule or "").strip()
        embedded_until = CalendarModule._rrule_until(rrule)
        if embedded_until is not None:
            until_bound = (
                embedded_until if until_bound is None else min(until_bound, embedded_until)
            )
        notes = _rrule_cron_lossy_notes(rrule)
        try:
            effective_cron = CalendarModule._rrule_to_cron(start, rrule)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid recurrence rule: {exc}") from exc

    if until_bound is not None and until_bound < window_end:
        window_end = until_bound

    if window_end < window_start:
        return CalendarButlerEventPreviewResponse(
            occurrences=[],
            total_in_window=0,
            more_count=0,
            window_start=window_start,
            window_end=window_end,
            effective_cron=effective_cron,
            notes=notes,
        )

    try:
        pairs = _cron_occurrences_in_window(
            effective_cron, window_start, window_end, body.duration_minutes
        )
    except ValueError as exc:
        # croniter's errors subclass ValueError; bad cron -> fail fast, persist nothing.
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {exc}") from exc

    starts = [pair[0] for pair in pairs]
    total = len(starts)
    capped = starts[: body.limit]
    return CalendarButlerEventPreviewResponse(
        occurrences=capped,
        total_in_window=total,
        more_count=max(0, total - len(capped)),
        window_start=window_start,
        window_end=window_end,
        effective_cron=effective_cron,
        notes=notes,
    )


@router.post(
    "/butler-events/preview",
    response_model=ApiResponse[CalendarButlerEventPreviewResponse],
)
async def preview_butler_event_recurrence(
    body: CalendarButlerEventPreviewRequest,
) -> ApiResponse[CalendarButlerEventPreviewResponse]:
    """Dry-run a draft butler event's recurrence expansion.

    Returns the projected occurrence datetimes within the 90-day projection
    window (capped, with a ``more_count`` "+N more" sentinel) plus ``notes``
    describing any lossy RRULE->cron degradations. Persists nothing, creates no
    event, and spawns no LLM session. Unparseable ``rrule``/``cron`` -> HTTP 422.
    """
    preview = _build_butler_event_preview(body)
    return ApiResponse[CalendarButlerEventPreviewResponse](data=preview)


def _result_indicates_not_found(result: dict[str, Any]) -> bool:
    """Detect a "target id does not exist" signal in a soft-mutation result.

    The reused MCP tools report a missing target differently:
    ``calendar_update_butler_event`` (snooze) catches the error and returns
    ``{"status": "error", "error": ...}``, while ``reminder_dismiss`` raises a
    ``ValueError`` whose text is surfaced in the ``result`` envelope. Both encode
    "not found" in the message, so we map that to a 404 for the snooze/dismiss
    affordances.
    """
    if not isinstance(result, dict):
        return False
    if result.get("status") == "error":
        return "not found" in str(result.get("error") or "").lower()
    raw = result.get("result")
    if isinstance(raw, str):
        return "not found" in raw.lower()
    return False


@router.post("/butler-events", response_model=ApiResponse[CalendarWorkspaceMutationResponse])
async def mutate_butler_event(
    body: CalendarWorkspaceButlerMutationRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
) -> ApiResponse[CalendarWorkspaceMutationResponse]:
    """Create/update/delete/toggle/dismiss/snooze butler-view events via calendar MCP tools.

    ``dismiss`` consumes a due reminder/butler event through the existing
    ``reminder_dismiss`` tool; ``snooze`` reschedules it to a new ``due_at`` time
    through the existing ``calendar_update_butler_event`` update path. Both reuse
    the soft-mutation envelope and 404 when the target id does not exist — no new
    table and no new MCP tool.
    """
    tool_name = {
        "create": "calendar_create_butler_event",
        "update": "calendar_update_butler_event",
        "delete": "calendar_delete_butler_event",
        "toggle": "calendar_toggle_butler_event",
        "dismiss": "reminder_dismiss",
        "snooze": "calendar_update_butler_event",
    }[body.action]

    arguments = dict(body.payload)
    if body.action == "create":
        arguments.setdefault("butler_name", body.butler_name)
    if body.action == "snooze":
        # Snooze reschedules to a new due time. Accept it under ``due_at`` (the
        # user-facing reminder field) or ``start_at`` and normalize to the update
        # tool's ``start_at`` parameter, which drives both the reminder and the
        # scheduled-task update paths.
        due_at = arguments.pop("due_at", None)
        if due_at is not None and not arguments.get("start_at"):
            arguments["start_at"] = due_at
        if not arguments.get("start_at"):
            raise HTTPException(
                status_code=422, detail="snooze requires a new due_at/start_at time"
            )
    if body.action == "dismiss":
        # ``reminder_dismiss`` only accepts ``event_id`` — forward nothing else
        # (no request_id) so the MCP call does not reject unexpected arguments.
        event_id = arguments.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise HTTPException(status_code=422, detail="dismiss requires an event_id")
        arguments = {"event_id": event_id.strip()}
    elif body.request_id is not None:
        arguments["request_id"] = body.request_id

    summary = {
        "action": body.action,
        "tool_name": tool_name,
        "request_id": body.request_id,
    }
    try:
        try:
            mutation_result = await _call_mcp_tool(mgr, body.butler_name, tool_name, arguments)
        except HTTPException as exc:
            # ``reminder_dismiss`` raises (rather than returns) on a missing id,
            # surfacing as a 503 transport error — remap to a 404 for the
            # dismiss/snooze affordances so unknown ids fail cleanly.
            if (
                body.action in {"dismiss", "snooze"}
                and exc.status_code == 503
                and "not found" in str(exc.detail).lower()
            ):
                raise HTTPException(status_code=404, detail="Target event was not found") from exc
            raise
        if body.action in {"dismiss", "snooze"} and _result_indicates_not_found(mutation_result):
            raise HTTPException(status_code=404, detail="Target event was not found")
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
# Proposals — accept / dismiss
#   POST /api/calendar/workspace/proposals/{proposal_id}/accept
#   POST /api/calendar/workspace/proposals/{proposal_id}/dismiss
# ---------------------------------------------------------------------------


def _proposal_response(row: CalendarProposalRow) -> CalendarProposalActionResponse:
    return CalendarProposalActionResponse(
        proposal_id=row.proposal_id,
        status=row.status,
        accepted_event_id=row.accepted_event_id,
        butler_name=row.butler_name,
    )


def _butler_event_create_args(
    row: CalendarProposalRow,
    overrides: CalendarProposalAcceptRequest | None,
    *,
    request_id: str,
) -> dict[str, Any]:
    """Build ``calendar_create_butler_event`` arguments from a stored proposal.

    Inline ``overrides`` (when present) take precedence over the stored payload.
    Datetimes are serialized to ISO-8601 strings for the MCP transport. The
    butler-event tool routes the create to the Butlers subcalendar by default
    (``_resolve_calendar_id(None)``), so no ``calendar_id`` is passed.
    """
    schema = row.db_butler or row.butler_name or ""
    title = (overrides.title if overrides and overrides.title else None) or row.title
    start_at = (overrides.start_at if overrides and overrides.start_at else None) or row.start_at
    end_at = (overrides.end_at if overrides and overrides.end_at else None) or row.end_at
    timezone = (
        (overrides.timezone if overrides and overrides.timezone else None) or row.timezone or "UTC"
    )
    # Falsy fallback (consistent with title/start_at/timezone above): a blank
    # override does not blank out the stored value.
    description = (
        overrides.description if overrides and overrides.description else None
    ) or row.description
    location = (overrides.location if overrides and overrides.location else None) or row.location

    start_iso = start_at.isoformat() if isinstance(start_at, datetime) else start_at
    end_iso = end_at.isoformat() if isinstance(end_at, datetime) else end_at

    args: dict[str, Any] = {
        "butler_name": schema,
        "title": str(title or "Untitled"),
        "start_at": start_iso,
        "end_at": end_iso,
        "timezone": timezone,
        "request_id": request_id,
    }
    # Only forward description/location when present so the tool's optional
    # parameters keep their defaults for proposals that carry neither.
    if description is not None:
        args["description"] = description
    if location is not None:
        args["location"] = location
    return args


@router.post(
    "/proposals/{proposal_id}/accept",
    response_model=ApiResponse[CalendarProposalActionResponse],
)
async def accept_proposal(
    proposal_id: UUID,
    body: CalendarProposalAcceptRequest | None = Body(default=None),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
) -> ApiResponse[CalendarProposalActionResponse]:
    """Accept a calendar proposal — create the event on the Butlers subcalendar.

    Reads the stored proposal payload (with optional inline overrides), routes it
    through ``calendar_create_butler_event`` (which defaults butler-authored
    creates to the dedicated Butlers subcalendar, never the user's primary), and
    flips the proposal to ``status='accepted'`` with the created
    ``accepted_event_id``.

    Idempotent: accepting an already-accepted proposal returns the existing
    ``accepted_event_id`` with **no** second provider write.

    Fail-closed: if the provider create fails, the proposal stays ``pending``
    (never a partial ``accepted`` row without an ``accepted_event_id``) so the
    user can retry. Unknown id → 404; accepting a dismissed proposal → 409.
    """
    row = await query_calendar_proposal_by_id(db, proposal_id=proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    schema = row.db_butler or row.butler_name or ""
    summary = {"proposal_id": str(proposal_id), "action": "accept"}

    # Idempotent re-accept: already accepted → return existing id, no 2nd write.
    if row.status == "accepted":
        return ApiResponse[CalendarProposalActionResponse](data=_proposal_response(row))
    if row.status == "dismissed":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal '{proposal_id}' is dismissed and cannot be accepted",
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal '{proposal_id}' has unexpected status '{row.status}'",
        )

    # Fail-closed: create on the provider FIRST. Only mark accepted once the
    # event exists, so a provider failure leaves the row pending for retry.
    create_args = _butler_event_create_args(row, body, request_id=f"proposal-accept-{proposal_id}")
    try:
        create_result = await _call_mcp_tool(
            mgr, schema, "calendar_create_butler_event", create_args
        )
    except HTTPException:
        await log_audit_entry(
            db,
            schema,
            "calendar.workspace.proposals.accept",
            summary,
            result="error",
            error="calendar_create_butler_event failed",
        )
        raise

    created_event_id = create_result.get("event_id")
    if not isinstance(created_event_id, str) or not created_event_id.strip():
        await log_audit_entry(
            db,
            schema,
            "calendar.workspace.proposals.accept",
            summary,
            result="error",
            error="provider create returned no event_id",
        )
        raise HTTPException(
            status_code=502,
            detail="calendar_create_butler_event did not return an event id",
        )
    try:
        accepted_event_id = UUID(created_event_id.strip())
    except ValueError as exc:
        await log_audit_entry(
            db,
            schema,
            "calendar.workspace.proposals.accept",
            summary,
            result="error",
            error="provider returned a non-UUID event id",
        )
        raise HTTPException(
            status_code=502,
            detail=f"calendar_create_butler_event returned a non-UUID event id: {created_event_id}",
        ) from exc

    updated = await update_calendar_proposal_status(
        db,
        schema=schema,
        proposal_id=proposal_id,
        status="accepted",
        accepted_event_id=accepted_event_id,
        only_if_status="pending",
    )
    if updated is None:
        # Lost a race (concurrently accepted or dismissed) — reconcile with the
        # persisted state instead of returning a misleading 500.
        refreshed = await query_calendar_proposal_by_id(db, proposal_id=proposal_id)
        if refreshed is not None:
            if refreshed.status == "accepted":
                return ApiResponse[CalendarProposalActionResponse](
                    data=_proposal_response(refreshed)
                )
            if refreshed.status == "dismissed":
                raise HTTPException(
                    status_code=409,
                    detail=f"Proposal '{proposal_id}' is dismissed and cannot be accepted",
                )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to mark proposal '{proposal_id}' accepted after provider create",
        )

    await log_audit_entry(
        db,
        schema,
        "calendar.workspace.proposals.accept",
        {**summary, "accepted_event_id": str(accepted_event_id)},
    )
    return ApiResponse[CalendarProposalActionResponse](data=_proposal_response(updated))


@router.post(
    "/proposals/{proposal_id}/dismiss",
    response_model=ApiResponse[CalendarProposalActionResponse],
)
async def dismiss_proposal(
    proposal_id: UUID,
    *,
    db: Db,
) -> ApiResponse[CalendarProposalActionResponse]:
    """Dismiss a calendar proposal — discard it with no provider write.

    Flips the proposal to ``status='dismissed'``. Idempotent: dismissing an
    already-dismissed proposal is a no-op returning its state. Unknown id → 404;
    dismissing an already-accepted proposal → 409.
    """
    row = await query_calendar_proposal_by_id(db, proposal_id=proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    schema = row.db_butler or row.butler_name or ""
    summary = {"proposal_id": str(proposal_id), "action": "dismiss"}

    # Idempotent re-dismiss.
    if row.status == "dismissed":
        return ApiResponse[CalendarProposalActionResponse](data=_proposal_response(row))
    if row.status == "accepted":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal '{proposal_id}' is accepted and cannot be dismissed",
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal '{proposal_id}' has unexpected status '{row.status}'",
        )

    updated = await update_calendar_proposal_status(
        db,
        schema=schema,
        proposal_id=proposal_id,
        status="dismissed",
        accepted_event_id=None,
        only_if_status="pending",
    )
    if updated is None:
        # Lost a race (concurrently dismissed or accepted) — reconcile with the
        # persisted state instead of returning a misleading 500.
        refreshed = await query_calendar_proposal_by_id(db, proposal_id=proposal_id)
        if refreshed is not None:
            if refreshed.status == "dismissed":
                return ApiResponse[CalendarProposalActionResponse](
                    data=_proposal_response(refreshed)
                )
            if refreshed.status == "accepted":
                raise HTTPException(
                    status_code=409,
                    detail=f"Proposal '{proposal_id}' is accepted and cannot be dismissed",
                )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to mark proposal '{proposal_id}' dismissed",
        )

    await log_audit_entry(
        db,
        schema,
        "calendar.workspace.proposals.dismiss",
        summary,
    )
    return ApiResponse[CalendarProposalActionResponse](data=_proposal_response(updated))


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
    *,
    db: Db,
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
    results, failed = await db.fan_out_with_status(
        _AUDIT_SQL, (limit + offset, 0), butler_names=query_targets
    )
    count_results, count_failed = await db.fan_out_with_status(
        _AUDIT_COUNT_SQL, (), butler_names=query_targets
    )
    # A partial fan-out failure silently drops a schema's audit rows and
    # undercounts ``total`` — flag it so the log is never read as a complete
    # (merely shorter) history. Either the row or the count fan-out failing is a
    # genuine source failure (core ``calendar_action_log`` table).
    sources_available = not failed and not count_failed

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
    data = CalendarAuditResponse(
        entries=entries,
        total=total,
        offset=offset,
        limit=limit,
        sources_available=sources_available,
    )
    return ApiResponse[CalendarAuditResponse](data=data)


# ===========================================================================
# Accounts control plane + per-calendar source toggle
#
# These endpoints live under /api/calendar (NOT /api/calendar/workspace) so a
# separate router is exported. Both routers use the public ``Db`` dependency.
# ===========================================================================

accounts_router = APIRouter(prefix="/api/calendar", tags=["calendar", "accounts"])

_GOOGLE_CALENDAR_CONNECTOR_TYPE = "google_calendar"


def _calendar_accounts_shared_pool(db: DatabaseManager) -> Any | None:
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
            " cr.error_message"
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

    last_heartbeat_at = _coerce_datetime(heartbeat.get("last_heartbeat_at"))

    # A stale or missing heartbeat means the connector's last-written state
    # is no longer trustworthy as a liveness signal (bu-27dxl.6.6): a
    # heartbeat that stopped weeks ago while stuck on state='healthy' must
    # not keep rendering as healthy forever. Liveness (derive_liveness) gates
    # the presented state; the stored state is only consulted once the
    # heartbeat itself is confirmed live.
    if derive_liveness(last_heartbeat_at) != "online":
        state: CalendarAccountHealthState = "degraded"
    else:
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
        last_heartbeat_at=last_heartbeat_at,
        last_ingest_at=last_ingest_at,
    )


@accounts_router.get("/accounts", response_model=ApiResponse[CalendarAccountsResponse])
async def list_calendar_accounts(
    *,
    db: Db,
) -> ApiResponse[CalendarAccountsResponse]:
    """List connected Google accounts joined with Google Calendar connector health.

    Read-only: this endpoint never connects or disconnects accounts (account
    lifecycle lives in the Google accounts surface). When the connector health
    surface is unavailable, accounts are still returned with an ``unknown``
    health indicator and ``health_available = false`` rather than failing.
    """
    shared_pool = _calendar_accounts_shared_pool(db)
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
    *,
    db: Db,
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


class _SourceRoleLockError(Exception):
    """The per-source advisory lock could not be taken."""


@contextlib.asynccontextmanager
async def _source_role_lock(pool: Any, source_key: str):
    """Hold ``pg_advisory_lock`` for one source's role write; yields the holding connection.

    The caller must run that pool's own write on the yielded connection: asking the same pool
    for a second connection while holding one deadlocks a small pool under concurrent requests.
    """
    key = f"calendar_source_role:{source_key}"
    try:
        context = pool.acquire()
        conn = await context.__aenter__()
    except Exception as exc:
        raise _SourceRoleLockError from exc
    try:
        try:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", key)
        except Exception as exc:
            raise _SourceRoleLockError from exc
        try:
            yield conn
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", key)
            except Exception:
                # A session lock must never go back into the pool held: drop the connection.
                logger.warning("calendar source role unlock failed; dropping the connection")
                conn.terminate()
    finally:
        await context.__aexit__(None, None, None)


@accounts_router.post("/sources/role", response_model=ApiResponse[CalendarSourceRoleResponse])
async def set_calendar_source_role(
    request: CalendarSourceRoleRequest,
    *,
    db: Db,
) -> ApiResponse[CalendarSourceRoleResponse]:
    """Set whose time a calendar source's events are (REQ-module-calendar-037).

    The same provider calendar is a source in every calendar butler schema, and every reader joins
    its own schema's row, so the role is written to all of them. A role is the owner's word
    (``owner_confirmed``) and discovery never overwrites it; ``role: null`` clears it back to
    ``unclassified`` for the next discovery to infer. A schema whose write failed is reported.
    """
    names = db.butlers_with_module("calendar")
    targets = sorted(names if isinstance(names, list) else db.butler_names)
    role = request.role or "unclassified"
    basis = "owner_confirmed" if request.role is not None else "provider_inferred"
    subject = request.subject_entity_id
    if subject is not None and role not in SUBJECT_ROLES:
        raise HTTPException(
            status_code=422,
            detail="A subject only accompanies the household_member, other_person or team role.",
        )
    if subject is not None and targets:
        known = await db.pool(targets[0]).fetchval(
            "SELECT 1 FROM public.entities WHERE id = $1", subject
        )
        if not known:
            raise HTTPException(status_code=404, detail="Requested subject entity was not found")

    updated: list[str] = []
    failed: list[str] = []
    # One owner decision must land in every schema or in none of the interleavings: hold a
    # cluster-wide advisory lock for this source across the whole fan-out, so two concurrent
    # requests (even on different API replicas) cannot leave schemas on different roles.
    update_sql = f"""
        UPDATE calendar_sources
        SET time_role = $1, role_basis = '{basis}', role_subject_entity_id = $3,
            updated_at = now()
        WHERE source_key = $2
        RETURNING id
        """
    try:
        if targets:
            async with _source_role_lock(db.pool(targets[0]), request.source_key) as lock_conn:
                for butler in targets:
                    try:
                        # The lock-holding connection serves its own schema's write.
                        runner = lock_conn if butler == targets[0] else db.pool(butler)
                        rows = await runner.fetch(update_sql, role, request.source_key, subject)
                    except Exception:
                        logger.warning(
                            "set_calendar_source_role failed in %s", butler, exc_info=True
                        )
                        failed.append(butler)
                        continue
                    if rows:
                        updated.append(butler)
    except _SourceRoleLockError as exc:
        raise HTTPException(
            status_code=503, detail="Could not lock the calendar source. Try again."
        ) from exc
    if not updated:
        if failed:
            # Nothing was written and some schemas could not be asked: that is an outage, not
            # an unknown source, so the owner is told to retry rather than that it does not exist.
            raise HTTPException(
                status_code=503,
                detail=f"Could not write the role in: {', '.join(failed)}. Try again.",
            )
        raise HTTPException(status_code=404, detail="Requested calendar source was not found")

    await log_audit_entry(
        db,
        updated[0],
        "calendar.workspace.source_role",
        {
            "source_key": request.source_key,
            "role": role,
            "basis": basis,
            "subject_entity_id": str(subject) if subject else None,
            "updated": updated,
            "failed": failed,
        },
    )
    return ApiResponse[CalendarSourceRoleResponse](
        data=CalendarSourceRoleResponse(
            source_key=request.source_key,
            role=role,  # type: ignore[arg-type]
            role_basis=basis,  # type: ignore[arg-type]
            subject_entity_id=subject,
            updated_butlers=updated,
            failed_butlers=failed,
        )
    )
