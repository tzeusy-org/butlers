"""Real-PostgreSQL person posture (bu-q7vx1q.8).

Posture is owner-asserted (``entity_set_posture``) and read by every producer that
nudges about a person. Fixtures are synthetic people; nothing here asserts on or
logs real names.

Pinned at the real seam (people are anchored through ``contact_entity_map``): the
briefing birthday and gift-ask queries, the calendar overlay, the idempotent write
tool, and the ``public.entities`` trigger that keeps a non-Relationship butler role
from changing posture.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.briefing import _count_birthdays_on, run_relationship_briefing_contribution
from butlers.jobs.calendar_overlay import run_relationship_calendar_overlay_contribution
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.posture import entity_set_posture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_TODAY = date(2031, 3, 6)
_TOMORROW = date(2031, 3, 7)
_PROBE_ROLE = "butler_posture_probe_rw"
_RELATIONSHIP_ROLE = "butler_relationship_rw"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship", "contacts"],
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    connection_pool = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
    )
    # contacts_004 supplies the entity anchor AND nullable contact_id required by
    # the contactless arm. Retain this original idempotent fixture guard as a no-op
    # after the canonical contacts chain; do not hand-copy its schema constraints.
    await connection_pool.execute(
        "ALTER TABLE important_dates ADD COLUMN IF NOT EXISTS local_entity_id UUID"
    )
    yield connection_pool
    await connection_pool.close()


async def _person_with_birthday(pool: asyncpg.Pool, name: str, on: date) -> Any:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        name,
    )
    contact_id = await pool.fetchval("SELECT gen_random_uuid()")
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        contact_id,
        entity_id,
    )
    await pool.execute(
        "INSERT INTO important_dates (contact_id, label, month, day) "
        "VALUES ($1, 'Birthday', $2, $3)",
        contact_id,
        on.month,
        on.day,
    )
    return entity_id


async def test_posture_gates_birthday_count_and_is_reversible(pool: asyncpg.Pool) -> None:
    entity_id = await _person_with_birthday(pool, "Person Count", _TOMORROW)
    assert await _count_birthdays_on(pool, _TOMORROW) == 1

    for posture in ("memorial", "quiet", "no_contact"):
        await entity_set_posture(pool, entity_id, posture)
        assert await _count_birthdays_on(pool, _TOMORROW) == 0

    await entity_set_posture(pool, entity_id, "active")
    assert await _count_birthdays_on(pool, _TOMORROW) == 1

    from fastmcp import FastMCP

    from butlers.modules._roster_relationship import RelationshipModule, RelationshipModuleConfig
    from butlers.tools.relationship import dates
    from tests.three_seams_helpers import baseline_function

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 3, 6, tzinfo=UTC)

    # Position literal empty before other planted dates. Keep all original count
    # and reversible writer assertions above, with a three-days-ahead birthday.
    await pool.execute(
        "UPDATE important_dates SET month=3,day=9 WHERE contact_id IN (SELECT contact_id FROM contact_entity_map WHERE entity_id=$1)",
        entity_id,
    )
    await entity_set_posture(pool, entity_id, "memorial")
    old = baseline_function("upcoming_dates", {**vars(dates), "datetime": FrozenDateTime})
    assert len(await old(pool, 7)) == 1  # actual old SQL leaks the sole memorial
    with patch.object(dates, "datetime", FrozenDateTime):
        assert await dates.upcoming_dates(pool, 7) == []
        await entity_set_posture(pool, entity_id, "active")
        assert [r["contact_name"] for r in await dates.upcoming_dates(pool, 7)] == ["Person Count"]
        # Both UNION arms must exclude each posture while finding planted active
        # positives; SQL mutations independently neutralize each predicate.
        for anchored in (False, True):
            for posture in ("active", "memorial", "quiet", "no_contact"):
                name = f"Arm {anchored} {posture}"
                if anchored:
                    target = await pool.fetchval(
                        "INSERT INTO public.entities(canonical_name,entity_type) VALUES($1,'person') RETURNING id",
                        name,
                    )
                    await pool.execute(
                        "INSERT INTO important_dates(local_entity_id,label,month,day) VALUES($1,'Anniversary',3,9)",
                        target,
                    )
                else:
                    target = await _person_with_birthday(pool, name, date(2031, 3, 9))
                await entity_set_posture(pool, target, posture)
        current = await dates.upcoming_dates(pool, 7)
        assert {r["contact_name"] for r in current} == {
            "Person Count",
            "Arm False active",
            "Arm True active",
        }
        import inspect

        for occurrence in (0, 1):
            src = inspect.getsource(dates.upcoming_dates)
            needle = "          AND e.posture = 'active'\n"
            at = [i for i in range(len(src)) if src.startswith(needle, i)][occurrence]
            changed = src[:at] + src[at + len(needle) :]
            ns = {**vars(dates), "datetime": FrozenDateTime}
            exec(compile(changed, "posture-arm-control", "exec"), ns)
            wrong = await ns["upcoming_dates"](pool, 7)
            assert any(r["contact_name"] == f"Arm {bool(occurrence)} memorial" for r in wrong)
            assert any(r["contact_name"] == "Arm True active" for r in wrong)
        module = RelationshipModule()
        mcp = FastMCP("posture-control")
        await module.register_tools(
            mcp,
            RelationshipModuleConfig(
                groups=[
                    "contacts",
                    "contacts_extended",
                    "interactions",
                    "relationships",
                    "social",
                    "notes",
                    "tracking",
                    "management",
                ]
            ),
            SimpleNamespace(pool=pool),
            "relationship",
        )
        tool = await mcp.get_tool("upcoming_dates")
        assert await tool.fn(days_ahead=7) == current
        # Failed real SQL is not an empty result; restore via rollback and prove
        # the active data is still visible from a fresh acquisition.
        async with pool.acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            try:
                await connection.execute(
                    "ALTER TABLE public.entities RENAME COLUMN posture TO posture_hidden"
                )
                with pytest.raises(asyncpg.UndefinedColumnError):
                    await dates.upcoming_dates(connection, 7)
            finally:
                await transaction.rollback()
        assert await dates.upcoming_dates(pool, 7) == current


async def test_briefing_has_no_birthday_highlight_for_a_memorial_person(
    pool: asyncpg.Pool,
) -> None:
    await _person_with_birthday(pool, "Person Active", date(2031, 3, 8))
    memorial_id = await _person_with_birthday(pool, "Person Memorial", date(2031, 3, 9))
    await entity_set_posture(pool, memorial_id, "memorial")

    write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.briefing._write_contribution", new=write),
        patch(
            "butlers.jobs.briefing._relationship_finance_birthday_gift_ask",
            new=AsyncMock(return_value={"status": "skipped"}),
        ),
    ):
        await run_relationship_briefing_contribution(pool, None)

    texts = [h["text"] for h in write.await_args.args[1]["highlights"]]
    assert any("Person Active" in text for text in texts)
    assert not any("Person Memorial" in text for text in texts)


async def test_overlay_turns_a_memorial_birthday_into_a_remembrance(pool: asyncpg.Pool) -> None:
    quiet_id = await _person_with_birthday(pool, "Person Quiet", date(2031, 3, 10))
    memorial_id = await _person_with_birthday(pool, "Person Remembered", date(2031, 3, 11))
    await _person_with_birthday(pool, "Person Overlay Active", date(2031, 3, 12))
    await entity_set_posture(pool, quiet_id, "quiet")
    await entity_set_posture(pool, memorial_id, "memorial")

    store: dict[str, Any] = {}

    async def _state_set(_pool: Any, key: str, value: Any) -> int:
        store[key] = value
        return 1

    with (
        patch("butlers.jobs.calendar_overlay.today_sgt", return_value=_TODAY),
        patch("butlers.jobs.calendar_overlay.state_set", new=_state_set),
        patch("butlers.jobs.calendar_overlay.state_list", new=AsyncMock(return_value=[])),
        patch("butlers.jobs.calendar_overlay.state_delete", new=AsyncMock()),
    ):
        result = await run_relationship_calendar_overlay_contribution(pool, None)

    # The module DB is shared with the other tests, so assert per person, never on the
    # whole overlay: other active people legitimately yield birthdays.
    entries = [e for env in store.values() for e in env["entries"]]

    def _entries_for(person: str) -> list[dict[str, Any]]:
        return [e for e in entries if person in e["label"]]

    assert [e["kind"] for e in _entries_for("Person Remembered")] == ["remembrance"]
    assert _entries_for("Person Quiet") == []
    assert [e["kind"] for e in _entries_for("Person Overlay Active")] == ["birthday"]
    assert result["remembrance_entries"] >= 1


async def test_set_posture_is_idempotent_and_audits_without_the_value(
    pool: asyncpg.Pool,
) -> None:
    entity_id = await _person_with_birthday(pool, "Person Idempotent", date(2031, 4, 1))

    first = await entity_set_posture(pool, str(entity_id), "no_contact")
    again = await entity_set_posture(pool, str(entity_id), "no_contact")
    assert (first["changed"], again["changed"]) == (True, False)

    row = await pool.fetchrow(
        "SELECT posture, posture_since, posture_set_by FROM public.entities WHERE id = $1",
        entity_id,
    )
    assert row["posture"] == "no_contact"
    assert row["posture_since"] is not None
    assert row["posture_set_by"] == "relationship"

    audit = await pool.fetch(
        "SELECT note, metadata::text AS metadata FROM public.audit_log "
        "WHERE action = 'entity_set_posture' AND target = $1",
        str(entity_id),
    )
    assert len(audit) == 1
    assert "no_contact" not in (audit[0]["metadata"] or "")
    assert "no_contact" not in (audit[0]["note"] or "")


async def test_set_posture_rejects_unknown_values_entities_and_the_owner(
    pool: asyncpg.Pool,
) -> None:
    entity_id = await _person_with_birthday(pool, "Person Rejected", date(2031, 4, 2))
    with pytest.raises(ValueError, match="posture must be one of"):
        await entity_set_posture(pool, entity_id, "deceased")
    with pytest.raises(ValueError, match="not found"):
        await entity_set_posture(pool, "00000000-0000-0000-0000-00000000dead", "memorial")

    owner_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner Synthetic', 'person', ARRAY['owner']) RETURNING id"
    )
    with pytest.raises(ValueError, match="owner"):
        await entity_set_posture(pool, owner_id, "quiet")


@pytest.fixture
async def posture_roles(pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Create the two NOLOGIN roles the trigger distinguishes; drop only what we made."""
    created: list[str] = []
    try:
        for role in (_PROBE_ROLE, _RELATIONSHIP_ROLE):
            if not await pool.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
                await pool.execute(f'CREATE ROLE "{role}" NOLOGIN')
                created.append(role)
            await pool.execute(f'GRANT SELECT, UPDATE ON public.entities TO "{role}"')
    except asyncpg.InsufficientPrivilegeError:
        pytest.skip("test DB user cannot create roles; role decision is covered separately")
    yield
    for role in created:
        await pool.execute(f'DROP OWNED BY "{role}"')
        await pool.execute(f'DROP ROLE "{role}"')


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("butler_relationship_rw", True),
        ("butler_finance_rw", False),
        ("butler_switchboard_rw", False),
        ("connector_writer", False),
        ("postgres", True),
        ("migration_owner", True),
    ],
)
async def test_posture_writer_role_decision(pool: asyncpg.Pool, role: str, allowed: bool) -> None:
    """The decision the trigger applies to current_user, exercised without creating roles."""
    assert await pool.fetchval("SELECT public.posture_writer_allowed($1)", role) is allowed


