"""Unit contract for liveness-qualified expected signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.core.expected_signals import (
    ExpectedSignalState,
    evaluate_expected_signal,
    measurement_producer,
    measurement_producer_identity,
    upsert_expected_signal,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


async def test_live_elapsed_connector_signal_is_absent() -> None:
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"state": "healthy", "last_heartbeat_at": _NOW - timedelta(seconds=30)}
    ]

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="connector:google_health",
        producer_endpoint_identity="google_health:user:owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=15),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.ABSENT
    assert result.unmeasurable_reason is None
    pool.fetch.assert_awaited_once()
    assert "endpoint_identity = $2" in pool.fetch.await_args.args[0]
    assert pool.fetch.await_args.args[1:] == (
        "google_health",
        "google_health:user:owner",
    )


async def test_exact_cadence_boundary_is_absent() -> None:
    pool = AsyncMock()

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=14),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.ABSENT


async def test_stale_connector_makes_elapsed_signal_unmeasurable() -> None:
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"state": "healthy", "last_heartbeat_at": _NOW - timedelta(minutes=6)}
    ]

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="connector:google_health",
        producer_endpoint_identity="google_health:user:owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "producer_stale_or_offline"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("offline", "producer_stale_or_offline"),
        ("error", "producer_not_healthy"),
        ("degraded", "producer_not_healthy"),
        ("paused", "producer_not_healthy"),
    ],
)
async def test_unhealthy_exact_connector_endpoint_is_unmeasurable(state: str, reason: str) -> None:
    pool = AsyncMock()
    pool.fetch.return_value = [{"state": state, "last_heartbeat_at": _NOW}]

    result = await evaluate_expected_signal(
        pool,
        signal_key="finance:recurrence:group-1",
        producer="connector:gmail",
        producer_endpoint_identity="gmail:user:owner@example.invalid",
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == reason


async def test_unavailable_liveness_fails_closed_to_unmeasurable() -> None:
    pool = AsyncMock()
    pool.fetch.side_effect = RuntimeError("view unavailable")

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="connector:google_health",
        producer_endpoint_identity="google_health:user:owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "liveness_unavailable"


async def test_connector_without_endpoint_fails_closed_without_liveness_query() -> None:
    pool = AsyncMock()

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="connector:google_health",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "producer_endpoint_missing"
    pool.fetch.assert_not_awaited()


async def test_unregistered_exact_endpoint_is_unmeasurable() -> None:
    pool = AsyncMock()
    pool.fetch.return_value = []

    result = await evaluate_expected_signal(
        pool,
        signal_key="finance:recurrence:group-1",
        producer="connector:gmail",
        producer_endpoint_identity="gmail:user:missing@example.invalid",
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "producer_unregistered"


@pytest.mark.parametrize("reverse_rows", [False, True])
async def test_healthy_sibling_endpoint_cannot_substitute(reverse_rows: bool) -> None:
    rows = [
        {
            "endpoint_identity": "google_health:user:dead",
            "state": "offline",
            "last_heartbeat_at": _NOW,
        },
        {
            "endpoint_identity": "google_health:user:healthy",
            "state": "healthy",
            "last_heartbeat_at": _NOW,
        },
    ]
    if reverse_rows:
        rows.reverse()
    pool = AsyncMock()
    pool.fetch.return_value = [row for row in rows if row["endpoint_identity"].endswith(":dead")]

    result = await evaluate_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="connector:google_health",
        producer_endpoint_identity="google_health:user:dead",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=60),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "producer_stale_or_offline"
    assert pool.fetch.await_args.args[1:] == (
        "google_health",
        "google_health:user:dead",
    )


async def test_upsert_is_keyed_and_uses_the_evaluated_state() -> None:
    pool = AsyncMock()

    first = await upsert_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW,
        now=_NOW,
    )
    second = await upsert_expected_signal(
        pool,
        signal_key="health:measurement-gap:weight",
        producer="owner",
        expected_cadence=timedelta(days=14),
        last_observed_at=_NOW - timedelta(days=20),
        now=_NOW,
    )

    assert first.state is ExpectedSignalState.PRESENT
    assert second.state is ExpectedSignalState.ABSENT
    assert pool.execute.await_count == 2
    assert "ON CONFLICT (signal_key) DO UPDATE" in pool.execute.await_args.args[0]


def test_measurement_producer_prefers_instrument_provenance() -> None:
    assert measurement_producer(["owner_log", "google_health"]) == "connector:google_health"
    assert measurement_producer(["owner_log"]) == "owner"
    assert measurement_producer(["manual"]) == "owner"
    assert measurement_producer([None]) == "unknown"


def test_mixed_connector_provenance_is_order_independently_unknown() -> None:
    sources = ["google_health", "home_assistant"]

    assert measurement_producer(sources) == "unknown"
    assert measurement_producer(list(reversed(sources))) == "unknown"


@pytest.mark.parametrize("known_source", ["google_health", "home_assistant"])
@pytest.mark.parametrize("unproven_source", ["legacy_import", None], ids=["unknown", "null"])
def test_known_connector_mixed_with_unproven_source_is_order_independently_unknown(
    known_source: str,
    unproven_source: str | None,
) -> None:
    for sources in (
        [known_source, unproven_source],
        [unproven_source, known_source],
    ):
        assert measurement_producer(sources) == "unknown"


def test_measurement_identity_requires_one_exact_corroborated_endpoint() -> None:
    rows = [
        ("google_health", "google_health:user:owner"),
        ("google_health", "google_health:user:owner"),
    ]
    assert measurement_producer_identity(rows) == (
        "connector:google_health",
        "google_health:user:owner",
    )
    assert measurement_producer_identity([rows[0], ("google_health", None)]) == (
        "unknown",
        None,
    )
    assert measurement_producer_identity(
        [rows[0], ("google_health", "google_health:user:sibling")]
    ) == ("unknown", None)
    assert measurement_producer_identity([("owner_log", None)]) == ("owner", None)
