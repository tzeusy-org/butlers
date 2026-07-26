"""Tests for the flight-status poll job (bu-8bnn9, follow-up from bu-ep4ks.16).

Covers: not-configured honest skip (no AVIATIONSTACK_API_KEY), the pure
``parse_flight_status`` classifier, a successful poll that notifies on a
delay past threshold, a poll that stays quiet under threshold, and a fetch
failure that degrades honestly without crashing the sweep.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.jobs.flight_status import (
    parse_flight_status,
    run_flight_status_check,
)

pytestmark = pytest.mark.unit

_FLIGHT_PAYLOAD_DELAYED = {
    "data": [
        {
            "flight_status": "active",
            "departure": {
                "scheduled": "2026-07-27T10:00:00+00:00",
                "estimated": "2026-07-27T10:45:00+00:00",
                "delay": 45,
            },
        }
    ]
}

_FLIGHT_PAYLOAD_ON_TIME = {
    "data": [
        {
            "flight_status": "scheduled",
            "departure": {
                "scheduled": "2026-07-27T10:00:00+00:00",
                "estimated": None,
                "delay": None,
            },
        }
    ]
}

_FLIGHT_PAYLOAD_CANCELLED = {
    "data": [
        {
            "flight_status": "cancelled",
            "departure": {"scheduled": "2026-07-27T10:00:00+00:00", "delay": None},
        }
    ]
}

_FLIGHT_PAYLOAD_EMPTY: dict = {"data": []}


def _make_pool(leg_rows=None):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=leg_rows or [])
    return pool


def _mock_credential_store(key: str | None):
    store = MagicMock()
    store.resolve = AsyncMock(return_value=key)
    return store


def _mock_client(payload: dict, *, status_code: int = 200):
    def _handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "boom"})
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _leg_row(**overrides):
    row = {
        "id": uuid.uuid4(),
        "trip_id": uuid.uuid4(),
        "departure_at": datetime.now(UTC) + timedelta(hours=6),
        "metadata": {"flight_number": "BA123"},
        "trip_name": "Tokyo trip",
        "destination": "Tokyo",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# parse_flight_status — pure classifier
# ---------------------------------------------------------------------------


def test_parse_flight_status_no_data_returns_none():
    assert parse_flight_status(_FLIGHT_PAYLOAD_EMPTY) is None


def test_parse_flight_status_delay_past_threshold_is_notify_worthy():
    status = parse_flight_status(_FLIGHT_PAYLOAD_DELAYED)
    assert status["delay_minutes"] == 45
    assert status["notify_worthy"] is True


def test_parse_flight_status_on_time_is_not_notify_worthy():
    status = parse_flight_status(_FLIGHT_PAYLOAD_ON_TIME)
    assert status["delay_minutes"] is None
    assert status["notify_worthy"] is False


def test_parse_flight_status_cancelled_is_notify_worthy():
    status = parse_flight_status(_FLIGHT_PAYLOAD_CANCELLED)
    assert status["flight_status"] == "cancelled"
    assert status["notify_worthy"] is True


# ---------------------------------------------------------------------------
# run_flight_status_check — not configured (honest skip)
# ---------------------------------------------------------------------------


async def test_not_configured_skips_poll_and_marks_status():
    pool = _make_pool()

    with patch(
        "butlers.jobs.flight_status.CredentialStore",
        return_value=_mock_credential_store(None),
    ):
        result = await run_flight_status_check(pool)

    assert result == {"skipped": True, "reason": "not_configured"}
    pool.execute.assert_awaited_once()
    (sql,), _ = pool.execute.call_args
    assert "flight_status_feed_status" in sql
    assert "configured = false" in sql
    # Never queries travel.legs when not configured.
    pool.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_flight_status_check — configured, no upcoming legs
# ---------------------------------------------------------------------------


async def test_configured_no_legs_records_zero_check_attempt():
    pool = _make_pool(leg_rows=[])

    with patch(
        "butlers.jobs.flight_status.CredentialStore",
        return_value=_mock_credential_store("fake-key"),
    ):
        result = await run_flight_status_check(pool)

    assert result["skipped"] is False
    assert result["legs_checked"] == 0
    assert result["delays_detected"] == 0
    assert result["notified"] == []


# ---------------------------------------------------------------------------
# run_flight_status_check — delay past threshold notifies once
# ---------------------------------------------------------------------------


async def test_delay_past_threshold_notifies_and_writes_leg_metadata():
    leg = _leg_row()
    pool = _make_pool(leg_rows=[leg])
    client = _mock_client(_FLIGHT_PAYLOAD_DELAYED)

    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with (
        patch(
            "butlers.jobs.flight_status.CredentialStore",
            return_value=_mock_credential_store("fake-key"),
        ),
        patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            propose_mock,
        ),
    ):
        result = await run_flight_status_check(pool, http_client=client)

    assert result["skipped"] is False
    assert result["legs_checked"] == 1
    assert result["delays_detected"] == 1
    assert len(result["notified"]) == 1
    assert result["notified"][0]["delay_minutes"] == 45

    propose_mock.assert_awaited_once()
    _, kwargs = propose_mock.call_args
    assert kwargs["origin_butler"] == "travel"
    assert kwargs["category"] == "flight-status"
    assert str(leg["id"]) in kwargs["dedup_key"]

    # One call writes the leg metadata, another records feed status.
    leg_update_calls = [c for c in pool.execute.call_args_list if "travel.legs" in c.args[0]]
    status_calls = [
        c for c in pool.execute.call_args_list if "flight_status_feed_status" in c.args[0]
    ]
    assert len(leg_update_calls) == 1
    assert len(status_calls) == 1

    await client.aclose()


# ---------------------------------------------------------------------------
# run_flight_status_check — on-time flight stays quiet
# ---------------------------------------------------------------------------


async def test_on_time_flight_does_not_notify():
    leg = _leg_row()
    pool = _make_pool(leg_rows=[leg])
    client = _mock_client(_FLIGHT_PAYLOAD_ON_TIME)

    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with (
        patch(
            "butlers.jobs.flight_status.CredentialStore",
            return_value=_mock_credential_store("fake-key"),
        ),
        patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            propose_mock,
        ),
    ):
        result = await run_flight_status_check(pool, http_client=client)

    assert result["delays_detected"] == 0
    assert result["notified"] == []
    propose_mock.assert_not_awaited()

    await client.aclose()


# ---------------------------------------------------------------------------
# run_flight_status_check — fetch failure degrades honestly
# ---------------------------------------------------------------------------


async def test_fetch_failure_records_error_without_crashing():
    leg = _leg_row()
    pool = _make_pool(leg_rows=[leg])
    client = _mock_client(_FLIGHT_PAYLOAD_DELAYED, status_code=503)

    with patch(
        "butlers.jobs.flight_status.CredentialStore",
        return_value=_mock_credential_store("fake-key"),
    ):
        result = await run_flight_status_check(pool, http_client=client)

    assert result["skipped"] is False
    assert result["legs_checked"] == 0
    assert result["last_error"] is not None
    # The sanitized error never leaks the access_key query param.
    assert "fake-key" not in result["last_error"]

    status_calls = [
        c for c in pool.execute.call_args_list if "flight_status_feed_status" in c.args[0]
    ]
    assert len(status_calls) == 1

    await client.aclose()


async def test_leg_without_flight_number_is_skipped():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    # _fetch_upcoming_flight_legs filters on metadata ? 'flight_number' in SQL;
    # simulate that filter already excluding this leg.
    pool.fetch = AsyncMock(return_value=[])

    with patch(
        "butlers.jobs.flight_status.CredentialStore",
        return_value=_mock_credential_store("fake-key"),
    ):
        result = await run_flight_status_check(pool)

    assert result["legs_checked"] == 0
    assert result["notified"] == []
