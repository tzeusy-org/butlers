"""rel_048: ``relationship.encounters`` and the widened debrief state CHECK (bu-5ii4gj.3 E2a)."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import asyncpg
import pytest

from butlers.testing.migration import load_migration

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")
_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "048_encounters.py"


def _migration():
    return load_migration(_MIGRATION_PATH)


@pytest.mark.unit
def test_revision_and_sql_shape() -> None:
    mod = _migration()
    assert mod.revision == "rel_048" and mod.down_revision == "rel_046"
    # A literal % in SQL handed to the driver breaks parameter handling.
    for statement in (*mod.upgrade_statements(), *mod.downgrade_statements()):
        assert "%" not in statement


@pytest.fixture
async def rel(db):
    return await db(
        "core",
        "memory",
        "relationship",
        schemas={"relationship": "relationship"},
        schema="relationship",
    )


async def _run(pool, statements) -> None:
    for statement in statements:
        await pool.execute(statement)


async def _debrief_states(pool) -> set[str]:
    return {r["state"] for r in await pool.fetch("SELECT state FROM relationship.meeting_debriefs")}


async def _insert_debrief(pool, state: str) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.meeting_debriefs
            (event_id, occurrence_start, occurrence_end, event_title, attendees, state)
        VALUES ($1, now(), now(), 'Synthetic', '[]'::jsonb, $2)
        """,
        uuid.uuid4(),
        state,
    )


async def _table_exists(pool) -> bool:
    return await pool.fetchval("SELECT to_regclass('relationship.encounters') IS NOT NULL")


@pytest.mark.integration
@_docker
@_session_loop
async def test_head_has_the_ledger_and_the_wider_check(rel) -> None:
    assert await _table_exists(rel)
    for state in ("did_not_happen", "moved", "pending", "none_agreed", "captured", "expired"):
        await _insert_debrief(rel, state)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_debrief(rel, "invented")


@pytest.mark.integration
@_docker
@_session_loop
async def test_ledger_constraints(rel) -> None:
    entity, event = uuid.uuid4(), uuid.uuid4()
    insert = (
        "INSERT INTO relationship.encounters "
        "(entity_id, event_id, occurrence_start, series_key, state, basis) "
        "VALUES ($1, $2, '2026-01-01T00:00:00Z', 'series', $3, $4)"
    )
    await rel.execute(insert, entity, event, "happened_attested", "owner_debrief")
    with pytest.raises(asyncpg.UniqueViolationError):
        await rel.execute(insert, entity, event, "unknown", "schedule")
    with pytest.raises(asyncpg.CheckViolationError):
        await rel.execute(insert, uuid.uuid4(), event, "met", "schedule")
    with pytest.raises(asyncpg.CheckViolationError):
        await rel.execute(insert, uuid.uuid4(), event, "unknown", "guess")


@pytest.mark.integration
@_docker
@_session_loop
async def test_the_role_can_use_the_ledger(rel) -> None:
    if not await rel.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = $1)", "butler_relationship_rw"
    ):
        pytest.skip("role absent in this database")
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert await rel.fetchval(
            "SELECT has_table_privilege('butler_relationship_rw', 'relationship.encounters', $1)",
            privilege,
        )


@pytest.mark.integration
@_docker
@_session_loop
async def test_upgrade_is_idempotent(rel) -> None:
    mod = _migration()
    await _run(rel, mod.upgrade_statements())
    await _run(rel, mod.upgrade_statements())
    await _insert_debrief(rel, "moved")
    assert await _table_exists(rel)


@pytest.mark.integration
@_docker
@_session_loop
async def test_downgrade_keeps_rows_the_old_check_forbids_as_expired_then_reupgrades(rel) -> None:
    mod = _migration()
    await _insert_debrief(rel, "did_not_happen")
    await _insert_debrief(rel, "moved")
    await _insert_debrief(rel, "captured")

    await _run(rel, mod.downgrade_statements())

    assert not await _table_exists(rel)
    assert await _debrief_states(rel) == {"expired", "captured"}
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_debrief(rel, "moved")
    await _run(rel, mod.downgrade_statements())  # a second downgrade is a no-op

    await _run(rel, mod.upgrade_statements())
    await _insert_debrief(rel, "moved")
    assert await _table_exists(rel)
