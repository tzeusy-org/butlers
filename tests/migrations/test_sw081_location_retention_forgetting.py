"""sw_081: Switchboard's location forgetting record is append-only, and the downgrade fences it.

Real PostgreSQL. The runtime role reads and appends forgotten keys and receipts and may take the
forget lock; nothing updates, deletes or truncates either table; a receipt's outcome agrees with
its blocked count and a grant completes at most once. The downgrade drops empty tables (and the
``message_inbox`` trigger) and refuses while either table holds a row (bu-s11n0s.7 S4c-2).
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration

_RUNTIME = "butler_switchboard_rw"
_KEYS = "switchboard.location_retention_forgotten_keys"
_RUNS = "switchboard.location_retention_forgetting"
_RECEIPT = (
    f"INSERT INTO {_RUNS} (grant_id, outcome, item_count, forgotten_count, "
    "already_forgotten_count, absent_count, blocked_count) VALUES ($1, $2, 1, 0, 0, 0, $3)"
)


def _db(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_081"},  # pinned-revision: owns the forgetting record
    )


def _config(url: str):
    return _build_alembic_config(url, ["switchboard"], target_schema="switchboard")


async def _run(url: str, *statements: tuple) -> None:
    conn = await asyncpg.connect(url)
    try:
        for statement, *args in statements:
            await conn.execute(statement, *args)
    finally:
        await conn.close()


async def _privileges(url: str) -> dict[str, dict[str, bool]]:
    conn = await asyncpg.connect(url)
    try:
        return {
            table: {
                privilege: await conn.fetchval(
                    "SELECT has_table_privilege($1, $2, $3)", _RUNTIME, table, privilege
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            for table in (_KEYS, _RUNS)
        }
    finally:
        await conn.close()


def test_the_runtime_role_may_only_read_and_append(postgres_container) -> None:
    url = _db(postgres_container)
    expected = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
    assert asyncio.run(_privileges(url)) == {_KEYS: expected, _RUNS: expected}
    asyncio.run(
        _run(
            url,
            (f"SET ROLE {_RUNTIME}",),
            ("SELECT switchboard.location_retention_forget_lock()",),
            ("SELECT switchboard.location_retention_forget_guard()",),
        )
    )


def test_the_record_is_append_only_and_completes_once(postgres_container) -> None:
    url = _db(postgres_container)
    grant = uuid4()
    asyncio.run(
        _run(
            url,
            (f"INSERT INTO {_KEYS} (logical_source_key, grant_id) VALUES ('k', $1)", grant),
            (_RECEIPT, grant, "blocked", 1),
            (_RECEIPT, grant, "blocked", 1),
            (_RECEIPT, grant, "complete", 0),
        )
    )
    for statement in (
        (f"UPDATE {_KEYS} SET grant_id = $1", uuid4()),
        (f"DELETE FROM {_RUNS}",),
        (f"TRUNCATE {_KEYS}",),
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            asyncio.run(_run(url, statement))
    for statement in (
        (_RECEIPT, grant, "complete", 0),  # a second complete
        (_RECEIPT, uuid4(), "complete", 1),  # complete with a blocked copy
        (_RECEIPT, uuid4(), "blocked", 0),  # blocked with nothing blocked
        (f"INSERT INTO {_KEYS} (logical_source_key, grant_id) VALUES ('k', $1)", uuid4()),
    ):
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            asyncio.run(_run(url, statement))


def test_the_downgrade_fences_a_record_and_drops_an_empty_one(postgres_container) -> None:
    url = _db(postgres_container)
    asyncio.run(_run(url, (_RECEIPT, uuid4(), "blocked", 1)))
    with pytest.raises(Exception, match="forgetting is recorded"):
        command.downgrade(_config(url), "switchboard@sw_080")  # pinned-revision: sw_081 fence

    empty = _db(postgres_container)
    command.downgrade(_config(empty), "switchboard@sw_080")  # pinned-revision: sw_081 fence

    async def _gone() -> tuple:
        conn = await asyncpg.connect(empty)
        try:
            return await conn.fetchrow(
                f"SELECT to_regclass('{_KEYS}'), to_regclass('{_RUNS}'), "
                "(SELECT count(*) FROM pg_trigger "
                " WHERE tgname = 'message_inbox_withhold_forgotten_location')"
            )
        finally:
            await conn.close()

    assert tuple(asyncio.run(_gone())) == (None, None, 0)
