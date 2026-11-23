"""core_366: ``connectors.calendar_event_revisions`` is append-only, keyed and narrowly granted.

Real PostgreSQL. Covers the baseline/change/replay/resync write path through
``record_calendar_revisions``, the unique identity key, policy-blocked and cancelled rows, the
verbatim-versus-scrubbed contract, the UPDATE/DELETE/TRUNCATE guard, the grants, and the downgrade
refusal while rows exist (bu-uhbhzq.1 slice 2). Synthetic ids and text only.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.connectors.calendar_event_revisions import record_calendar_revisions
from butlers.connectors.google_calendar import _parse_dt
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_CORE_366 = Path(__file__).resolve().parents[2] / (
    "alembic/versions/core/core_366_calendar_event_revisions.py"
)
_ENDPOINT = "google_calendar:user:synthetic@example.test"


def _below_core_366() -> str:
    spec = importlib.util.spec_from_file_location("core_366_under_test", _CORE_366)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return f"core@{module.down_revision}"


def _event(event_id: str, etag: str, start: str = "2026-11-24T14:00:00+00:00", **extra) -> dict:
    return {
        "id": event_id,
        "etag": etag,
        "status": "confirmed",
        "summary": f"Synthetic title {event_id}",
        "location": "Synthetic Room",
        "description": "Agenda. Join https://meet.example.test/abc passcode: 998877",
        "start": {"dateTime": start},
        "end": {"dateTime": "2026-11-24T15:00:00+00:00"},
        "updated": "2026-11-23T01:00:00.000Z",
        "attendees": [{"email": "guest@example.test", "responseStatus": "needsAction"}],
        **extra,
    }


async def _record(pool: asyncpg.Pool, events: list[dict], observation: str, **kwargs) -> None:
    await record_calendar_revisions(
        pool,
        endpoint_identity=_ENDPOINT,
        calendar_id="primary",
        events=events,
        observation=observation,
        parse=_parse_dt,
        **kwargs,
    )


async def _count(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT count(*) FROM connectors.calendar_event_revisions")


async def test_baseline_change_replay_and_resync_append_exactly_the_new_versions(
    db_pool: asyncpg.Pool,
) -> None:
    baseline = [_event("e1", "a1"), _event("e2", "b1"), _event("e3", "c1")]
    await _record(db_pool, baseline, "baseline")
    assert await _count(db_pool) == 3

    moved = _event("e1", "a2", start="2026-11-24T16:00:00+00:00")
    rsvp = _event(
        "e2",
        "b2",
        attendees=[{"email": "guest@example.test", "responseStatus": "accepted"}],
    )
    await _record(db_pool, [moved, rsvp], "change")
    assert await _count(db_pool) == 5
    kinds = await db_pool.fetch(
        "SELECT observation, count(*) AS n FROM connectors.calendar_event_revisions "
        "GROUP BY observation ORDER BY observation"
    )
    assert {r["observation"]: r["n"] for r in kinds} == {"baseline": 3, "change": 2}

    await _record(db_pool, [moved, rsvp], "change")  # replaying the same page
    await _record(db_pool, [_event("e3", "c1")], "resync")  # 410 recovery, unchanged etag
    assert await _count(db_pool) == 5


async def test_a_cancelled_instance_is_a_revision_and_a_blocked_event_keeps_no_raw(
    db_pool: asyncpg.Pool,
) -> None:
    cancelled = {"id": "e9", "etag": "z2", "status": "cancelled"}
    blocked = _event("e8", "y1")
    await _record(db_pool, [cancelled, blocked], "change", blocked=lambda e: e["id"] == "e8")
    rows = {
        r["event_id"]: r
        for r in await db_pool.fetch(
            "SELECT event_id, status, policy_blocked, raw, starts_at "
            "FROM connectors.calendar_event_revisions"
        )
    }
    assert rows["e9"]["status"] == "cancelled" and rows["e9"]["policy_blocked"] is False
    assert rows["e8"]["policy_blocked"] is True and rows["e8"]["raw"] is None
    assert rows["e8"]["starts_at"] is not None  # structure and times survive


async def test_raw_keeps_title_location_and_attendees_but_scrubs_description_links_and_passcodes(
    db_pool: asyncpg.Pool,
) -> None:
    await _record(db_pool, [_event("e1", "a1")], "baseline")
    raw = await db_pool.fetchval("SELECT raw FROM connectors.calendar_event_revisions")
    assert raw["summary"] == "Synthetic title e1" and raw["location"] == "Synthetic Room"
    assert raw["attendees"][0]["email"] == "guest@example.test"
    assert "meet.example.test" not in raw["description"] and "998877" not in raw["description"]


async def test_the_journal_is_append_only(db_pool: asyncpg.Pool) -> None:
    await _record(db_pool, [_event("e1", "a1")], "baseline")
    for statement in (
        "UPDATE connectors.calendar_event_revisions SET status = 'x'",
        "DELETE FROM connectors.calendar_event_revisions",
        "TRUNCATE connectors.calendar_event_revisions",
    ):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await db_pool.execute(statement)
    assert await _count(db_pool) == 1


async def test_only_the_writer_and_chronicler_hold_privileges_and_never_update_or_delete(
    db_pool: asyncpg.Pool,
) -> None:
    grants = await db_pool.fetch(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'connectors' AND table_name = 'calendar_event_revisions' "
        "AND grantee <> current_user"
    )
    by_role: dict[str, set[str]] = {}
    for grant in grants:
        by_role.setdefault(grant["grantee"], set()).add(grant["privilege_type"])
    assert by_role == {"connector_writer": {"SELECT", "INSERT"}, "butler_chronicler_rw": {"SELECT"}}
    for role in ("connector_writer", "butler_chronicler_rw"):
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not await db_pool.fetchval(
                "SELECT has_table_privilege($1, 'connectors.calendar_event_revisions', $2)",
                role,
                privilege,
            )


def test_the_downgrade_refuses_while_rows_exist(fresh_db) -> None:
    url = fresh_db("core")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connectors.calendar_event_revisions (endpoint_identity, "
                    "calendar_id, event_id, etag, observation) "
                    "VALUES ('e', 'primary', 'e1', 'a1', 'baseline')"
                )
            )
        config = _build_alembic_config(url, chains=["core"])
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(config, _below_core_366())
    finally:
        engine.dispose()


_TABLE = "connectors.calendar_event_revisions"


def _acl(engine) -> set[tuple[str, str, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT coalesce(r.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable "
                "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
                "LEFT JOIN pg_roles r ON r.oid = a.grantee "
                "WHERE c.oid = to_regclass(:t) AND a.grantee <> c.relowner"
            ),
            {"t": _TABLE},
        )
        return {(r[0], r[1], r[2]) for r in rows}


_EXACT = {
    ("connector_writer", "SELECT", False),
    ("connector_writer", "INSERT", False),
    ("butler_chronicler_rw", "SELECT", False),
}


def _upgrade(db_url: str, schema: str):
    config = _build_alembic_config(db_url, chains=["core"], target_schema=schema)
    command.upgrade(config, "core_366")
    return config


def test_a_second_schema_is_catalog_only_and_a_downgrade_keeps_the_shared_table(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _upgrade(db_url, "general")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    holder = engine.connect()
    try:
        assert _acl(engine) == _EXACT
        # The peer's scenario: an open INSERT transaction on the journal holds ROW EXCLUSIVE. That
        # blocks table DDL (CREATE INDEX, CREATE OR REPLACE TRIGGER), but not the ACL-only
        # REVOKE/GRANT. The fresh second schema replays core_001/core_007's blanket grants, so its
        # ACL has drifted: the upgrade must still succeed, through the ACL-only branch, and a
        # replay of the DDL would hit the lock timeout.
        holder.execute(text("BEGIN"))
        holder.execute(
            text(
                f"INSERT INTO {_TABLE} (endpoint_identity, calendar_id, event_id, etag, "
                "observation) VALUES ('held', 'primary', 'held-1', 'h1', 'baseline')"
            )
        )
        db_name = engine.url.database
        with engine.connect() as admin:
            admin.execute(text(f"ALTER DATABASE \"{db_name}\" SET lock_timeout = '1s'"))
        try:
            second = _upgrade(db_url, "travel")
        finally:
            holder.execute(text("ROLLBACK"))
            with engine.connect() as admin:
                admin.execute(text(f'ALTER DATABASE "{db_name}" RESET lock_timeout'))
        assert _acl(engine) == _EXACT

        command.downgrade(first, "core_362")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass(:t)"), {"t": _TABLE}).scalar() is not None
        command.downgrade(second, "core_362")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass(:t)"), {"t": _TABLE}).scalar() is None
    finally:
        holder.close()
        engine.dispose()


@pytest.mark.parametrize(
    "stray",
    [
        "GRANT SELECT ON TABLE {t} TO PUBLIC",
        "GRANT TRIGGER ON TABLE {t} TO connector_writer",
        "GRANT SELECT ON TABLE {t} TO butler_chronicler_rw WITH GRANT OPTION",
    ],
)
def test_a_later_schema_upgrade_repairs_a_stray_grant(postgres_container, stray: str) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    _upgrade(db_url, "general")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(stray.format(t=_TABLE)))
        assert _acl(engine) != _EXACT
        _upgrade(db_url, "travel")
        assert _acl(engine) == _EXACT
    finally:
        engine.dispose()
