"""The calendar premise probe and its definer on real Postgres (bu-5ii4gj.10 slice A-S3)."""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from butlers.modules.calendar.models import AttendeeInfo, AttendeeResponseStatus
from tests.modules.test_calendar_owner_agreements import _attendees, _world
from tests.modules.test_calendar_participants import _event, _module, _project

_ROOT = Path(__file__).resolve().parents[2]
_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")

_PROBE = ("calendar", "occurrence_still_changed")


def _premises():
    from butlers.roster_imports import roster_module

    return roster_module("switchboard", "tools/insight/premises")


# The shared test database keeps the calendar tables in ``public``; a butler keeps them in its own
# schema, and the definer takes whichever schema name it is given.
_SCHEMA = "public"


async def _check(pool, event_id, start, butler=_SCHEMA):
    probe = _premises().PREMISE_PROBES[_PROBE]
    return await probe(
        pool,
        {"butler": butler, "event_id": str(event_id), "original_start": start.isoformat()},
    )


async def _accepted(db):
    pool, source = await _world(db)
    event = _event(attendees=_attendees())
    await _project(_module(pool), source, event)
    row = await pool.fetchrow("SELECT event_id, occurrence_start FROM calendar_owner_agreements")
    return pool, source, event, row["event_id"], row["occurrence_start"]


@pytest.mark.unit
def test_the_probe_is_registered_and_the_migration_extends_the_chain():
    assert _PROBE in _premises().PREMISE_PROBES
    path = _ROOT / "alembic/versions/core/core_379_calendar_occurrence_definer.py"
    spec = importlib.util.spec_from_file_location("core_379", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("core_379", "core_378")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_an_unchanged_event_is_false_a_move_is_true_and_moving_back_is_false(db):
    pool, source, event, event_id, start = await _accepted(db)
    assert await _check(pool, event_id, start) is False

    moved = event.model_copy(
        update={
            "start_at": event.start_at + timedelta(hours=1),
            "end_at": event.end_at + timedelta(hours=1),
        }
    )
    await _project(_module(pool), source, moved)
    assert await _check(pool, event_id, start) is True

    await _project(_module(pool), source, event)
    assert await _check(pool, event_id, start) is False


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_a_changed_location_or_guest_list_counts_as_changed(db):
    pool, source, event, event_id, start = await _accepted(db)
    await _project(_module(pool), source, event.model_copy(update={"location": "Room 9"}))
    assert await _check(pool, event_id, start) is True

    await _project(_module(pool), source, event)
    assert await _check(pool, event_id, start) is False
    extra = [
        *_attendees(),
        AttendeeInfo(email="bob@known.test", response_status=AttendeeResponseStatus.accepted),
    ]
    await _project(_module(pool), source, event.model_copy(update={"attendees": extra}))
    assert await _check(pool, event_id, start) is True


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_a_gone_agreement_is_false_and_a_bad_butler_or_argument_is_unknown(db):
    pool, source, event, event_id, start = await _accepted(db)
    declined = event.model_copy(update={"attendees": _attendees(AttendeeResponseStatus.declined)})
    await _project(_module(pool), source, declined)
    assert await _check(pool, event_id, start) is False

    assert await _check(pool, event_id, start, butler="no_such_schema") is None
    assert await _check(pool, event_id, start, butler="x; DROP TABLE x") is None
    assert await _check(pool, uuid.uuid4(), start) is False
    probe = _premises().PREMISE_PROBES[_PROBE]
    assert await probe(pool, {"butler": "any", "event_id": "nope", "original_start": "x"}) is None
    assert (
        await probe(
            pool,
            {
                "butler": _SCHEMA,
                "event_id": str(event_id),
                "original_start": "2026-01-01T00:00:00",
            },
        )
        is None
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@_docker
async def test_only_the_switchboard_may_execute_and_it_reads_one_boolean(db):
    pool, _, _, event_id, start = await _accepted(db)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        answer = await conn.fetchval(
            "SELECT public.resolve_calendar_occurrence($1, $2, $3)", _SCHEMA, event_id, start
        )
    assert answer is False
    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', "
        "'public.resolve_calendar_occurrence(text, uuid, timestamptz)', 'EXECUTE')"
    )


@pytest.mark.unit
def test_public_execute_is_revoked_on_its_own_and_the_definer_is_pinned():
    from unittest.mock import MagicMock, patch

    path = _ROOT / "alembic/versions/core/core_379_calendar_occurrence_definer.py"
    spec = importlib.util.spec_from_file_location("core_379_shape", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        module.upgrade()
    flat = [" ".join(s.split()) for s in statements]
    function = next(s for s in flat if "CREATE OR REPLACE FUNCTION" in s)
    assert "SECURITY DEFINER" in function and "SET search_path = pg_catalog, pg_temp" in function
    assert "RETURNS boolean" in function
    assert (
        "REVOKE ALL ON FUNCTION public.resolve_calendar_occurrence(text, uuid, timestamptz) "
        "FROM PUBLIC" in flat
    )
