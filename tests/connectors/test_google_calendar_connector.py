"""Condensed Google Calendar connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for event and starting_soon
- Normalized text generation (branching logic)
- DateTime parsing edge cases (all-day, Z suffix, invalid)
- Policy evaluation: blocked → buffered, allowed → submitted

[bu-35fm7]
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from butlers.connectors.google_calendar import (
    CalendarAccountConfig,
    CalendarConnectorRuntime,
    _build_ingest_envelope,
    _build_normalized_text,
    _parse_dt,
    _parse_event_start,
    build_event_envelope,
    build_starting_soon_envelope,
)
from butlers.ingestion_policy import PolicyDecision

_ENDPOINT = "google_calendar:user:test@example.com"
_OBSERVED = "2026-06-01T10:00:00+00:00"


@pytest.fixture
def account_config() -> CalendarAccountConfig:
    return CalendarAccountConfig(
        email="test@example.com",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://localhost:41100/sse",
        poll_interval_s=60,
        starting_soon_lead_minutes=15,
        starting_soon_window_hours=2,
    )


# ---------------------------------------------------------------------------
# ingest.v1 envelope contract
# ---------------------------------------------------------------------------


def test_build_event_envelope_contract() -> None:
    """event envelope carries ingest.v1 schema, calendar source, full tier."""
    env = build_event_envelope(
        {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": _OBSERVED},
            "end": {"dateTime": _OBSERVED},
            "organizer": {"email": "org@example.com"},
        },
        event_type="created",
        endpoint_identity=_ENDPOINT,
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "google_calendar"
    assert env["control"]["ingestion_tier"] == "full"
    # starting_soon variant carries interactive tier + minted external_event_id
    soon = build_starting_soon_envelope(
        {"id": "evt-2", "summary": "Standup", "start": {"dateTime": _OBSERVED}},
        lead_minutes=15,
        endpoint_identity=_ENDPOINT,
    )
    assert soon["control"]["policy_tier"] == "interactive"
    assert "starting_soon:" in soon["event"]["external_event_id"]


def test_internal_envelope_passes_parse_ingest_envelope() -> None:
    """Internally built envelope must validate against parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    env = _build_ingest_envelope(
        event_id="evt-3",
        change_type="updated",
        summary="Review",
        event={"id": "evt-3"},
        endpoint_identity=_ENDPOINT,
        observed_at=_OBSERVED,
        organizer_email="org@example.com",
        normalized_text="[UPDATED] Review",
    )
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"Envelope failed parse_ingest_envelope: {exc}")


# ---------------------------------------------------------------------------
# Normalized text (branching logic — keep)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change_type,expected_prefix",
    [("created", "[CREATED]"), ("updated", "[UPDATED]"), ("deleted", "[DELETED]")],
)
def test_normalized_text_change_type_prefix(change_type: str, expected_prefix: str) -> None:
    text = _build_normalized_text(
        change_type=change_type,
        summary="Test Event",
        start_dt=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end_dt=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
        organizer_email="org@example.com",
        attendees=[],
    )
    assert expected_prefix in text


def test_normalized_text_attendee_list_capped_at_10() -> None:
    """Attendee list is capped; overflow shown as (+N more)."""
    attendees = [f"user{i}@example.com" for i in range(15)]
    text = _build_normalized_text(
        change_type="updated",
        summary="Big meeting",
        start_dt=None,
        end_dt=None,
        organizer_email="org@example.com",
        attendees=attendees,
    )
    assert "(+5 more)" in text


# ---------------------------------------------------------------------------
# DateTime parsing edge cases (complex branching — keep)
# ---------------------------------------------------------------------------


def test_parse_dt_z_suffix() -> None:
    dt = _parse_dt("2026-06-01T10:00:00Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_parse_dt_all_day_event() -> None:
    dt = _parse_dt("2026-06-01")
    assert dt is not None
    assert dt.tzinfo == UTC


def test_parse_dt_invalid_returns_none() -> None:
    assert _parse_dt("not-a-date") is None
    assert _parse_dt("") is None


def test_parse_event_start_prefers_datetime_and_missing() -> None:
    event = {"start": {"dateTime": "2026-06-01T10:00:00Z", "date": "2026-06-01"}}
    dt = _parse_event_start(event)
    assert dt is not None
    assert dt.hour == 10
    assert _parse_event_start({}) is None


# ---------------------------------------------------------------------------
# Policy integration: blocked events buffered
# ---------------------------------------------------------------------------


async def test_blocked_event_buffered_not_ingested(
    account_config: CalendarAccountConfig,
) -> None:
    """Events blocked by policy must be buffered, not submitted to Switchboard."""
    runtime = CalendarConnectorRuntime(account_config)
    block_decision = PolicyDecision(
        action="block",
        matched_rule_id="rule-1",
        matched_rule_type="sender_domain",
        reason="blocked",
    )
    with patch.object(runtime._ingestion_policy, "evaluate", return_value=block_decision):
        event = {
            "id": "evt-blocked",
            "status": "confirmed",
            "summary": "Blocked",
            "start": {"dateTime": "2026-06-01T10:00:00Z"},
            "end": {"dateTime": "2026-06-01T11:00:00Z"},
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-02T00:00:00Z",
            "organizer": {"email": "blocked@example.com"},
        }
        ingested = await runtime._process_event(event)

    assert not ingested
    assert len(runtime._filtered_event_buffer) == 1


async def test_live_ingest_envelope_carries_canonical_idempotency_key(
    account_config: CalendarAccountConfig,
) -> None:
    """The real _process_event path must emit control.idempotency_key + ingestion_tier.

    Regression for bu-42f1i: the live envelope previously omitted these control
    fields, weakening Switchboard dedup to payload-hash bucketing. The key must be
    the canonical event-ID + Google ``updated`` timestamp derived value so two
    ingests of the same event revision dedup deterministically.
    """
    runtime = CalendarConnectorRuntime(account_config)
    event = {
        "id": "evt-live-1",
        "status": "confirmed",
        "summary": "Sync",
        "start": {"dateTime": "2026-06-01T10:00:00Z"},
        "end": {"dateTime": "2026-06-01T11:00:00Z"},
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-02T00:00:00Z",
        "organizer": {"email": "org@example.com"},
    }

    captured: list[dict] = []

    async def _capture(envelope: dict) -> None:
        captured.append(envelope)

    # Policy allows (None decision == allowed) so the success path runs.
    with (
        patch.object(runtime._ingestion_policy, "evaluate", return_value=None),
        patch.object(runtime._global_ingestion_policy, "evaluate", return_value=None),
        patch.object(runtime, "_submit_to_ingest_api", side_effect=_capture),
    ):
        assert await runtime._process_event(event) is True
        assert await runtime._process_event(dict(event)) is True

    assert len(captured) == 2
    control = captured[0]["control"]
    assert control["ingestion_tier"] == "full"
    # Canonical, event-ID + updated-timestamp derived (NOT a payload hash).
    expected_key = f"gcal:{_ENDPOINT}:evt-live-1:2026-01-02T00:00:00Z"
    assert control["idempotency_key"] == expected_key
    # Two ingests of the same event revision dedup to the same key.
    assert captured[0]["control"]["idempotency_key"] == captured[1]["control"]["idempotency_key"]
    # External thread id mirrors the event id per spec.
    assert captured[0]["event"]["external_thread_id"] == "evt-live-1"
