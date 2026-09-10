"""Tests for roster/travel/tools/connections.py (bu-2jtfw.8).

Splits into: pure-function unit tests for ``compute_connection_verdict``
(no I/O, no docker needed) and Docker-gated integration tests for
``recompute_trip_connections`` against a real Postgres pool, covering the
acceptance-criteria "Connections" behavior matrix -- holds/tight/broken/
unknown derivation, the approval-spine door raised on a break and withdrawn
on recovery, and the honest ``expected_signals`` row for missing reference
data.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.testing.schema_standins import PENDING_ACTIONS
from butlers.tools.travel.connections import compute_connection_verdict, recompute_trip_connections

_docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# compute_connection_verdict — pure derivation
# ---------------------------------------------------------------------------

_ARRIVAL = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_holds_when_available_comfortably_exceeds_minimum():
    result = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=150),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
    )
    assert result["verdict"] == "holds"
    assert result["available_minutes"] == 150


def test_tight_when_available_is_just_above_minimum():
    result = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=100),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
    )
    assert result["verdict"] == "tight"


def test_broken_when_available_is_below_minimum():
    """The design's canonical example: 65 minutes at PEK, 90-minute minimum."""
    result = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=65),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
    )
    assert result["verdict"] == "broken"
    assert result["available_minutes"] == 65
    assert result["evidence"]["minimum_minutes"] == 90


def test_unknown_when_no_minimum_on_file():
    result = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=150),
        connecting_airport="XYZ",
        minimum_connect_minutes=None,
    )
    assert result["verdict"] == "unknown"
    assert result["evidence"]["reason"] == "no_minimum_on_file"
    # Never renders a false safety claim alongside the honest 'unknown'.
    assert "minimum_minutes" not in result["evidence"]


def test_interline_buffer_can_flip_holds_to_broken():
    same_carrier = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=100),
        connecting_airport="PEK",
        inbound_carrier="Air China",
        outbound_carrier="Air China",
        minimum_connect_minutes=90,
        interline_buffer_minutes=45,
    )
    interline = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=100),
        connecting_airport="PEK",
        inbound_carrier="Air China",
        outbound_carrier="United Airlines",
        minimum_connect_minutes=90,
        interline_buffer_minutes=45,
    )
    assert same_carrier["evidence"]["interline"] is False
    assert interline["evidence"]["interline"] is True
    assert interline["evidence"]["minimum_minutes"] == 90 + 45
    assert same_carrier["verdict"] == "tight"
    assert interline["verdict"] == "broken"


def test_terminal_change_recorded_in_evidence():
    changed = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=150),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
        inbound_arrival_terminal="T2",
        outbound_departure_terminal="T3",
    )
    unchanged = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL + timedelta(minutes=150),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
        inbound_arrival_terminal="T2",
        outbound_departure_terminal="T2",
    )
    assert changed["evidence"]["terminal_change"] is True
    assert unchanged["evidence"]["terminal_change"] is False


def test_negative_or_zero_gap_is_broken_not_a_crash():
    result = compute_connection_verdict(
        inbound_arrival_at=_ARRIVAL,
        outbound_departure_at=_ARRIVAL - timedelta(minutes=5),
        connecting_airport="PEK",
        minimum_connect_minutes=90,
    )
    assert result["verdict"] == "broken"
    assert result["available_minutes"] == -5


# ---------------------------------------------------------------------------
# recompute_trip_connections — real Postgres integration
# ---------------------------------------------------------------------------

CREATE_TRAVEL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS travel"

