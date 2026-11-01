"""Calendar conflicts implementation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from butlers.core.temporal.scheduling import (
    SchedulingPreferences,
    get_scheduling_preferences,
)

from .constants import (
    _SUGGESTION_SEARCH_HORIZON,
    DEFAULT_CONFLICT_SUGGESTION_COUNT,
    VALID_CONFLICT_POLICIES,
    CalendarConflictPolicy,
    logger,
)
from .errors import CalendarAuthError, _build_structured_error
from .google_mapping import _coerce_zoneinfo
from .models import BusyWindow, CalendarEvent, CalendarEventCreate
from .provider import CalendarProvider

_SLOT_WEEKDAY_CODES: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


_PART_OF_DAY_BOUNDS: dict[str, tuple[int, int]] = {
    "morning": (0, 12),
    "afternoon": (12, 17),
    "evening": (17, 24),
}


def _parse_slot_constraints(
    constraints: dict[str, Any] | None,
) -> tuple[str | None, frozenset[str]]:
    """Validate the structured (pre-parsed) finder constraints.

    Returns ``(part_of_day, avoid_weekdays)``. Raises ``ValueError`` on an
    unrecognised part-of-day bucket or weekday code so a bad caller argument
    surfaces as a validation error rather than silently ranking nothing.
    """
    if not constraints:
        return None, frozenset()
    part_of_day = constraints.get("part_of_day")
    if part_of_day is not None:
        part_of_day = str(part_of_day).strip().lower()
        if part_of_day not in _PART_OF_DAY_BOUNDS:
            raise ValueError(
                f"Invalid part_of_day {part_of_day!r}; "
                f"expected one of {sorted(_PART_OF_DAY_BOUNDS)}"
            )
    avoid: set[str] = set()
    for raw in constraints.get("avoid_weekdays") or []:
        code = str(raw).strip().upper()
        if code not in _SLOT_WEEKDAY_CODES:
            raise ValueError(
                f"Invalid weekday {raw!r}; expected one of {list(_SLOT_WEEKDAY_CODES)}"
            )
        avoid.add(code)
    return part_of_day, frozenset(avoid)


def _merge_busy_windows(windows: list[tuple[datetime, datetime]]) -> list[BusyWindow]:
    """Sort and merge overlapping/touching intervals into ``BusyWindow``s."""
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w[0], w[1]))
    merged: list[list[datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [BusyWindow(start_at=s, end_at=e) for s, e in merged]


def _subtract_busy(
    start_at: datetime,
    end_at: datetime,
    busy: list[BusyWindow],
) -> list[tuple[datetime, datetime]]:
    """Return the free gaps in ``[start_at, end_at)`` after removing busy windows.

    ``busy`` is assumed sorted by ``start_at`` and non-overlapping (the shape
    ``get_free_busy`` returns).
    """
    gaps: list[tuple[datetime, datetime]] = []
    cursor = start_at
    for window in busy:
        # ``busy`` is sorted by ``start_at``; once a window opens at/after the
        # search end, every later window does too, so nothing can clip the
        # remaining gap — break before a past-``end_at`` window inflates it.
        if window.start_at >= end_at:
            break
        ws = max(window.start_at, start_at)
        we = min(window.end_at, end_at)
        if we <= cursor:
            continue
        if ws > cursor:
            gaps.append((cursor, ws))
        cursor = we
        if cursor >= end_at:
            break
    if cursor < end_at:
        gaps.append((cursor, end_at))
    return [(s, e) for s, e in gaps if e > s]


def _slot_matches_constraints(
    local_start: datetime,
    *,
    part_of_day: str | None,
    avoid_weekdays: frozenset[str],
) -> bool:
    """Soft-preference check for the structured (pre-parsed) finder constraints."""
    if avoid_weekdays and _SLOT_WEEKDAY_CODES[local_start.weekday()] in avoid_weekdays:
        return False
    if part_of_day is not None:
        bounds = _PART_OF_DAY_BOUNDS.get(part_of_day)
        if bounds is not None and not (bounds[0] <= local_start.hour < bounds[1]):
            return False
    return True


def _compute_free_slots(
    *,
    search_start: datetime,
    search_end: datetime,
    busy: list[BusyWindow],
    duration: timedelta,
    scheduling_preferences: SchedulingPreferences | None = None,
    part_of_day: str | None = None,
    avoid_weekdays: frozenset[str] = frozenset(),
    display_timezone: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Turn free/busy data into ranked, duration-sized open slots.

    Subtracts ``busy`` from the search window, tiles each free gap into
    ``duration``-sized candidate slots, hard-filters them through the owner's
    scheduling-availability preferences (when configured), and ranks them
    earliest-first with structured-constraint matches preferred. Returns at most
    ``limit`` ``{"start_at", "end_at", "timezone"}`` dicts.
    """
    if duration <= timedelta(0) or limit < 1 or search_end <= search_start:
        return []

    apply_prefs = scheduling_preferences is not None and scheduling_preferences.has_constraints
    tz = display_timezone or (scheduling_preferences.timezone if scheduling_preferences else None)

    def _localize(dt: datetime) -> datetime:
        if tz is None or dt.tzinfo is None:
            return dt
        return dt.astimezone(_coerce_zoneinfo(tz))

    candidates: list[tuple[datetime, datetime, bool]] = []
    for gap_start, gap_end in _subtract_busy(search_start, search_end, busy):
        cursor = gap_start
        while cursor + duration <= gap_end:
            slot_end = cursor + duration
            if not apply_prefs or scheduling_preferences.allows(cursor, slot_end):
                matches = _slot_matches_constraints(
                    _localize(cursor),
                    part_of_day=part_of_day,
                    avoid_weekdays=avoid_weekdays,
                )
                candidates.append((cursor, slot_end, matches))
            cursor = slot_end

    # Constraint-matching slots first, then earliest-first within each group.
    candidates.sort(key=lambda c: (0 if c[2] else 1, c[0]))
    result_tz = tz or "UTC"
    return [
        {
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "timezone": result_tz,
        }
        for start, end, _ in candidates[:limit]
    ]


