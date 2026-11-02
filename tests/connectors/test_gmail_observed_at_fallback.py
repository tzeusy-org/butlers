"""Gmail observed_at never falls back to the epoch. [bu-wngqm2]

Order: parsed ``internalDate``, else a valid ``Date`` header, else receipt time. A missing and an
unparseable ``internalDate`` take the same path. The source used is logged by identifiers only.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.connectors.gmail import (
    OBSERVED_AT_DATE_HEADER,
    OBSERVED_AT_INTERNAL_DATE,
    OBSERVED_AT_RECEIPT_TIME,
    GmailConnectorConfig,
    GmailConnectorRuntime,
    _resolve_observed_at,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
GOOD_DATE = "Mon, 05 Oct 2026 09:30:00 +0200"
GOOD_DATE_UTC = datetime(2026, 10, 5, 7, 30, tzinfo=UTC)
INTERNAL_MS = 1708000000000
INTERNAL_DT = datetime.fromtimestamp(INTERNAL_MS / 1000, tz=UTC)


def _message(internal_date: Any = ..., date_header: str | None = None) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": "sender@example.com"},
        {"name": "Subject", "value": "Synthetic"},
        {"name": "Message-ID", "value": "<synthetic@example.com>"},
    ]
    if date_header is not None:
        headers.append({"name": "Date", "value": date_header})
    message: dict[str, Any] = {
        "id": "msg-1",
        "threadId": "thread-1",
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"body").decode()},
        },
    }
    if internal_date is not ...:
        message["internalDate"] = internal_date
    return message


@pytest.fixture
def runtime() -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_endpoint_identity="gmail:user:test@example.com",
        gmail_client_id="synthetic-client-id",
        gmail_client_secret="synthetic-client-secret",
        gmail_refresh_token="synthetic-refresh-token",
    )
    return GmailConnectorRuntime(config, cursor_pool=MagicMock())


def test_present_internal_date_is_unchanged_even_with_a_date_header() -> None:
    observed, source = _resolve_observed_at(_message(str(INTERNAL_MS), GOOD_DATE), NOW)
    assert (observed, source) == (INTERNAL_DT, OBSERVED_AT_INTERNAL_DATE)


def test_missing_internal_date_uses_a_valid_date_header() -> None:
    observed, source = _resolve_observed_at(_message(date_header=GOOD_DATE), NOW)
    assert (observed, source) == (GOOD_DATE_UTC, OBSERVED_AT_DATE_HEADER)


@pytest.mark.parametrize("bad", ["not-a-date", "", "Mon, 99 Foo 2026 00:00:00 +0000"])
def test_missing_internal_date_with_an_invalid_header_uses_receipt_time(bad: str) -> None:
    observed, source = _resolve_observed_at(_message(date_header=bad), NOW)
    assert (observed, source) == (NOW, OBSERVED_AT_RECEIPT_TIME)


def test_missing_internal_date_without_a_header_uses_receipt_time_not_the_epoch() -> None:
    observed, source = _resolve_observed_at(_message(), NOW)
    assert (observed, source) == (NOW, OBSERVED_AT_RECEIPT_TIME)
    assert observed.year != 1970


@pytest.mark.parametrize("bad_internal", ["not-a-number", "", None, "9" * 40, "-" + "9" * 40])
def test_unparseable_internal_date_takes_the_same_path_as_missing(bad_internal: Any) -> None:
    with_header = _resolve_observed_at(_message(bad_internal, GOOD_DATE), NOW)
    without = _resolve_observed_at(_message(bad_internal), NOW)
    assert with_header == (GOOD_DATE_UTC, OBSERVED_AT_DATE_HEADER)
    assert without == (NOW, OBSERVED_AT_RECEIPT_TIME)


@pytest.mark.parametrize(
    "implausible",
    [
        "Mon, 05 Oct 1970 00:00:00 +0000",  # epoch-era
        "Mon, 01 Jan 1985 00:00:00 +0000",  # before the 1990 floor
        "Sat, 01 Jan 2050 00:00:00 +0000",  # far future
        "Mon, 05 Oct 2026 09:30:00 -0000",  # naive (no zone information)
    ],
)
def test_implausible_or_naive_date_header_is_rejected(implausible: str) -> None:
    observed, source = _resolve_observed_at(_message(date_header=implausible), NOW)
    assert (observed, source) == (NOW, OBSERVED_AT_RECEIPT_TIME)


def test_date_header_inside_the_skew_allowance_is_accepted_and_beyond_it_is_not() -> None:
    inside = (NOW + timedelta(hours=23)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    beyond = (NOW + timedelta(hours=25)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    assert _resolve_observed_at(_message(date_header=inside), NOW)[1] == OBSERVED_AT_DATE_HEADER
    assert _resolve_observed_at(_message(date_header=beyond), NOW)[1] == OBSERVED_AT_RECEIPT_TIME


@pytest.mark.parametrize("policy", [None, "metadata"])
async def test_envelope_observed_at_follows_the_fallback_and_logs_the_source(
    runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture, policy: str | None
) -> None:
    if policy is None:
        policy_result = None
    else:
        policy_result = MagicMock(
            ingestion_tier="metadata", policy_tier="default", automated_kind=None
        )
    with caplog.at_level(logging.WARNING, logger="butlers.connectors.gmail"):
        envelope = await runtime._build_ingest_envelope(
            _message(date_header=GOOD_DATE), policy_result
        )
    assert envelope["event"]["observed_at"] == GOOD_DATE_UTC.isoformat()
    messages = [r.getMessage() for r in caplog.records]
    assert any(OBSERVED_AT_DATE_HEADER in m and "msg-1" in m for m in messages)
    assert not any(GOOD_DATE in m or "Synthetic" in m for m in messages)


async def test_envelope_without_any_usable_time_is_receipt_time_not_1970(
    runtime: GmailConnectorRuntime,
) -> None:
    before = datetime.now(UTC)
    envelope = await runtime._build_ingest_envelope(_message(), None)
    after = datetime.now(UTC)
    observed = datetime.fromisoformat(envelope["event"]["observed_at"])
    assert before <= observed <= after


async def test_envelope_with_present_internal_date_logs_nothing_and_is_unchanged(
    runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="butlers.connectors.gmail"):
        envelope = await runtime._build_ingest_envelope(_message(str(INTERNAL_MS)), None)
    assert envelope["event"]["observed_at"] == INTERNAL_DT.isoformat()
    assert not [r for r in caplog.records if "internalDate" in r.getMessage()]


OVERFLOW_DATE = "Fri, 31 Dec 9999 23:59:59 -0100"


def test_date_header_that_overflows_on_utc_conversion_falls_back_to_receipt_time() -> None:
    observed, source = _resolve_observed_at(_message(date_header=OVERFLOW_DATE), NOW)
    assert (observed, source) == (NOW, OBSERVED_AT_RECEIPT_TIME)


async def test_envelope_with_an_overflowing_date_header_is_still_built(
    runtime: GmailConnectorRuntime,
) -> None:
    before = datetime.now(UTC)
    envelope = await runtime._build_ingest_envelope(_message(date_header=OVERFLOW_DATE), None)
    observed = datetime.fromisoformat(envelope["event"]["observed_at"])
    assert before <= observed <= datetime.now(UTC)


async def _filtered_row(
    runtime: GmailConnectorRuntime, message: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> tuple:
    from unittest.mock import AsyncMock, patch

    from butlers.ingestion_policy import PolicyDecision

    with (
        caplog.at_level(logging.WARNING, logger="butlers.connectors.gmail"),
        patch.object(runtime, "_fetch_message", new_callable=AsyncMock, return_value=message),
        patch.object(
            runtime._ingestion_policy,
            "evaluate",
            return_value=PolicyDecision(action="block", matched_rule_type="sender_domain"),
        ),
    ):
        await runtime._ingest_single_message("msg-1")
    rows = runtime._filtered_event_buffer._rows
    assert len(rows) == 1
    return rows[0]


async def test_filtered_path_uses_the_date_header_and_logs_the_source(
    runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    row = await _filtered_row(runtime, _message(date_header=GOOD_DATE), caplog)
    assert row[9]["event"]["observed_at"] == GOOD_DATE_UTC.isoformat()
    assert any(OBSERVED_AT_DATE_HEADER in r.getMessage() for r in caplog.records)


async def test_filtered_path_without_any_usable_time_is_receipt_time_not_1970(
    runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    before = datetime.now(UTC)
    row = await _filtered_row(runtime, _message(), caplog)
    observed = datetime.fromisoformat(row[9]["event"]["observed_at"])
    assert before <= observed <= datetime.now(UTC)
    assert any(OBSERVED_AT_RECEIPT_TIME in r.getMessage() for r in caplog.records)


async def test_filtered_path_with_a_present_internal_date_is_unchanged_and_silent(
    runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    row = await _filtered_row(runtime, _message(str(INTERNAL_MS)), caplog)
    assert row[9]["event"]["observed_at"] == INTERNAL_DT.isoformat()
    assert not [r for r in caplog.records if "internalDate" in r.getMessage()]
