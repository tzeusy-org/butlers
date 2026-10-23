"""Calendar tools implementation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .constants import _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID, SYNC_ERROR_KIND_NONE, logger
from .errors import (
    CalendarAuthError,
    CalendarRequestError,
    _build_structured_error,
    classify_sync_error_kind,
)
from .models import CalendarSyncState

if TYPE_CHECKING:
    from . import CalendarModule


def register_read_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("core")
    async def calendar_list_events(
        calendar_id: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List calendar events using the configured provider.

        Fail-open: returns empty events list with error metadata on provider failure
        rather than raising (spec section 4.4 / 15.2).
        """
        provider = module._require_provider()
        resolved_calendar_id = module._resolve_calendar_id(calendar_id)
        try:
            events = await provider.list_events(
                calendar_id=resolved_calendar_id,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
            )
        except CalendarAuthError as exc:
            logger.warning(
                "calendar_list_events failed (calendar_id=%s): %s",
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_dict = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            error_dict["events"] = []
            return error_dict
        return {
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "events": [module._event_to_payload(event) for event in events],
        }

    @_tool("core")
    async def calendar_find_free_slots(
        duration_minutes: int,
        search_start: datetime,
        search_end: datetime,
        calendar_ids: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find ranked open time slots over a window using free/busy data.

        Read-only availability finder: it proposes slots and never creates,
        updates, or deletes an event. Queries free/busy across the relevant
        calendars, subtracts busy windows, tiles each free gap into
        ``duration_minutes``-sized candidate slots, hard-filters them through
        the owner's scheduling-availability preferences (earliest/latest
        meeting time, allowed days, no-meeting blocks) when configured, and
        ranks earliest-first with structured-constraint matches preferred.

        Args:
            duration_minutes: Length of the meeting to place (must be > 0).
            search_start: Start of the window to search (timezone-aware).
            search_end: End of the window to search (must be after start).
            calendar_ids: Optional explicit calendars to span; defaults to
                every connected calendar.
            constraints: Optional pre-parsed structured constraints, e.g.
                ``{"part_of_day": "morning", "avoid_weekdays": ["FR"]}``.
                The finder performs no LLM call; natural-language parsing
                ("mornings only", "avoid Fridays") happens at the call site.
            limit: Maximum number of slots to return (default 10).

        On success, returns ``{"slots": [{"start_at", "end_at", "timezone"}],
        "duration_minutes", "calendar_ids"}``. A fully-busy window yields an
        empty ``slots`` list (the free/busy lookup completed successfully,
        so this is not an error). If the provider free/busy lookup raises a
        ``CalendarAuthError`` (including an authentication or request
        failure), returns the module's structured error dictionary with
        ``status="error"``, redacted diagnostic metadata, empty ``slots``,
        the requested duration, and the resolved calendar IDs. The workspace
        API maps that structured failure to its fixed content-blind
        unavailable reason; callers must not treat it as a successful empty
        result.
        """
        return await module._find_free_slots(
            duration_minutes=duration_minutes,
            search_start=search_start,
            search_end=search_end,
            calendar_ids=calendar_ids,
            constraints=constraints,
            limit=limit,
        )

    @_tool("core")
    async def calendar_get_event(
        event_id: str,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a single calendar event by id using the configured provider.

        Fail-open: returns null event with error metadata on provider failure
        rather than raising (spec section 4.4 / 15.2).
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        provider = module._require_provider()
        resolved_calendar_id = module._resolve_calendar_id(calendar_id)
        try:
            event = await provider.get_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
            )
        except CalendarRequestError as exc:
            if exc.status_code == 404:
                return {
                    "status": "not_found",
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "event": None,
                }
            logger.warning(
                "calendar_get_event failed (event_id=%s, calendar_id=%s): %s",
                normalized_event_id,
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_dict = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            error_dict["event"] = None
            return error_dict
        except CalendarAuthError as exc:
            logger.warning(
                "calendar_get_event failed (event_id=%s, calendar_id=%s): %s",
                normalized_event_id,
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_dict = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            error_dict["event"] = None
            return error_dict
        return {
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event": None if event is None else module._event_to_payload(event),
        }


def register_sync_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("core")
    async def calendar_sync_status(
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Return sync state for the configured calendar (spec section 13.5).

        Returns the last sync time, whether the sync token is valid,
        pending changes count, and any last sync error.

        Fail-open: returns state with ``sync_enabled=False`` when sync
        is not configured rather than raising.
        """
        resolved_calendar_id = module._resolve_calendar_id(calendar_id)
        cfg = module._require_config()
        provider = module._require_provider()
        projection_freshness = await module._projection_freshness_metadata()

        if not cfg.sync.enabled:
            return {
                "status": "ok",
                "sync_enabled": False,
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "last_sync_at": None,
                "sync_token_valid": False,
                "last_batch_change_count": 0,
                "last_sync_error": None,
                "error_kind": SYNC_ERROR_KIND_NONE,
                "projection_freshness": projection_freshness,
            }

        # Use cached in-memory state when available; fall back to KV store.
        sync_state = module._sync_states.get(resolved_calendar_id)
        if sync_state is None:
            try:
                sync_state = await module._load_sync_state(resolved_calendar_id)
            except Exception as exc:
                logger.warning(
                    "calendar_sync_status: failed to load state for '%s': %s",
                    resolved_calendar_id,
                    exc,
                )
                sync_state = CalendarSyncState()

        return {
            "status": "ok",
            "sync_enabled": True,
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "last_sync_at": sync_state.last_sync_at,
            "sync_token_valid": sync_state.sync_token is not None,
            "last_batch_change_count": sync_state.last_batch_change_count,
            "last_sync_error": sync_state.last_sync_error,
            "error_kind": classify_sync_error_kind(sync_state.last_sync_error),
            "projection_freshness": projection_freshness,
        }

    @_tool("core")
    async def calendar_force_sync(
        calendar_id: str | None = None,
        full: bool = False,
        queue: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Trigger or durably queue an immediate calendar sync.

        When ``calendar_id`` is omitted, syncs **all** registered provider
        calendars (pull-all) and pushes internal events to the Butlers
        calendar.  When a specific ``calendar_id`` is given, syncs only
        that calendar.

        When ``full=True`` this is the operator-driven **cursor recovery**
        path: the sync ignores the stored incremental token and runs a full
        re-sync over ``config.sync.full_sync_window_days`` (``sync_token=None``).
        The default (``full=False``) preserves the incremental behavior using
        the stored sync token. A forced full re-sync is logged.

        ``queue=True`` persists a command and returns immediately. It is the
        dashboard/API path: the module-owned worker performs provider I/O
        after acknowledgement so a browser request never has to wait for a
        full provider pull. Direct MCP calls retain the historical inline
        behavior by default.
        """
        if queue:
            return await module._enqueue_force_sync_command(
                calendar_id=calendar_id,
                full=full,
                request_id=request_id,
            )
        return await module._run_force_sync(calendar_id=calendar_id, full=full)

    @_tool("core")
    async def calendar_set_primary(
        calendar_id: str,
    ) -> dict[str, Any]:
        """Set the user's DEFAULT-TARGET calendar for user-facing mutations.

        This is the calendar used when an MCP tool mutation passes no explicit
        ``calendar_id``.  The ``calendar_id`` must be one of the discovered
        provider calendars.  Updates the in-memory default-target field and
        persists it under ``GOOGLE_CALENDAR_DEFAULT_TARGET_ID`` so the choice
        survives restarts.

        This MUST NOT touch ``_resolved_calendar_id`` — the immutable Butlers
        calendar id (cred key ``GOOGLE_CALENDAR_ID``).  The two are distinct
        concerns: the Butlers calendar is the home for butler-authored events,
        while the default target is the user's preferred personal calendar.
        """
        normalized = calendar_id.strip()
        if not normalized:
            raise ValueError("calendar_id must be a non-empty string")

        if normalized not in module._all_provider_calendar_ids:
            return {
                "status": "error",
                "error": f"Unknown calendar_id: {normalized}",
                "known_calendars": module._all_provider_calendar_ids,
            }

        # Update ONLY the default-target selection.  Never mutate
        # ``_resolved_calendar_id`` (the immutable Butlers calendar id).
        old_id = module._default_target_calendar_id
        module._default_target_calendar_id = normalized

        persisted = False
        if module._credential_store is not None:
            try:
                await module._credential_store.store_shared(
                    _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID,
                    normalized,
                    category="google",
                    description="Default-target Google Calendar ID (set via dashboard)",
                    is_sensitive=False,
                )
                persisted = True
            except Exception as exc:
                logger.warning(
                    "calendar_set_primary: failed to persist to credential store: %s",
                    exc,
                )

        return {
            "status": "ok",
            "old_calendar_id": old_id,
            "new_calendar_id": normalized,
            "persisted": persisted,
        }

    @_tool("core")
    async def calendar_list_calendars() -> dict[str, Any]:
        """List the calendars available on the connected provider account.

        Read-only. Wraps the provider's ``list_calendars()`` and normalizes
        each entry into a butler-aware shape that backs the dashboard's
        per-event calendar selector and the Sources drawer.

        Each calendar is returned as
        ``{calendar_id, summary, primary, access_role, is_butlers_calendar,
        selectable}`` where:

        - ``is_butlers_calendar`` flags the dedicated Butlers calendar
          (``_resolved_calendar_id``).
        - ``selectable`` is ``False`` for any calendar whose access role is
          not ``writer`` or ``owner`` (read-only calendars cannot be a write
          target).

        Fail-open: a provider error returns an empty ``calendars`` list with
        error metadata rather than raising.
        """
        provider = module._require_provider()
        if not hasattr(provider, "list_calendars"):
            return {"provider": provider.name, "calendars": []}
        try:
            raw_calendars = await provider.list_calendars()
        except Exception as exc:
            logger.warning("calendar_list_calendars failed: %s", exc, exc_info=True)
            error_dict = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id="",
            )
            error_dict["calendars"] = []
            return error_dict

        calendars: list[dict[str, Any]] = []
        for cal in raw_calendars:
            if not isinstance(cal, dict):
                continue
            cal_id = cal.get("id")
            if not isinstance(cal_id, str) or not cal_id:
                continue
            summary = cal.get("summaryOverride") or cal.get("summary") or cal_id
            access_role = cal.get("accessRole")
            selectable = access_role in ("owner", "writer")
            calendars.append(
                {
                    "calendar_id": cal_id,
                    "summary": summary,
                    "primary": bool(cal.get("primary")),
                    "access_role": access_role,
                    "is_butlers_calendar": cal_id == module._resolved_calendar_id,
                    "selectable": selectable,
                }
            )
        return {"provider": provider.name, "calendars": calendars}


def register_proposal_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("core")
    async def calendar_propose_event(
        title: str,
        start_at: datetime,
        end_at: datetime,
        description: str | None = None,
        location: str | None = None,
        timezone: str = "UTC",
        source_event_id: str | None = None,
        source_snippet: str | None = None,
        confidence: float | None = None,
        entity_ids: list[str] | None = None,
        butler_name: str | None = None,
    ) -> dict[str, Any]:
        """Propose a calendar event for review without writing to any provider.

        Inserts a PENDING row into calendar_event_proposals and returns its id.
        Idempotent: if ``source_event_id`` is supplied and a proposal with that
        id already exists, the existing proposal id is returned (no duplicate,
        no error).
        """
        resolved_entity_ids = []
        if entity_ids:
            for raw in entity_ids:
                try:
                    resolved_entity_ids.append(uuid.UUID(raw.strip()))
                except (ValueError, AttributeError) as exc:
                    raise ValueError(f"Invalid entity_id: {raw!r}") from exc

        proposal_id = await module._propose_event(
            butler_name=butler_name,
            title=title,
            start_at=start_at,
            end_at=end_at,
            description=description,
            location=location,
            timezone=timezone,
            source_event_id=source_event_id,
            source_snippet=source_snippet,
            confidence=confidence,
            entity_ids=resolved_entity_ids,
        )
        return {"proposal_id": str(proposal_id)}
