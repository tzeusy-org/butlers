"""Google ``eventType`` and ``workingLocationProperties`` reach the projection (bu-s11n0s.4 S1).

The payloads are SYNTHETIC: shaped after the public Calendar API v3 Events resource reference
(``eventType``, ``workingLocationProperties.{type, homeOffice, officeLocation, customLocation}``),
not recorded from a real account. Real-Postgres readback lives in
``tests/integration/test_calendar_event_type_projection.py``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.calendar import CalendarModule
from butlers.modules.calendar.google import _GoogleOAuthCredentials, _GoogleProvider
from butlers.modules.calendar.google_mapping import (
    _build_google_event_body,
    _build_google_event_patch_body,
    _google_event_to_calendar_event,
)
from butlers.modules.calendar.models import (
    CalendarConfig,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
)

pytestmark = pytest.mark.unit


def _payload(**extra: Any) -> dict[str, Any]:
    return {
        "id": "evt-typed",
        "summary": "Block",
        "start": {"dateTime": "2026-07-01T09:00:00+00:00"},
        "end": {"dateTime": "2026-07-01T17:00:00+00:00"},
        **extra,
    }


def _parse(**extra: Any) -> CalendarEvent:
    event = _google_event_to_calendar_event(_payload(**extra), fallback_timezone="UTC")
    assert event is not None
    return event


# -- eventType -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    ["default", "outOfOffice", "focusTime", "workingLocation", "fromGmail", "birthday"],
)
def test_every_documented_event_type_is_kept_verbatim(event_type: str) -> None:
    assert _parse(eventType=event_type).event_type == event_type


def test_a_future_event_type_survives_as_observed() -> None:
    assert _parse(eventType="someNewType").event_type == "someNewType"


@pytest.mark.parametrize(
    "raw",
    [None, 7, "", "   ", "x" * 65, "outOf\nOffice"],
    ids=["missing", "non-string", "empty", "blank", "overlong", "control-char"],
)
def test_an_unusable_event_type_is_default(raw: Any) -> None:
    extra = {} if raw is None else {"eventType": raw}
    assert _parse(**extra).event_type == "default"


def test_event_types_are_case_sensitive_and_never_inferred() -> None:
    # Not a recognized spelling: kept as a future value, never folded into outOfOffice.
    assert _parse(eventType="outofoffice").event_type == "outofoffice"
    # A title never makes a type.
    assert _parse(summary="Out of office").event_type == "default"


def test_a_bounded_event_type_at_the_limit_is_kept() -> None:
    assert _parse(eventType="t" * 64).event_type == "t" * 64


# -- workingLocationProperties ------------------------------------------------------------


def _location(properties: Any, event_type: str = "workingLocation") -> dict[str, str] | None:
    return _parse(eventType=event_type, workingLocationProperties=properties).working_location


def test_home_office_has_the_fixed_label() -> None:
    assert _location({"type": "homeOffice", "homeOffice": {}}) == {
        "type": "homeOffice",
        "label": "home office",
    }


@pytest.mark.parametrize("branch", ["officeLocation", "customLocation"])
def test_office_and_custom_locations_keep_their_cleaned_label(branch: str) -> None:
    properties = {
        "type": branch,
        branch: {"label": "  HQ\x07 Floor​ 3 ", "buildingId": "b1", "deskId": "d9"},
    }
    location = _location(properties)
    assert location is not None
    assert location["type"] == branch
    assert location["label"] == "HQ Floor 3"
    assert set(location) == {"type", "label"}


def test_a_long_label_is_bounded() -> None:
    location = _location({"type": "customLocation", "customLocation": {"label": "a" * 300}})
    assert location == {"type": "customLocation", "label": "a" * 256}


@pytest.mark.parametrize(
    "properties",
    [
        None,
        "homeOffice",
        {"type": "homeOffice"},  # the matching branch is missing
        {"type": "officeLocation", "customLocation": {"label": "Cafe"}},  # mismatched branch
        {"type": "officeLocation", "officeLocation": {"label": "  "}},
        {"type": "officeLocation", "officeLocation": {"buildingId": "b1"}},  # ids only
        {"type": "customLocation", "customLocation": {"label": 5}},
        {"type": "somewhereElse", "somewhereElse": {"label": "Moon"}},
        {"customLocation": {"label": "No type"}},
    ],
)
def test_missing_or_mismatched_location_properties_are_none(properties: Any) -> None:
    assert _location(properties) is None


def test_location_properties_count_only_on_working_location_events() -> None:
    properties = {"type": "customLocation", "customLocation": {"label": "Cafe"}}
    assert _location(properties, event_type="default") is None
    assert _location(properties, event_type="outOfOffice") is None


def test_the_event_location_and_summary_are_never_substituted() -> None:
    event = _parse(eventType="workingLocation", location="Office HQ", summary="Office")
    assert event.working_location is None


# -- read-side only ------------------------------------------------------------------------


def test_the_tool_payload_carries_the_typed_fields() -> None:
    event = _parse(
        eventType="workingLocation",
        workingLocationProperties={"type": "homeOffice", "homeOffice": {}},
    )
    payload = CalendarModule._event_to_payload(event)
    assert payload["event_type"] == "workingLocation"
    assert payload["working_location"] == {"type": "homeOffice", "label": "home office"}
    assert CalendarModule._event_to_payload(_parse())["working_location"] is None


def test_no_write_path_accepts_or_sends_the_typed_fields() -> None:
    for model in (CalendarEventCreate, CalendarEventUpdate):
        assert "event_type" not in model.model_fields
        assert "working_location" not in model.model_fields
    create = CalendarEventCreate(
        title="Block",
        start_at=_parse().start_at,
        end_at=_parse().end_at,
        timezone="UTC",
    )
    body = _build_google_event_body(create)
    assert "eventType" not in body and "workingLocationProperties" not in body
    patch = _build_google_event_patch_body(CalendarEventUpdate(title="Moved"))
    assert "eventType" not in patch and "workingLocationProperties" not in patch


# -- provider requests ---------------------------------------------------------------------


def _provider() -> _GoogleProvider:
    return _GoogleProvider(
        CalendarConfig(provider="google", timezone="UTC"),
        _GoogleOAuthCredentials(
            client_id="client-id", client_secret="client-secret", refresh_token="refresh-token"
        ),
    )


async def test_list_events_requests_every_type_by_omitting_the_filter() -> None:
    provider = _provider()
    provider._request_google_json = AsyncMock(  # type: ignore[method-assign]
        return_value={"items": [_payload(eventType="outOfOffice")]}
    )
    try:
        events = await provider.list_events(calendar_id="primary")
    finally:
        await provider.shutdown()
    assert [event.event_type for event in events] == ["outOfOffice"]
    params = provider._request_google_json.await_args.kwargs["params"]
    assert "eventTypes" not in params


@pytest.mark.parametrize("sync_token", [None, "token-1"])
async def test_incremental_sync_requests_every_type_by_omitting_the_filter(
    sync_token: str | None,
) -> None:
    provider = _provider()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "items": [
            _payload(
                eventType="workingLocation",
                workingLocationProperties={"type": "homeOffice", "homeOffice": {}},
            )
        ],
        "nextSyncToken": "token-2",
    }
    provider._request_with_bearer = AsyncMock(return_value=response)  # type: ignore[method-assign]
    try:
        updated, cancelled, token = await provider.sync_incremental(
            calendar_id="primary", sync_token=sync_token
        )
    finally:
        await provider.shutdown()
    assert token == "token-2" and cancelled == []
    assert updated[0].working_location == {"type": "homeOffice", "label": "home office"}
    assert "eventTypes" not in provider._request_with_bearer.await_args.kwargs["params"]


# -- projection ----------------------------------------------------------------------------


def _projecting_module() -> tuple[CalendarModule, MagicMock]:
    module = CalendarModule()
    module._butler_name = "general"
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    module._db = SimpleNamespace(pool=pool)
    module._upsert_projection_instance = AsyncMock(return_value=uuid.uuid4())  # type: ignore[method-assign]
    module._prune_superseded_provider_instances = AsyncMock()  # type: ignore[method-assign]
    return module, pool


def _insert_args(pool: MagicMock) -> tuple[object, ...]:
    (call,) = [
        call
        for call in pool.fetchrow.await_args_list
        if "INSERT INTO calendar_events" in call.args[0]
    ]
    return call.args


async def test_provider_projection_writes_and_replaces_the_typed_fields() -> None:
    module, pool = _projecting_module()
    located = _parse(
        eventType="workingLocation",
        workingLocationProperties={"type": "customLocation", "customLocation": {"label": "Cafe"}},
    )
    await module._project_provider_changes(
        source_id=uuid.uuid4(),
        provider_name="google",
        calendar_id="primary",
        updated_events=[located],
        cancelled_ids=[],
    )
    args = _insert_args(pool)
    sql = args[0]
    assert "event_type = EXCLUDED.event_type" in sql
    # Replaced, never kept: a later update without a location clears it.
    assert "working_location = EXCLUDED.working_location" in sql
    assert "COALESCE" not in sql
    assert args[19:] == ("workingLocation", {"type": "customLocation", "label": "Cafe"})

    module, pool = _projecting_module()
    await module._project_provider_changes(
        source_id=uuid.uuid4(),
        provider_name="google",
        calendar_id="primary",
        updated_events=[_parse()],
        cancelled_ids=[],
    )
    assert _insert_args(pool)[19:] == ("default", None)
