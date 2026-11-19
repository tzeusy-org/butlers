"""Google-sent calendar mail passes the endpoint custody fence (bu-s11n0s.94).

A write with ``sendUpdates`` ``all``/``externalOnly`` makes Google email the organizer and guests
from the owner's account. Each such write must refuse before provider start when any recipient
is held or custody cannot be read; a write that sends no mail is never fenced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.core.custody_egress import CustodyEgressRefused
from butlers.modules.calendar import (
    AttendeeInfo,
    CalendarConfig,
    CalendarEvent,
    _GoogleOAuthCredentials,
    _GoogleProvider,
)
from butlers.modules.calendar.custody import (
    google_resource_recipients,
    require_calendar_mail_custody_clear,
    sends_mail,
)
from butlers.modules.calendar_response import (
    CalendarResponseCoordinator,
    CalendarResponseError,
)

pytestmark = pytest.mark.unit

_HELD_GUEST = "lost-phone@example.com"


def _custody_pool(held: bool | Exception) -> MagicMock:
    pool = MagicMock()
    if isinstance(held, Exception):
        pool.fetchval = AsyncMock(side_effect=held)
    else:
        pool.fetchval = AsyncMock(return_value=held)
    return pool


def _asked_emails(pool: MagicMock) -> list[str]:
    _sql, predicates, objects = pool.fetchval.await_args.args
    assert set(predicates) == {"has-email"}
    return list(objects)


def _provider(pool) -> _GoogleProvider:
    return _GoogleProvider(
        CalendarConfig(provider="google", timezone="UTC"),
        _GoogleOAuthCredentials(
            client_id="client-id", client_secret="client-secret", refresh_token="refresh-token"
        ),
        pool=pool,
    )


def _event(*guests: str) -> CalendarEvent:
    return CalendarEvent(
        event_id="event",
        title="Lunch",
        start_at=datetime(2026, 11, 20, 12, tzinfo=UTC),
        end_at=datetime(2026, 11, 20, 13, tzinfo=UTC),
        timezone="UTC",
        organizer="owner@example.com",
        attendees=[AttendeeInfo(email=guest) for guest in guests],
    )


_UPDATED = {
    "id": "event",
    "summary": "Lunch",
    "start": {"dateTime": "2026-11-20T12:00:00Z"},
    "end": {"dateTime": "2026-11-20T13:00:00Z"},
}


# --------------------------------------------------------------------------- the helper


@pytest.mark.parametrize(
    ("send_updates", "mails"),
    [("all", True), ("externalOnly", True), (" all ", True), ("none", False), (None, False)],
)
def test_only_all_and_external_only_make_google_send_mail(send_updates, mails):
    assert sends_mail(send_updates) is mails


def test_a_raw_resource_names_its_organizer_and_guests():
    resource = {
        "organizer": {"email": "owner@example.com"},
        "attendees": [{"email": "a@example.com"}, {"email": "b@example.com"}],
    }
    assert google_resource_recipients(resource) == [
        "owner@example.com",
        "a@example.com",
        "b@example.com",
    ]
    assert google_resource_recipients(None) == []


async def test_a_silent_write_is_never_fenced_even_without_a_pool():
    await require_calendar_mail_custody_clear(None, "none", [_HELD_GUEST])
    await require_calendar_mail_custody_clear(None, None, [_HELD_GUEST])


async def test_a_mailing_write_with_no_recipient_passes():
    await require_calendar_mail_custody_clear(None, "all", [])


@pytest.mark.parametrize(
    ("pool", "recipients", "reason"),
    [
        (_custody_pool(True), [_HELD_GUEST], "endpoint_held"),
        (_custody_pool(RuntimeError("down")), [_HELD_GUEST], "eligibility_unknown"),
        (None, [_HELD_GUEST], "eligibility_unknown"),
        (_custody_pool(False), ["not-a-mailbox"], "eligibility_unknown"),
    ],
)
async def test_a_mailing_write_fails_held_or_unknown(pool, recipients, reason):
    with pytest.raises(CustodyEgressRefused) as refused:
        await require_calendar_mail_custody_clear(pool, "all", recipients)
    assert refused.value.reason == reason


# --------------------------------------------------------------------------- provider writes


async def test_adding_a_held_guest_with_invites_refuses_before_the_patch():
    pool = _custody_pool(True)
    provider = _provider(pool)
    provider.get_event = AsyncMock(return_value=_event("friend@example.com"))
    provider._request_google_json = AsyncMock(return_value=_UPDATED)

    with pytest.raises(CustodyEgressRefused) as refused:
        await provider.add_attendees(
            calendar_id="primary", event_id="event", attendees=[_HELD_GUEST], send_updates="all"
        )

    assert refused.value.reason == "endpoint_held"
    provider._request_google_json.assert_not_awaited()
    assert _asked_emails(pool) == ["owner@example.com", "friend@example.com", _HELD_GUEST]


async def test_adding_an_unheld_guest_with_invites_still_writes():
    pool = _custody_pool(False)
    provider = _provider(pool)
    provider.get_event = AsyncMock(return_value=_event())
    provider._request_google_json = AsyncMock(return_value=_UPDATED)

    await provider.add_attendees(
        calendar_id="primary",
        event_id="event",
        attendees=["friend@example.com"],
        send_updates="all",
    )

    provider._request_google_json.assert_awaited_once()
    call = provider._request_google_json.await_args
    assert call is not None
    assert call.kwargs["params"] == {"sendUpdates": "all"}


async def test_a_silent_attendee_write_never_asks_custody():
    pool = _custody_pool(True)
    provider = _provider(pool)
    provider.get_event = AsyncMock(return_value=_event(_HELD_GUEST))
    provider._request_google_json = AsyncMock(return_value=_UPDATED)

    await provider.remove_attendees(
        calendar_id="primary", event_id="event", attendees=[_HELD_GUEST], send_updates="none"
    )

    pool.fetchval.assert_not_awaited()
    provider._request_google_json.assert_awaited_once()


async def test_removing_a_held_guest_with_a_cancellation_mail_refuses():
    pool = _custody_pool(True)
    provider = _provider(pool)
    provider.get_event = AsyncMock(return_value=_event(_HELD_GUEST))
    provider._request_google_json = AsyncMock(return_value=_UPDATED)

    with pytest.raises(CustodyEgressRefused):
        await provider.remove_attendees(
            calendar_id="primary", event_id="event", attendees=[_HELD_GUEST], send_updates="all"
        )

    provider._request_google_json.assert_not_awaited()


@pytest.mark.parametrize("operation", ["delete", "recurrence"])
async def test_a_mailing_delete_or_recurrence_change_reads_the_guests_first(operation):
    pool = _custody_pool(True)
    provider = _provider(pool)
    provider.get_event = AsyncMock(return_value=_event(_HELD_GUEST))
    provider._request_with_bearer = AsyncMock(return_value=httpx.Response(204))
    provider._request_google_json = AsyncMock(return_value=_UPDATED)

    with pytest.raises(CustodyEgressRefused):
        if operation == "delete":
            await provider.delete_event(calendar_id="primary", event_id="event", send_updates="all")
        else:
            await provider.set_recurrence(
                calendar_id="primary",
                event_id="event",
                recurrence=["RRULE:FREQ=DAILY"],
                send_updates="externalOnly",
            )

    provider._request_with_bearer.assert_not_awaited()
    provider._request_google_json.assert_not_awaited()


async def test_a_silent_delete_reads_nothing_and_asks_nothing():
    pool = _custody_pool(True)
    provider = _provider(pool)
    provider.get_event = AsyncMock()
    provider._request_with_bearer = AsyncMock(return_value=httpx.Response(204))

    await provider.delete_event(calendar_id="primary", event_id="event", send_updates="none")

    provider.get_event.assert_not_awaited()
    pool.fetchval.assert_not_awaited()
    provider._request_with_bearer.assert_awaited_once()


# --------------------------------------------------------------------------- invitation response


def _coordinator(pool, raw) -> tuple[CalendarResponseCoordinator, AsyncMock]:
    provider = SimpleNamespace(get_response_event=AsyncMock(return_value=raw))
    module = SimpleNamespace(_db=SimpleNamespace(pool=pool), _require_provider=lambda: provider)
    return CalendarResponseCoordinator(module), provider.get_response_event


_TARGET = {"calendar_id": "primary", "provider_event_id": "event"}
_INVITE = {
    "id": "event",
    "organizer": {"email": _HELD_GUEST},
    "attendees": [{"email": "owner@example.com", "self": True}],
}


async def test_a_response_mailing_a_held_organizer_fails_closed_before_start():
    coordinator, read = _coordinator(_custody_pool(True), _INVITE)

    with pytest.raises(CalendarResponseError) as refused:
        await coordinator._require_response_mail_unheld(_TARGET, "all")

    assert refused.value.code == "response_custody_endpoint_held"
    read.assert_awaited_once_with(calendar_id="primary", event_id="event")


async def test_a_silent_response_reads_nothing():
    coordinator, read = _coordinator(_custody_pool(True), _INVITE)

    await coordinator._require_response_mail_unheld(_TARGET, "none")

    read.assert_not_awaited()
