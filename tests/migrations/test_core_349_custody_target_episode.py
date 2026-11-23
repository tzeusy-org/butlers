# ruff: noqa: F811
"""core_349 content-blind custody episode read on migrated PostgreSQL (bu-s11n0s.1 S5b).

REQ-endpoint-custody-holds-010: a custody-parked item keeps its hold episode. The read mirrors
``custody_target_held``: any role may call it, it names only the opaque active episode.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_core_347_custody_command_engine import (  # noqa: F401
    API,
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

EPISODE_SQL = "SELECT public.custody_target_episode(:p, :o)"


def test_the_episode_of_a_held_target_is_readable_by_a_runtime_role_and_clears_on_release(
    dbs,
) -> None:
    address = unique("episode-") + "@example.test"
    target = fact(dbs, "has-email", address)
    held = dashboard(dbs, "hold", hold_payload(target), session(dbs))
    assert scalar(dbs, EPISODE_SQL, p="has-email", o=address) == uuid.UUID(held["episode_id"])
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{API}"'))
        as_runtime = c.execute(text(EPISODE_SQL), {"p": "has-email", "o": address.upper()}).scalar()
    assert as_runtime == uuid.UUID(held["episode_id"])  # digest normalizes case
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
    assert released["outcome"] == "released"
    assert scalar(dbs, EPISODE_SQL, p="has-email", o=address) is None
    assert scalar(dbs, EPISODE_SQL, p="has-email", o=unique("never-") + "@example.test") is None


def test_the_read_is_owned_by_the_database_owner_and_executable_by_public(dbs) -> None:
    row = scalar(
        dbs,
        "SELECT pg_get_userbyid(p.proowner) = pg_get_userbyid(d.datdba) "
        "AND p.prosecdef AND has_function_privilege('public', p.oid, 'EXECUTE') "
        "FROM pg_proc p, pg_database d "
        "WHERE p.proname = 'custody_target_episode' AND d.datname = current_database()",
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
        revisions={"core": "core_349"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    query = text("SELECT to_regprocedure('public.custody_target_episode(text, text)')")
    try:
        with engine.connect() as c:
            assert c.execute(query).scalar() is not None
        command.downgrade(config, "core_348")
        with engine.connect() as c:
            assert c.execute(query).scalar() is None
        command.upgrade(config, "core_349")
        with engine.connect() as c:
            assert c.execute(query).scalar() is not None
    finally:
        engine.dispose()