CREATE_TRIPS_SQL = """
CREATE TABLE IF NOT EXISTS travel.trips (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    destination TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    status      TEXT NOT NULL DEFAULT 'planned',
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_LEGS_SQL = """
CREATE TABLE IF NOT EXISTS travel.legs (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id                   UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type                      TEXT NOT NULL DEFAULT 'flight',
    carrier                   TEXT,
    departure_airport_station TEXT,
    departure_city            TEXT,
    departure_at              TIMESTAMPTZ NOT NULL,
    arrival_airport_station   TEXT,
    arrival_city              TEXT,
    arrival_at                TIMESTAMPTZ NOT NULL,
    confirmation_number       TEXT,
    pnr                       TEXT,
    seat                      TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_AIRPORT_MINIMUM_CONNECT_SQL = """
CREATE TABLE IF NOT EXISTS travel.airport_minimum_connect (
    airport_code               TEXT PRIMARY KEY,
    minimum_connect_minutes    INT NOT NULL,
    interline_buffer_minutes   INT NOT NULL DEFAULT 30,
    source                     TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_CONNECTIONS_SQL = """
CREATE TABLE IF NOT EXISTS travel.connections (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id            UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    inbound_leg_id     UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
    outbound_leg_id    UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
    verdict            TEXT NOT NULL CHECK (verdict IN ('holds', 'tight', 'broken', 'unknown')),
    available_minutes  INT,
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at        TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (inbound_leg_id, outbound_leg_id)
)
"""

# Hand-written (not a registered schema_standins.py stand-in): mirrors
# core_210/core_211's public.expected_signals shape closely enough for the
# 'unknown' verdict test's read/write.
CREATE_EXPECTED_SIGNALS_SQL = """
CREATE TABLE IF NOT EXISTS public.expected_signals (
    signal_key                 TEXT PRIMARY KEY,
    producer                   TEXT NOT NULL,
    producer_endpoint_identity TEXT,
    expected_cadence_seconds   BIGINT NOT NULL,
    last_observed_at           TIMESTAMPTZ,
    measurability               TEXT NOT NULL,
    unmeasurable_reason         TEXT,
    evaluated_at                TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@pytest.fixture
async def pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRAVEL_SCHEMA)
        await p.execute(CREATE_TRIPS_SQL)
        await p.execute(CREATE_LEGS_SQL)
        await p.execute(CREATE_AIRPORT_MINIMUM_CONNECT_SQL)
        await p.execute(CREATE_CONNECTIONS_SQL)
        await p.execute(CREATE_EXPECTED_SIGNALS_SQL)
        await p.execute(PENDING_ACTIONS.ddl())
        yield p


async def _insert_trip(pool, *, start_date: str, end_date: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO travel.trips (name, destination, start_date, end_date, status) "
        "VALUES ('Test Trip', 'Test', $1, $2, 'planned') RETURNING id",
        date.fromisoformat(start_date),
        date.fromisoformat(end_date),
    )
    return str(row["id"])


async def _insert_leg(
    pool, trip_id, *, departure_at, arrival_at, departure_station, arrival_station, carrier="CA"
):
    row = await pool.fetchrow(
        "INSERT INTO travel.legs (trip_id, type, carrier, departure_airport_station, "
        "arrival_airport_station, departure_at, arrival_at) "
        "VALUES ($1::uuid, 'flight', $2, $3, $4, $5, $6) RETURNING id",
        trip_id,
        carrier,
        departure_station,
        arrival_station,
        departure_at,
        arrival_at,
    )
    return str(row["id"])


def _propose_insight_mock():
    return AsyncMock(return_value={"status": "accepted"})


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
class TestRecomputeTripConnectionsAgainstPostgres:
    async def test_broken_connection_raises_one_door_and_one_alert(self, pool):
        trip_id = await _insert_trip(pool, start_date="2026-10-16", end_date="2026-10-16")
        inbound_arrival = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
        inbound_id = await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival - timedelta(hours=3),
            arrival_at=inbound_arrival,
            departure_station="SIN",
            arrival_station="PEK",
        )
        outbound_id = await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival + timedelta(minutes=65),
            arrival_at=inbound_arrival + timedelta(hours=3),
            departure_station="PEK",
            arrival_station="NRT",
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('PEK', 90)"
        )

        propose_mock = _propose_insight_mock()
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate", propose_mock
        ):
            result = await recompute_trip_connections(pool, trip_id)

        assert "error" not in result
        assert len(result["connections"]) == 1
        assert result["connections"][0]["verdict"] == "broken"

        row = await pool.fetchrow(
            "SELECT verdict, available_minutes FROM travel.connections "
            "WHERE inbound_leg_id = $1::uuid AND outbound_leg_id = $2::uuid",
            inbound_id,
            outbound_id,
        )
        assert row["verdict"] == "broken"
        assert row["available_minutes"] == 65

        propose_mock.assert_awaited_once()
        assert propose_mock.call_args.kwargs["category"] == "connection-risk"

        door = await pool.fetchrow(
            "SELECT status, tool_name, deduplication_key FROM pending_actions "
            "WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door is not None
        assert door["status"] == "pending"
        assert door["deduplication_key"] == f"travel:connection-risk:{inbound_id}:{outbound_id}"

        # Recomputing again while still broken must not park a second door.
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate", propose_mock
        ):
            await recompute_trip_connections(pool, trip_id)
        door_count = await pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door_count == 1

    async def test_recovery_withdraws_the_door(self, pool):
        trip_id = await _insert_trip(pool, start_date="2026-10-16", end_date="2026-10-16")
        inbound_arrival = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
        await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival - timedelta(hours=3),
            arrival_at=inbound_arrival,
            departure_station="SIN",
            arrival_station="PEK",
        )
        outbound_id = await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival + timedelta(minutes=65),
            arrival_at=inbound_arrival + timedelta(hours=3),
            departure_station="PEK",
            arrival_station="NRT",
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('PEK', 90)"
        )

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            await recompute_trip_connections(pool, trip_id)

        door_before = await pool.fetchrow(
            "SELECT status FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door_before["status"] == "pending"

        # Recovery: the outbound leg is rebooked/delayed further out, restoring the layover.
        await pool.execute(
            "UPDATE travel.legs SET departure_at = $2, arrival_at = $3 WHERE id = $1::uuid",
            outbound_id,
            inbound_arrival + timedelta(minutes=150),
            inbound_arrival + timedelta(hours=4),
        )

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            result = await recompute_trip_connections(pool, trip_id)

        assert result["connections"][0]["verdict"] == "holds"

        door_after = await pool.fetchrow(
            "SELECT status, decided_by FROM pending_actions "
            "WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door_after["status"] == "rejected"
        assert door_after["decided_by"] == "system:connection-recovery"

    async def test_unknown_minimum_emits_expected_signal_never_an_alert(self, pool):
        trip_id = await _insert_trip(pool, start_date="2026-10-16", end_date="2026-10-16")
        inbound_arrival = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
        await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival - timedelta(hours=3),
            arrival_at=inbound_arrival,
            departure_station="SIN",
            arrival_station="XYZ",
        )
        await _insert_leg(
            pool,
            trip_id,
            departure_at=inbound_arrival + timedelta(minutes=150),
            arrival_at=inbound_arrival + timedelta(hours=3),
            departure_station="XYZ",
            arrival_station="NRT",
        )
        # Deliberately no travel.airport_minimum_connect row for XYZ.

        propose_mock = _propose_insight_mock()
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate", propose_mock
        ):
            result = await recompute_trip_connections(pool, trip_id)

        assert result["connections"][0]["verdict"] == "unknown"
        assert result["connections"][0]["evidence"]["reason"] == "no_minimum_on_file"

        # No connection alert and no door for an 'unknown' verdict -- it must
        # never render as a safety claim in either direction.
        propose_mock.assert_not_awaited()
        door_count = await pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door_count == 0

        signal = await pool.fetchrow(
            "SELECT measurability, producer FROM public.expected_signals "
            "WHERE signal_key = 'travel:airport-minimum-connect:XYZ'"
        )
        assert signal is not None
        assert signal["measurability"] == "absent"
        assert signal["producer"] == "owner"

    async def test_no_connecting_legs_leaves_connections_empty(self, pool):
        """A round trip's two direct legs (different airports, days apart) is not a connection."""
        trip_id = await _insert_trip(pool, start_date="2026-10-16", end_date="2026-10-25")
        await _insert_leg(
            pool,
            trip_id,
            departure_at=datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
            arrival_at=datetime(2026, 10, 16, 14, 0, tzinfo=UTC),
            departure_station="SIN",
            arrival_station="PEK",
        )
        await _insert_leg(
            pool,
            trip_id,
            departure_at=datetime(2026, 10, 25, 20, 0, tzinfo=UTC),
            arrival_at=datetime(2026, 10, 26, 2, 0, tzinfo=UTC),
            departure_station="PEK",
            arrival_station="SIN",
        )

        result = await recompute_trip_connections(pool, trip_id)

        assert result["connections"] == []
        count = await pool.fetchval(
            "SELECT count(*) FROM travel.connections WHERE trip_id = $1::uuid", trip_id
        )
        assert count == 0
