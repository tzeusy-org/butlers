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

import asyncio
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.testing.schema_standins import PENDING_ACTIONS
from butlers.tools.travel.connections import (
    acknowledge_connection_risk,
    compute_connection_verdict,
    recompute_trip_connections,
)

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
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    booking_record_id         UUID,
    segment_index             INT
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
    verdict_changed_at TIMESTAMPTZ NOT NULL,
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

CREATE_INSIGHT_CANDIDATES_SQL = """
CREATE TABLE IF NOT EXISTS insight_candidates (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category           TEXT NOT NULL,
    dedup_key          TEXT NOT NULL UNIQUE,
    prepared_action_id UUID
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
        await p.execute(CREATE_INSIGHT_CANDIDATES_SQL)
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
    pool,
    trip_id,
    *,
    departure_at,
    arrival_at,
    departure_station,
    arrival_station,
    carrier="CA",
    booking_record_id=None,
    segment_index=None,
):
    row = await pool.fetchrow(
        "INSERT INTO travel.legs (trip_id, type, carrier, departure_airport_station, "
        "arrival_airport_station, departure_at, arrival_at, booking_record_id, segment_index) "
        "VALUES ($1::uuid, 'flight', $2, $3, $4, $5, $6, $7::uuid, $8) RETURNING id",
        trip_id,
        carrier,
        departure_station,
        arrival_station,
        departure_at,
        arrival_at,
        booking_record_id,
        segment_index,
    )
    return str(row["id"])


def _propose_insight_mock():
    return AsyncMock(return_value={"status": "accepted"})


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
class TestRecomputeTripConnectionsAgainstPostgres:
    async def test_negative_gap_remains_a_broken_connection(self, pool):
        """A missed departure remains represented instead of disappearing."""
        trip_id = await _insert_trip(pool, start_date="2026-10-16", end_date="2026-10-16")
        outbound_departure = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
        booking_record_id = str(uuid.uuid4())
        inbound_id = await _insert_leg(
            pool,
            trip_id,
            departure_at=outbound_departure - timedelta(hours=3),
            arrival_at=outbound_departure + timedelta(minutes=5),
            departure_station="SIN",
            arrival_station="PEK",
            booking_record_id=booking_record_id,
            segment_index=0,
        )
        outbound_id = await _insert_leg(
            pool,
            trip_id,
            departure_at=outbound_departure,
            arrival_at=outbound_departure + timedelta(hours=3),
            departure_station="PEK",
            arrival_station="NRT",
            booking_record_id=booking_record_id,
            segment_index=1,
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('PEK', 90)"
        )

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            result = await recompute_trip_connections(pool, trip_id)

        assert result["connections"] == [
            {
                "inbound_leg_id": inbound_id,
                "outbound_leg_id": outbound_id,
                "verdict": "broken",
                "available_minutes": -5,
                "evidence": {
                    "connecting_airport": "PEK",
                    "interline": False,
                    "inbound_carrier": "CA",
                    "outbound_carrier": "CA",
                    "minimum_minutes": 90,
                },
            }
        ]

        # A severe operational delay moves the inbound departure beyond the
        # outbound departure. Stable segment identity must still keep the
        # original pair and its broken verdict.
        await pool.execute(
            "UPDATE travel.legs SET departure_at = $2, arrival_at = $3 WHERE id = $1::uuid",
            inbound_id,
            outbound_departure + timedelta(hours=1),
            outbound_departure + timedelta(hours=4),
        )
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            severely_missed = await recompute_trip_connections(pool, trip_id)
        assert severely_missed["connections"][0]["inbound_leg_id"] == inbound_id
        assert severely_missed["connections"][0]["outbound_leg_id"] == outbound_id
        assert severely_missed["connections"][0]["verdict"] == "broken"

        # A leg from another booking record has a persisted departure between
        # the reversed operational timestamps of a structurally ordered pair.
        # Both connections prove that all three legs retain deterministic
        # itinerary order without letting mutable times reverse segment 0/1.
        cross_record_trip = await _insert_trip(pool, start_date="2026-11-01", end_date="2026-11-01")
        structural_record_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        structural_inbound = await _insert_leg(
            pool,
            cross_record_trip,
            departure_at=datetime(2026, 11, 1, 14, tzinfo=UTC),
            arrival_at=datetime(2026, 11, 1, 16, tzinfo=UTC),
            departure_station="HKG",
            arrival_station="ICN",
            booking_record_id=structural_record_id,
            segment_index=0,
        )
        interleaved_cross_record = await _insert_leg(
            pool,
            cross_record_trip,
            departure_at=datetime(2026, 11, 1, 11, tzinfo=UTC),
            arrival_at=datetime(2026, 11, 1, 13, tzinfo=UTC),
            departure_station="SIN",
            arrival_station="HKG",
            booking_record_id="00000000-0000-0000-0000-000000000001",
            segment_index=0,
        )
        structural_outbound = await _insert_leg(
            pool,
            cross_record_trip,
            departure_at=datetime(2026, 11, 1, 8, tzinfo=UTC),
            arrival_at=datetime(2026, 11, 1, 10, tzinfo=UTC),
            departure_station="ICN",
            arrival_station="NRT",
            booking_record_id=structural_record_id,
            segment_index=1,
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('HKG', 60), ('ICN', 60)"
        )
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            cross_result = await recompute_trip_connections(pool, cross_record_trip)
        expected_pairs = [
            (interleaved_cross_record, structural_inbound),
            (structural_inbound, structural_outbound),
        ]
        assert [
            (row["inbound_leg_id"], row["outbound_leg_id"]) for row in cross_result["connections"]
        ] == expected_pairs

        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            repeated_cross_result = await recompute_trip_connections(pool, cross_record_trip)
        assert [
            (row["inbound_leg_id"], row["outbound_leg_id"])
            for row in repeated_cross_result["connections"]
        ] == expected_pairs

        mixed_trip = await _insert_trip(pool, start_date="2026-12-01", end_date="2026-12-01")
        legacy_inbound = await _insert_leg(
            pool,
            mixed_trip,
            departure_at=datetime(2026, 12, 1, 8, tzinfo=UTC),
            arrival_at=datetime(2026, 12, 1, 10, tzinfo=UTC),
            departure_station="SIN",
            arrival_station="BKK",
        )
        keyed_outbound = await _insert_leg(
            pool,
            mixed_trip,
            departure_at=datetime(2026, 12, 1, 11, tzinfo=UTC),
            arrival_at=datetime(2026, 12, 1, 14, tzinfo=UTC),
            departure_station="BKK",
            arrival_station="NRT",
            booking_record_id=str(uuid.uuid4()),
            segment_index=0,
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('BKK', 60)"
        )
        mixed_result = await recompute_trip_connections(pool, mixed_trip)
        assert [
            (row["inbound_leg_id"], row["outbound_leg_id"]) for row in mixed_result["connections"]
        ] == [(legacy_inbound, keyed_outbound)]

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

        rejected_candidate = AsyncMock(
            return_value={"status": "error", "reason": "injected candidate persistence failure"}
        )
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            rejected_candidate,
        ):
            failed = await recompute_trip_connections(pool, trip_id)

        assert failed["error"] == "recompute_failed"
        assert (
            await pool.fetchval(
                "SELECT metadata ? 'connection_derivation_completed_at' "
                "FROM travel.trips WHERE id = $1::uuid",
                trip_id,
            )
            is False
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.connections WHERE trip_id = $1::uuid", trip_id
            )
            == 0
        )
        assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 0

        filtered_candidate = AsyncMock(
            return_value={"status": "filtered", "reason": "verbosity is off"}
        )
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            filtered_candidate,
        ):
            failed = await recompute_trip_connections(pool, trip_id)

        assert failed["error"] == "recompute_failed"
        filtered_candidate.assert_awaited_once()
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.connections WHERE trip_id = $1::uuid", trip_id
            )
            == 0
        )
        assert await pool.fetchval("SELECT count(*) FROM insight_candidates") == 0
        assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 0

        propose_mock = _propose_insight_mock()
        with (
            patch(
                "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
                propose_mock,
            ),
            patch(
                "butlers.modules.approvals.park.park_prepared_action",
                AsyncMock(side_effect=RuntimeError("injected door persistence failure")),
            ),
        ):
            failed = await recompute_trip_connections(pool, trip_id)

        assert failed["error"] == "recompute_failed"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.connections WHERE trip_id = $1::uuid", trip_id
            )
            == 0
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
            )
            == 0
        )
        propose_mock.reset_mock()

        async def persist_candidate(db, **kwargs):
            await db.execute(
                "INSERT INTO insight_candidates (category, dedup_key, prepared_action_id) "
                "VALUES ($1, $2, $3)",
                kwargs["category"],
                kwargs["dedup_key"],
                kwargs["prepared_action_id"],
            )
            return {"status": "accepted"}

        propose_mock.side_effect = persist_candidate
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
        assert propose_mock.call_args.kwargs["dedup_key"].count(":") == 3

        door = await pool.fetchrow(
            "SELECT id, status, tool_name, deduplication_key FROM pending_actions "
            "WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert door is not None
        assert door["status"] == "pending"
        assert door["deduplication_key"].startswith(
            f"travel:connection-risk:{inbound_id}:{outbound_id}:broken:65:"
        )
        candidate_action_id = await pool.fetchval(
            "SELECT prepared_action_id FROM insight_candidates WHERE category = 'connection-risk'"
        )
        assert candidate_action_id == door["id"]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM insight_candidates WHERE category = 'connection-risk'"
            )
            == 1
        )

        acknowledged = await acknowledge_connection_risk(
            pool,
            trip_id=trip_id,
            inbound_leg_id=inbound_id,
            outbound_leg_id=outbound_id,
            verdict="broken",
            available_minutes=65,
        )
        assert acknowledged["status"] == "acknowledged"
        assert acknowledged["current_connection"]["verdict"] == "broken"

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
        import butlers.tools.travel.connections as connection_module

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

        class WithdrawFailureConnection:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            async def execute(self, query, *args):
                if "UPDATE pending_actions SET status = 'rejected'" in query:
                    raise RuntimeError("injected withdrawal persistence failure")
                return await self._conn.execute(query, *args)

        async with pool.acquire() as conn:
            with pytest.raises(RuntimeError, match="injected withdrawal persistence failure"):
                async with conn.transaction():
                    await connection_module._recompute_trip_connections_locked(
                        WithdrawFailureConnection(conn), trip_id
                    )

        still_broken = await pool.fetchval(
            "SELECT verdict FROM travel.connections WHERE trip_id = $1::uuid", trip_id
        )
        assert still_broken == "broken"
        assert (
            await pool.fetchval(
                "SELECT status FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
            )
            == "pending"
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

        # A later regression is a new verdict transition and therefore gets
        # a new active door; the rejected history row must not suppress it.
        await pool.execute(
            "UPDATE travel.legs SET departure_at = $2, arrival_at = $3 WHERE id = $1::uuid",
            outbound_id,
            inbound_arrival + timedelta(minutes=65),
            inbound_arrival + timedelta(hours=3),
        )
        with patch(
            "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
            _propose_insight_mock(),
        ):
            await recompute_trip_connections(pool, trip_id)

        door_statuses = await pool.fetch(
            "SELECT status FROM pending_actions "
            "WHERE tool_name = 'acknowledge_connection_risk' ORDER BY requested_at"
        )
        assert [row["status"] for row in door_statuses] == ["rejected", "pending"]

    async def test_concurrent_recovery_cannot_overtake_broken_door_creation(self, pool):
        """The connection row and approval-door transition share one serialized transaction."""
        import butlers.tools.travel.connections as connection_module

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
            departure_at=inbound_arrival + timedelta(minutes=150),
            arrival_at=inbound_arrival + timedelta(hours=4),
            departure_station="PEK",
            arrival_station="NRT",
        )
        await pool.execute(
            "INSERT INTO travel.airport_minimum_connect (airport_code, minimum_connect_minutes) "
            "VALUES ('PEK', 90)"
        )
        await recompute_trip_connections(pool, trip_id)

        # Make the connection broken, then pause that transition immediately
        # before its door insert. A recovery recompute started at this point
        # must wait rather than withdraw a door that does not exist yet.
        await pool.execute(
            "UPDATE travel.legs SET arrival_at = $2 WHERE id = $1::uuid",
            inbound_id,
            inbound_arrival + timedelta(minutes=100),
        )
        raising = asyncio.Event()
        release = asyncio.Event()
        original_raise = connection_module._raise_connection_door

        async def paused_raise(*args, **kwargs):
            raising.set()
            await release.wait()
            await original_raise(*args, **kwargs)

        with (
            patch.object(connection_module, "_raise_connection_door", paused_raise),
            patch(
                "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
                _propose_insight_mock(),
            ),
        ):
            broken_task = asyncio.create_task(recompute_trip_connections(pool, trip_id))
            await raising.wait()
            await pool.execute(
                "UPDATE travel.legs SET arrival_at = $2 WHERE id = $1::uuid",
                inbound_id,
                inbound_arrival,
            )
            recovery_task = asyncio.create_task(recompute_trip_connections(pool, trip_id))
            await asyncio.sleep(0)
            assert not recovery_task.done()
            release.set()
            await asyncio.gather(broken_task, recovery_task)

        connection = await pool.fetchrow(
            "SELECT verdict FROM travel.connections WHERE inbound_leg_id = $1::uuid "
            "AND outbound_leg_id = $2::uuid",
            inbound_id,
            outbound_id,
        )
        assert connection["verdict"] == "holds"
        statuses = await pool.fetch(
            "SELECT status FROM pending_actions WHERE tool_name = 'acknowledge_connection_risk'"
        )
        assert [row["status"] for row in statuses] == ["rejected"]

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