class ConflictsMixin:
    """Existing Calendar conflicts behavior on the owning module instance."""

    def _resolve_conflict_policy(
        self, policy_override: CalendarConflictPolicy | str | None
    ) -> CalendarConflictPolicy:
        if policy_override is None:
            return self._require_config().conflicts.policy

        normalized = policy_override.strip().lower()
        if normalized not in VALID_CONFLICT_POLICIES:
            supported = ", ".join(sorted(VALID_CONFLICT_POLICIES))
            raise ValueError(f"conflict_policy must be one of: {supported}")
        return normalized  # type: ignore[return-value]

    async def _gate_overlap_approval(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        conflicts: list[dict[str, str]],
        provider_name: str,
        resolved_calendar_id: str,
    ) -> dict[str, Any] | None:
        """Gate an overlap override through the approval queue when configured.

        Returns a structured ``approval_required`` response when approval is
        needed, or ``None`` to let the caller proceed with the write.

        When ``require_approval_for_overlap`` is False the method always returns
        ``None`` (write-through).  When it is True and the approval enqueuer is
        not wired (approvals module disabled), returns explicit fallback
        guidance instead.
        """
        config = self._require_config()
        if not config.conflicts.require_approval_for_overlap:
            return None

        if self._approval_enqueuer is None:
            # Approvals module is not enabled -- return fallback guidance.
            return {
                "status": "approval_unavailable",
                "policy": "allow_overlap",
                "provider": provider_name,
                "calendar_id": resolved_calendar_id,
                "conflicts": conflicts,
                "message": (
                    "Overlap override requires approval but the approvals module "
                    "is not enabled on this butler. Enable the approvals module or "
                    "set require_approval_for_overlap=false in calendar config to "
                    "allow direct overlap writes."
                ),
            }

        agent_summary = (
            f"Calendar overlap override: {tool_name} with {len(conflicts)} "
            f"conflicting event(s) on calendar '{resolved_calendar_id}'"
        )
        action_id = await self._approval_enqueuer(tool_name, tool_args, agent_summary)
        logger.info(
            "Overlap override queued for approval (action=%s, tool=%s, conflicts=%d)",
            action_id,
            tool_name,
            len(conflicts),
        )
        return {
            "status": "approval_required",
            "action_id": action_id,
            "policy": "allow_overlap",
            "provider": provider_name,
            "calendar_id": resolved_calendar_id,
            "conflicts": conflicts,
            "message": (
                f"Overlap detected with {len(conflicts)} existing event(s). "
                "The write has been queued for approval before execution."
            ),
        }

    async def _check_conflicts(
        self,
        *,
        provider: CalendarProvider,
        calendar_id: str,
        candidate: CalendarEventCreate,
        conflict_policy: CalendarConflictPolicy,
        ignore_start_at: datetime | None = None,
        ignore_end_at: datetime | None = None,
    ) -> dict[str, Any]:
        if candidate.end_at <= candidate.start_at:
            raise ValueError("end_at must be after start_at")

        conflicts = await provider.find_conflicts(calendar_id=calendar_id, candidate=candidate)
        if ignore_start_at is not None and ignore_end_at is not None:
            conflicts = [
                conflict
                for conflict in conflicts
                if not (conflict.start_at == ignore_start_at and conflict.end_at == ignore_end_at)
            ]

        conflict_payload = [self._conflict_to_payload(conflict) for conflict in conflicts]
        if not conflict_payload:
            return {"status": "clear", "conflicts": [], "suggested_slots": []}

        if conflict_policy == "allow_overlap":
            return {
                "status": "allow_overlap",
                "conflicts": conflict_payload,
                "suggested_slots": [],
            }

        suggested_slots: list[dict[str, str]] = []
        if conflict_policy == "suggest":
            scheduling_preferences = await self._load_scheduling_preferences()
            suggested_slots = self._build_suggested_slots(
                candidate,
                conflicts,
                count=DEFAULT_CONFLICT_SUGGESTION_COUNT,
                scheduling_preferences=scheduling_preferences,
            )
        return {
            "status": "conflict",
            "conflicts": conflict_payload,
            "suggested_slots": suggested_slots,
        }

    @staticmethod
    def _time_window_changed(
        *,
        existing_event: CalendarEvent,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> bool:
        if start_at is not None and start_at != existing_event.start_at:
            return True
        if end_at is not None and end_at != existing_event.end_at:
            return True
        return False

    @staticmethod
    def _conflict_to_payload(conflict: CalendarEvent) -> dict[str, str]:
        return {
            "event_id": conflict.event_id,
            "title": conflict.title,
            "start_at": conflict.start_at.isoformat(),
            "end_at": conflict.end_at.isoformat(),
            "timezone": conflict.timezone,
        }

    async def _load_scheduling_preferences(self) -> SchedulingPreferences | None:
        """Load the owner's scheduling-availability preferences for slot ranking.

        Returns None when the store is unavailable or unconfigured, in which case
        slot suggestions apply no life-availability filtering (back-compat).
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        try:
            row = await get_scheduling_preferences(pool)
        except Exception:  # fail-open: never block conflict resolution on prefs
            logger.warning("Failed to load owner scheduling preferences", exc_info=True)
            return None
        return SchedulingPreferences.from_row(row)

    def _resolve_free_busy_calendar_ids(self, calendar_ids: list[str] | None) -> list[str]:
        """Resolve which calendars an availability query should span.

        An explicit list is validated through ``_resolve_calendar_id`` (rejecting
        unknown ids). With no list, the query spans every connected calendar,
        falling back to the default-target calendar before discovery completes.
        """
        if calendar_ids:
            return [self._resolve_calendar_id(calendar_id) for calendar_id in calendar_ids]
        if self._all_provider_calendar_ids:
            return list(self._all_provider_calendar_ids)
        return [self._resolve_calendar_id(None)]

    async def _find_free_slots(
        self,
        *,
        duration_minutes: int,
        search_start: datetime,
        search_end: datetime,
        calendar_ids: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Rank open slots over a window; backs ``calendar_find_free_slots``.

        Read-only: queries the provider's free/busy, subtracts busy windows,
        clips to owner scheduling preferences, and ranks the duration-sized open
        slots. Raises ``ValueError`` on invalid duration/window/constraints;
        returns a structured provider error dictionary (rather than a successful
        empty result) when a free/busy authentication/request failure occurs.
        The workspace API maps that error to its fixed content-blind unavailable
        reason.
        """
        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be a positive integer")
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        if search_end <= search_start:
            raise ValueError("search_end must be after search_start")

        part_of_day, avoid_weekdays = _parse_slot_constraints(constraints)
        provider = self._require_provider()
        resolved_ids = self._resolve_free_busy_calendar_ids(calendar_ids)

        try:
            busy = await provider.get_free_busy(
                calendar_ids=resolved_ids,
                start_at=search_start,
                end_at=search_end,
            )
        except CalendarAuthError as exc:
            logger.warning(
                "calendar_find_free_slots free/busy lookup failed: %s", exc, exc_info=True
            )
            error_dict = _build_structured_error(
                exc, provider=provider.name, calendar_id=",".join(resolved_ids)
            )
            error_dict["slots"] = []
            error_dict["duration_minutes"] = duration_minutes
            error_dict["calendar_ids"] = resolved_ids
            return error_dict

        scheduling_preferences = await self._load_scheduling_preferences()
        slots = _compute_free_slots(
            search_start=search_start,
            search_end=search_end,
            busy=busy,
            duration=timedelta(minutes=duration_minutes),
            scheduling_preferences=scheduling_preferences,
            part_of_day=part_of_day,
            avoid_weekdays=avoid_weekdays,
            limit=limit,
        )
        return {
            "slots": slots,
            "duration_minutes": duration_minutes,
            "calendar_ids": resolved_ids,
        }

    @staticmethod
    def _build_suggested_slots(
        candidate: CalendarEventCreate,
        conflicts: list[CalendarEvent],
        *,
        count: int,
        scheduling_preferences: SchedulingPreferences | None = None,
    ) -> list[dict[str, str]]:
        if count < 1:
            return []
        duration = candidate.end_at - candidate.start_at
        if duration <= timedelta(0):
            return []

        last_conflict_end = max(conflict.end_at for conflict in conflicts)
        cursor = max(candidate.start_at, last_conflict_end)

        # When the owner has scheduling-availability preferences, skip candidate
        # slots that start before the earliest meeting time, end after the latest,
        # fall on a disallowed weekday, or overlap a no-meeting block. Stepping in
        # 15-minute increments is bounded by a search horizon so an unsatisfiable
        # constraint set terminates rather than looping forever.
        apply_prefs = scheduling_preferences is not None and scheduling_preferences.has_constraints
        search_deadline = cursor + _SUGGESTION_SEARCH_HORIZON if apply_prefs else None
        step = timedelta(minutes=15)

        suggestions: list[dict[str, str]] = []
        while len(suggestions) < count:
            suggestion_start = cursor
            suggestion_end = suggestion_start + duration
            if apply_prefs:
                if suggestion_start > search_deadline:
                    break
                if not scheduling_preferences.allows(suggestion_start, suggestion_end):
                    cursor = suggestion_start + step
                    continue
            suggestions.append(
                {
                    "start_at": suggestion_start.isoformat(),
                    "end_at": suggestion_end.isoformat(),
                    "timezone": candidate.timezone or "UTC",
                }
            )
            cursor = suggestion_end + step
        return suggestions
