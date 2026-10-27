"""Calendar provider implementation."""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any

from butlers.modules.calendar_response import (
    CalendarResponseError,
    SelfResponseSnapshot,
)

from .constants import DEFAULT_SYNC_WINDOW_DAYS
from .models import BusyWindow, CalendarEvent, CalendarEventCreate, CalendarEventUpdate


class CalendarProvider(abc.ABC):
    """Provider abstraction used by calendar tools."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., ``google``)."""
        ...

    @abc.abstractmethod
    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        """Return events in a time window."""
        ...

    @abc.abstractmethod
    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        """Fetch a single event by id."""
        ...

    async def get_response_calendar(self, *, calendar_id: str) -> dict[str, Any]:
        """Read the current exact calendar-list access role, without a fallback."""
        raise CalendarResponseError("response_provider_unavailable")

    async def get_response_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        """Read the full explicit participant/occurrence resource for admission."""
        raise CalendarResponseError("response_provider_unavailable")

    async def prepare_response_write(self) -> str:
        """Finish credential readiness before any durable write-start claim."""
        raise CalendarResponseError("response_provider_unavailable")

    async def respond_to_invitation(
        self,
        *,
        calendar_id: str,
        before: SelfResponseSnapshot,
        response_status: str,
        send_updates: str,
        access_token: str,
    ) -> dict[str, Any]:
        """Issue one conditional participant-only request, with no write retry."""
        raise CalendarResponseError("response_provider_unavailable")

    @abc.abstractmethod
    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        """Create an event."""
        ...

    @abc.abstractmethod
    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        """Update an event."""
        ...

    @abc.abstractmethod
    async def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        send_updates: str | None = None,
    ) -> None:
        """Delete (or cancel) an event.

        ``send_updates`` controls attendee notification:
        - ``None`` / ``"none"`` — no notifications (default for butler-managed events).
        - ``"all"`` — notify all attendees (sends cancellation emails).
        - ``"externalOnly"`` — notify only non-organizer-domain attendees.
        """
        ...

    @abc.abstractmethod
    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Add attendees to an existing event, deduplicating by email."""
        ...

    @abc.abstractmethod
    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Remove attendees from an existing event by email."""
        ...

    @abc.abstractmethod
    async def get_free_busy(
        self,
        *,
        calendar_ids: list[str],
        start_at: datetime,
        end_at: datetime,
        timezone: str | None = None,
    ) -> list[BusyWindow]:
        """Return merged busy windows across ``calendar_ids`` over the window.

        Generalizes the single-calendar, candidate-window free/busy lookup that
        previously lived only inside :meth:`find_conflicts`. Busy windows from
        every requested calendar are unioned, sorted by start, and overlapping or
        touching intervals are merged. Returns an empty list when nothing is busy.
        """
        ...

    @abc.abstractmethod
    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        """Find overlapping events for a candidate event."""
        ...

    @abc.abstractmethod
    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        """Fetch incremental changes since the given sync token.

        When ``sync_token`` is ``None`` a full sync is performed over the last
        ``full_sync_window_days`` days.

        Returns:
            A 3-tuple of:
            - ``updated_events``: list of new/updated CalendarEvent objects.
            - ``cancelled_event_ids``: list of event_id strings that were cancelled.
            - ``next_sync_token``: opaque token to pass on the next call.

        Raises:
            ``CalendarSyncTokenExpiredError`` when the provider reports the
            sync token is no longer valid.  The caller should retry with
            ``sync_token=None`` to perform a full sync.
        """
        ...

    async def get_calendar_summary(self, calendar_id: str) -> str | None:
        """Return the human-readable summary/name for a calendar, or ``None``."""
        return None

    async def get_recurrence(self, *, calendar_id: str, event_id: str) -> list[str]:
        """Return the raw provider recurrence array (RRULE / EXDATE / RDATE lines).

        Used by occurrence-scoped edits that must preserve existing exclusions.
        Providers that do not support occurrence-scoped recurrence editing raise
        ``NotImplementedError``.
        """
        raise NotImplementedError("provider does not support occurrence-scoped recurrence editing")

    async def set_recurrence(
        self,
        *,
        calendar_id: str,
        event_id: str,
        recurrence: list[str],
        send_updates: str | None = None,
    ) -> CalendarEvent:
        """Replace the master event's recurrence array and return the updated event."""
        raise NotImplementedError("provider does not support occurrence-scoped recurrence editing")

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Release provider resources."""
        ...
