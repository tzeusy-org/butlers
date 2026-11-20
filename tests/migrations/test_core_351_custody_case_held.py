# ruff: noqa: F811
"""core_351: custody cases never lapse while a hold is active (bu-s11n0s.1.1).

REQ-endpoint-custody-holds-010: a lost-device case closes only when every assigned generation is
released or replaced. The fleet-case lapse sweep closes stale silent/routine cases, and a custody
case opens silent, so the sweep asks the content-blind ``public.custody_case_held`` first.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.core.fleet_cases import run_lapse_sweep
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_core_347_custody_command_engine import (  # noqa: F401
    API,
    as_role,
    dashboard,
    dbs,
    fact,
    hold_payload,
    host,
    scalar,
    session,
    unique,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

HELD_SQL = "SELECT public.custody_case_held(:k)"


def _stale(dbs, case_id) -> None:
    """Age a case and its evidence past the seven-day window, as a quiet hold would be."""
    old = datetime.now(UTC) - timedelta(days=30)
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE public.fleet_cases SET updated_at = :t WHERE id = :id"),
            {"t": old, "id": case_id},
        )
        c.execute(
            text("UPDATE public.fleet_case_evidence SET contributed_at = :t WHERE case_id = :id"),
            {"t": old, "id": case_id},
        )


def _stale_case(dbs, key: str):
    old = datetime.now(UTC) - timedelta(days=30)
    with dbs["admin"].begin() as c:
        return c.execute(
            text(
                "INSERT INTO public.fleet_cases (correlation_key, updated_at, opened_at) "
                "VALUES (:k, :t, :t) RETURNING id"
            ),
            {"k": key, "t": old},
        ).scalar_one()


def _case(dbs, case_id) -> tuple[str, str | None]:
    with dbs["admin"].connect() as c:
        row = c.execute(
            text("SELECT state, outcome FROM public.fleet_cases WHERE id = :id"), {"id": case_id}
        ).one()
    return row.state, row.outcome


async def _sweep(dbs) -> dict:
    """Run the real sweep as Switchboard's role, which cannot read ``custody_holds``."""
    url = dbs["admin"].url.set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("SET ROLE butler_switchboard_rw")
        return await run_lapse_sweep(conn)
    finally:
        await conn.close()


async def test_a_quiet_hold_keeps_its_case_open_while_other_stale_cases_lapse(dbs) -> None:
    address = unique("quiet-") + "@example.test"
    held = dashboard(dbs, "hold", hold_payload(fact(dbs, "has-email", address)), session(dbs))
    custody_case = uuid.UUID(held["case_id"])
    _stale(dbs, custody_case)
    other = _stale_case(dbs, unique("test:lapse-other-"))
    orphan = _stale_case(dbs, f"endpoint_custody:{uuid.uuid4()}")  # no unreleased hold

    lapsed = set((await _sweep(dbs))["lapsed_case_ids"])

    assert str(custody_case) not in lapsed
    assert _case(dbs, custody_case) == ("open", None)
    assert {str(other), str(orphan)} <= lapsed
    assert _case(dbs, other) == ("closed", "lapsed")
    assert _case(dbs, orphan) == ("closed", "lapsed")

    # The release that resolves the last generation still closes it as resolved, not lapsed.
    released = host(
        dbs,
        "release",
        {
            "command_id": str(uuid.uuid4()),
            "hold_id": held["holds"][0]["hold_id"],
            "expected_generation": held["holds"][0]["generation"],
            "confirm": True,
        },
    )
    assert released["case_closed"] is True
    assert _case(dbs, custody_case) == ("closed", "custody_resolved")
    assert str(custody_case) not in (await _sweep(dbs))["lapsed_case_ids"]
    assert _case(dbs, custody_case) == ("closed", "custody_resolved")


def test_the_read_answers_only_held_episodes_and_never_raises(dbs) -> None:
    address = unique("read-") + "@example.test"
    held = dashboard(dbs, "hold", hold_payload(fact(dbs, "has-email", address)), session(dbs))
    key = f"endpoint_custody:{held['episode_id']}"
    assert scalar(dbs, HELD_SQL, k=key) is True
    assert as_role(dbs, API, HELD_SQL, k=key) is True  # a runtime role may ask
    assert scalar(dbs, HELD_SQL, k=f"endpoint_custody:{uuid.uuid4()}") is False
    assert scalar(dbs, HELD_SQL, k="endpoint_custody:not-a-uuid") is False
    assert scalar(dbs, HELD_SQL, k=held["episode_id"]) is False  # only custody keys
    assert scalar(dbs, HELD_SQL, k="health:flu") is False


def test_the_read_is_owned_by_the_database_owner_and_executable_by_public(dbs) -> None:
    row = scalar(
        dbs,
        "SELECT pg_get_userbyid(p.proowner) = pg_get_userbyid(d.datdba) "
        "AND p.prosecdef AND has_function_privilege('public', p.oid, 'EXECUTE') "
        "FROM pg_proc p, pg_database d "
        "WHERE p.proname = 'custody_case_held' AND d.datname = current_database()",
    )
    assert row is True


def test_downgrade_drops_the_read_and_reapplies(postgres_container) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
        revisions={"core": "core_351"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    query = text("SELECT to_regprocedure('public.custody_case_held(text)')")
    try:
        with engine.connect() as c:
            assert c.execute(query).scalar() is not None
        command.downgrade(config, "core_350")
        with engine.connect() as c:
            assert c.execute(query).scalar() is None
        command.upgrade(config, "core_351")
        with engine.connect() as c:
            assert c.execute(query).scalar() is not None
    finally:
        engine.dispose()
