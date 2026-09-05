"""Real-Postgres regression: fleet case contribution (bu-8cdl1.7 Slice 3, RFC 0032).

Exercises ``butlers.core.fleet_cases`` against a fully migrated Postgres
instance (testcontainers) -- not just the mocked-pool unit tests in
``tests/core/test_fleet_cases.py``. Two things a mock cannot verify:

- The RLS policy from core_217 actually blocks a non-Switchboard role's
  ``open_case``/``propose_posture``/``close_case`` at the database, so the
  MCP tool layer's route-forwarding (``core_tools/_fleet_cases.py``) is load-
  bearing, not decorative.
- ``contribute_evidence``'s ``ON CONFLICT DO NOTHING`` + re-SELECT round-trips
  correctly against real Postgres: the epic's acceptance criterion ("two
  butlers contributing the same evidence ref -> one row") means one row *per
  contributor* -- ``UNIQUE(case_id, contributor, kind, ref)`` -- so two
  distinct butlers reporting the same ref get two attributed rows, while the
  same butler repeating it collapses to one.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.core.fleet_cases import (
    FleetCaseError,
    close_case,
    contribute_evidence,
    open_case,
    propose_posture,
    read_case,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def _db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, _db_name: str) -> str:
    return create_migrated_test_db(postgres_container, _db_name, chains=["core"])


@pytest.fixture(scope="module")
def bootstrap_url(postgres_container, _db_name: str, db_url: str) -> str:
    # fleet_cases/fleet_case_links FORCE RLS with no DELETE policy at all --
    # even butler_switchboard_rw cannot delete a row (only close it, see
    # AGENTS.md's RLS note). Cleanup needs the real superuser, which bypasses
    # RLS unconditionally.
    return migration_bootstrap_db_url(postgres_container, _db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


async def _role_conn(db_url: str, role: str | None) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url)
    if role is not None:
        await conn.execute(f"SET ROLE {role}")
    return conn


async def _delete_case(bootstrap_url: str, correlation_key: str) -> None:
    conn = await asyncpg.connect(bootstrap_url)
    try:
        await conn.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = $1", correlation_key
        )
    finally:
        await conn.close()


async def test_lifecycle_via_switchboard_role_with_two_contributing_butlers(
    db_url: str, bootstrap_url: str
) -> None:
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    health_conn = await _role_conn(db_url, "butler_health_rw")
    relationship_conn = await _role_conn(db_url, "butler_relationship_rw")
    try:
        case = await open_case(switchboard_conn, correlation_key="test:roundtrip-key")
        case_id = str(case["id"])
        assert case["state"] == "open"
        assert case["posture"] == "silent"

        # Two distinct butlers reporting the identical (kind, ref): each gets
        # its own attributed row -- UNIQUE is per-contributor, not per-ref.
        health_row, health_new = await contribute_evidence(
            health_conn,
            case_id=case_id,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert health_new is True
        relationship_row, relationship_new = await contribute_evidence(
            relationship_conn,
            case_id=case_id,
            contributor="butler_relationship_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert relationship_new is True
        assert health_row["id"] != relationship_row["id"]

        # The same butler re-reporting the identical (kind, ref) collapses to
        # the existing row -- one row per contributor, not a duplicate.
        repeat_row, repeat_new = await contribute_evidence(
            health_conn,
            case_id=case_id,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert repeat_new is False
        assert repeat_row["id"] == health_row["id"]

        updated = await propose_posture(switchboard_conn, case_id=case_id, posture="active")
        assert updated["posture"] == "active"

        closed = await close_case(switchboard_conn, case_id=case_id, outcome="resolved")
        assert closed["state"] == "closed"
        assert closed["outcome"] == "resolved"

        full = await read_case(switchboard_conn, case_id)
        assert full is not None
        assert len(full["evidence"]) == 2
        assert {row["contributor"] for row in full["evidence"]} == {
            "butler_health_rw",
            "butler_relationship_rw",
        }

        with pytest.raises(FleetCaseError, match="already closed"):
            await close_case(switchboard_conn, case_id=case_id, outcome="resolved-again")
    finally:
        await switchboard_conn.close()
        await health_conn.close()
        await relationship_conn.close()
        await _delete_case(bootstrap_url, "test:roundtrip-key")


async def test_non_switchboard_role_cannot_write_fleet_cases_at_the_data_layer(
    db_url: str, bootstrap_url: str
) -> None:
    """Pins why the MCP tool layer must forward these calls through
    Switchboard's route(): the data layer itself refuses at the database,
    it does not silently succeed for the wrong role. INSERT (open_case) and
    UPDATE (propose_posture/close_case) fail differently under RLS -- a
    WITH CHECK violation on INSERT raises, but an UPDATE's USING clause
    just filters the row out, so those two instead surface as a
    FleetCaseError naming the mismatch rather than a raw asyncpg error."""
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    health_conn = await _role_conn(db_url, "butler_health_rw")
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await open_case(health_conn, correlation_key="test:rls-refusal-key")

        case = await open_case(switchboard_conn, correlation_key="test:rls-refusal-key-2")
        with pytest.raises(FleetCaseError, match="did not apply"):
            await propose_posture(health_conn, case_id=str(case["id"]), posture="urgent")
        with pytest.raises(FleetCaseError, match="did not apply"):
            await close_case(health_conn, case_id=str(case["id"]), outcome="resolved")
    finally:
        await switchboard_conn.close()
        await health_conn.close()
        await _delete_case(bootstrap_url, "test:rls-refusal-key-2")
