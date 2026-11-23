"""Quarantine for provider-direct reads (REQ-module-calendar-041).

``calendar_list_events`` and ``calendar_get_event`` read the provider, not the stored rows, so the
stored-row filters do not cover them. Here each provider event is checked with the same predicate
(``is_quarantined_invitation``) against the organizer verdict stamped on its stored row. An event
that is quarantined, or that has no stored stamp yet, or whose stamp could not be read, is replaced
by a stub holding only its time and verdict: the third-party title, description, attendees and
location never reach the model.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from butlers.core.temporal.calendar_provenance import is_quarantined_invitation

from .models import CalendarEvent

logger = logging.getLogger(__name__)

QUARANTINED_TITLE = "[quarantined invitation]"


def _keys(event_id: str) -> list[str]:
    """The stored ``origin_ref`` candidates: the event id and, for an instance, its series id."""
    base = event_id.split("_", 1)[0]
    return [event_id] if base == event_id or not base else [event_id, base]


def _as_metadata(event: CalendarEvent, verdict: str | None) -> dict[str, Any]:
    return {
        "butler_generated": event.butler_generated,
        "organizer": event.organizer,
        "organizer_verdict": verdict,
        "attendees": [
            {
                "email": attendee.email,
                "self": attendee.self_,
                "organizer": attendee.organizer,
                "response_status": attendee.response_status.value,
            }
            for attendee in event.attendees
        ],
    }


async def _stored_verdicts(pool: Any, events: list[CalendarEvent]) -> dict[str, str | None]:
    """Organizer verdict by ``origin_ref``; raises when the stored rows cannot be read."""
    refs = sorted({key for event in events for key in _keys(event.event_id)})
    if not refs:
        return {}
    rows = await pool.fetch(
        """
        -- quarantine-exempt: reads only the stamped verdict, never event text
        SELECT origin_ref, metadata ->> 'organizer_verdict' AS verdict
        FROM calendar_events
        WHERE origin_ref = ANY($1::text[])
        """,
        refs,
    )
    verdicts: dict[str, str | None] = {}
    for row in rows:
        ref, verdict = row["origin_ref"], row["verdict"]
        # Two stored rows for one id: the less trusted one wins.
        if ref in verdicts and verdicts[ref] != verdict:
            verdicts[ref] = None
        else:
            verdicts[ref] = verdict
    return verdicts


def _verdict_for(event_id: str, verdicts: dict[str, str | None]) -> str | None:
    """The stored verdict for an event. A recurring instance with no stored row of its own
    inherits its series' verdict; one that has a row, even an unstamped one, keeps that row's
    answer, so it never borrows trust from the series."""
    keys = _keys(event_id)
    for key in keys:
        if key in verdicts:
            return verdicts[key]
    return None


def _stub(event: CalendarEvent, verdict: str | None) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "title": QUARANTINED_TITLE,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "timezone": event.timezone,
        "all_day": event.all_day,
        "quarantined": True,
        "organizer_verdict": verdict or "unknown",
    }


async def guard_provider_events(
    pool: Any,
    events: Iterable[CalendarEvent],
    to_payload: Callable[[CalendarEvent], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Payloads for ``events``, with each quarantined invitation reduced to a stub.

    Fails closed: when the stored verdicts cannot be read, every event is judged as unstamped.
    """
    event_list = list(events)
    verdicts: dict[str, str | None] = {}
    if pool is not None and event_list:
        try:
            verdicts = await _stored_verdicts(pool, event_list)
        except Exception:
            logger.warning("organizer verdict lookup failed; quarantining unstamped", exc_info=True)
    payloads: list[dict[str, Any]] = []
    for event in event_list:
        verdict = _verdict_for(event.event_id, verdicts)
        if is_quarantined_invitation(_as_metadata(event, verdict)):
            payloads.append(_stub(event, verdict))
        else:
            payloads.append(to_payload(event))
    return payloads


def pool_of(module: Any) -> Any:
    """The module's database pool, or None when it has none."""
    db = getattr(module, "_db", None)
    return getattr(db, "pool", None) if db is not None else None


async def guard_event_payload(
    pool: Any,
    event: CalendarEvent,
    to_payload: Callable[[CalendarEvent], dict[str, Any]],
) -> dict[str, Any]:
    """One event's payload, or its quarantine stub."""
    return (await guard_provider_events(pool, [event], to_payload))[0]


async def withhold_quarantined_pre_state(
    pool: Any, event: CalendarEvent | None, result: dict[str, Any]
) -> dict[str, Any]:
    """The tool result with the event's pre-image withheld when it was a quarantined invitation.

    The full pre-image stays in the persisted action result (the owner's dashboard undo needs
    it); only the copy handed back to the model drops the third-party text.
    """
    if event is None or "pre_state" not in result:
        return result
    stub = await guard_event_payload(pool, event, lambda _: {})
    if not stub.get("quarantined"):
        return result
    return {**result, "pre_state": {"event_id": event.event_id, "quarantined": True}}