async def test_trigger_admits_the_session_user_and_still_writes_posture(
    pool: asyncpg.Pool,
) -> None:
    """The migration/test login is not a butler role, so the guard lets it through."""
    entity_id = await _person_with_birthday(pool, "Person Session", date(2031, 4, 4))
    await pool.execute("UPDATE public.entities SET posture = 'quiet' WHERE id = $1", entity_id)
    assert await pool.fetchval("SELECT posture FROM public.entities WHERE id = $1", entity_id) == (
        "quiet"
    )


async def test_only_the_relationship_role_can_change_posture(
    pool: asyncpg.Pool, posture_roles: None
) -> None:
    entity_id = await _person_with_birthday(pool, "Person Guarded", date(2031, 4, 3))

    async with pool.acquire() as conn:
        await conn.execute(f'SET ROLE "{_PROBE_ROLE}"')
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE public.entities SET posture = 'memorial' WHERE id = $1", entity_id
                )
            # Other columns stay writable for the same role.
            await conn.execute(
                "UPDATE public.entities SET canonical_name = 'Person Guarded 2' WHERE id = $1",
                entity_id,
            )
        finally:
            await conn.execute("RESET ROLE")
        assert await conn.fetchval(
            "SELECT posture FROM public.entities WHERE id = $1", entity_id
        ) == ("active")

        await conn.execute(f'SET ROLE "{_RELATIONSHIP_ROLE}"')
        try:
            await conn.execute(
                "UPDATE public.entities SET posture = 'memorial' WHERE id = $1", entity_id
            )
        finally:
            await conn.execute("RESET ROLE")
        assert await conn.fetchval(
            "SELECT posture FROM public.entities WHERE id = $1", entity_id
        ) == ("memorial")
