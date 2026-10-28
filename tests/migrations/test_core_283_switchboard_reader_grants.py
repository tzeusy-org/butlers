"""core_283 / sw_054: three roles read only switchboard.message_inbox (bu-svqpzo.13).

Real PostgreSQL (Docker), CI only.  The relationship, messenger and travel runtime roles
used to hold SELECT on every Switchboard table (core_077, core_143), including
``butler_secrets``, plus default privileges for every future table.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.core.state import state_get, state_set
from butlers.db import register_jsonb_codec, schema_search_path
from butlers.jobs.calendar_prep import (
    prep_key,
    run_messenger_calendar_prep_contribution,
    run_travel_calendar_prep_contribution,
)
from butlers.migrations import _build_alembic_config, get_chain_head
from butlers.modules.calendar import CalendarEvent, CalendarModule, EventStatus
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.config.test_schema_acl_isolation import _rerun_init_db_as_control

pytestmark = [pytest.mark.integration, pytest.mark.db]

_ROLES = ("butler_relationship_rw", "butler_messenger_rw", "butler_travel_rw")


def _configs(db_url: str):
    core = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    chain = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    return core, chain


def _select_grants(db_url: str, role: str) -> dict[str, bool]:
    """has_table_privilege(role, ..., SELECT) for every switchboard table."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT c.relname, has_table_privilege(CAST(:role AS name), c.oid, 'SELECT') "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'switchboard' AND c.relkind IN ('r', 'p') "
                    "AND NOT c.relispartition"
                ),
                {"role": role},
            )
            return {name: bool(granted) for name, granted in rows}
    finally:
        engine.dispose()


def _readable(db_url: str, role: str) -> set[str]:
    return {name for name, granted in _select_grants(db_url, role).items() if granted}


async def _exercise_calendar_prep_readers(db_url: str) -> None:
    """Exercise real LOCAL projections, jobs and state through the narrowed runtime roles."""
    jobs = {
        "messenger": run_messenger_calendar_prep_contribution,
        "travel": run_travel_calendar_prep_contribution,
    }
    now = datetime.now(UTC)
    received_at = now - timedelta(minutes=1)
    entity_id = uuid4()
    entity_name = f"SV13 native attendee {entity_id}"
    thread_id = f"sv13-native-thread-{uuid4()}"
    subject = "Synthetic native meeting context"
    message = "Harmless persisted native message"
    sentinel_key = "sv13/native-unrelated-state"
    sentinel = {"retained": True}
    pools = {}
    events = {}

    async with AsyncExitStack() as stack:
        # This ordinary migration identity owns only disposable setup/fault SQL.
        # Pool methods finish each statement before any later migration or DDL.
        control = await asyncpg.create_pool(
            db_url,
            min_size=1,
            max_size=1,
            init=register_jsonb_codec,
            server_settings={"search_path": schema_search_path("switchboard")},
        )
        stack.push_async_callback(control.close)
        for schema, chain in (("switchboard", "switchboard"), *[(s, s) for s in jobs]):
            versions = await control.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
            assert {row["version_num"] for row in versions} == {
                get_chain_head("core"),
                get_chain_head(chain),
            }
        await control.execute(
            "INSERT INTO public.entities(id, canonical_name, entity_type) VALUES ($1, $2, 'person')",
            entity_id,
            entity_name,
        )
        await control.execute(
            "SELECT switchboard.switchboard_message_inbox_ensure_partition($1)", received_at
        )
        await control.execute(
            "INSERT INTO switchboard.message_inbox "
            "(received_at, request_context, raw_payload, direction, normalized_text) "
            "VALUES ($1, $2, $3, 'inbound', $4)",
            received_at,
            {
                "source_channel": "email",
                "source_sender_entity_id": str(entity_id),
                "source_thread_identity": thread_id,
                "subject": subject,
            },
            {},
            message,
        )

        for schema in jobs:
            role = f"butler_{schema}_rw"

            async def setup(connection, *, runtime_role=role):
                await connection.execute(f'SET ROLE "{runtime_role}"')

            pool = await asyncpg.create_pool(
                db_url,
                min_size=1,
                max_size=2,
                init=register_jsonb_codec,
                setup=setup,
                server_settings={"search_path": schema_search_path(schema)},
            )
            stack.push_async_callback(pool.close)
            pools[schema] = pool
            async with pool.acquire() as connection:
                assert await connection.fetchval("SELECT current_user") == role
                assert await connection.fetchval("SELECT current_schema()") == schema
                assert await connection.fetchval("SHOW search_path") == schema_search_path(schema)
                # Returning this connection must not make the next acquire run as the login.
                await connection.execute("RESET ROLE")
            assert await pool.fetchval("SELECT current_user") == role
            assert await pool.fetchval("SELECT count(*) FROM switchboard.message_inbox") == 1
            for relation in (
                "switchboard.butler_secrets",
                "switchboard.notifications",
                f"{'travel' if schema == 'messenger' else 'messenger'}.state",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.fetchval(f"SELECT count(*) FROM {relation}")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.execute(
                    "UPDATE switchboard.notifications SET message=message WHERE false"
                )

            # These LOCAL methods issue SQL only: never start the module/provider.
            module = CalendarModule()
            module._db = SimpleNamespace(pool=pool)
            module._butler_name = schema
            source_id = await module._ensure_calendar_source(
                source_key=f"provider:google:sv13-native-{schema}",
                source_kind="provider",
                lane="user",
                provider="google",
                calendar_id=f"sv13-native-{schema}",
                butler_name=schema,
                writable=False,
            )
            assert source_id is not None
            event = CalendarEvent(
                event_id=f"sv13-native-{schema}-{uuid4()}",
                title=f"SV13 {schema} meeting",
                start_at=now + timedelta(hours=1),
                end_at=now + timedelta(hours=2),
                timezone="UTC",
                status=EventStatus.confirmed,
                entity_ids=[entity_id],
                source_butler=schema,
                butler_name=schema,
            )
            await module._project_provider_changes(
                source_id=source_id,
                provider_name="google",
                calendar_id=f"sv13-native-{schema}",
                updated_events=[event],
                cancelled_ids=[],
            )
            row = await pool.fetchrow(
                "SELECT e.id, e.source_butler, e.source_id, cee.entity_id "
                "FROM calendar_events e JOIN calendar_event_entities cee ON cee.event_id=e.id "
                "WHERE e.origin_ref=$1",
                event.event_id,
            )
            assert row is not None
            assert row["source_butler"] == schema
            assert row["source_id"] == source_id
            assert row["entity_id"] == entity_id
            events[schema] = (row["id"], event.title)
            await state_set(pool, sentinel_key, sentinel)

        async def populated(schema: str) -> int:
            pool = pools[schema]
            result = await jobs[schema](pool, None)
            assert result["butler"] == schema
            assert result["events_written"] == result["attendees"] == result["threads"] == 1
            assert result["pruned"] == 0
            event_id, title = events[schema]
            envelope = await state_get(pool, prep_key(str(event_id)))
            assert envelope is not None
            assert envelope["butler"] == schema
            assert envelope["event_id"] == str(event_id)
            assert envelope["event_title"] == title
            assert envelope["has_context"] is True
            assert len(envelope["attendees"]) == 1
            attendee = envelope["attendees"][0]
            assert attendee["entity_id"] == str(entity_id)
            assert attendee["name"] == entity_name
            assert attendee["message_context"] == [
                {
                    "channel": "email",
                    "thread_id": thread_id,
                    "subject": subject,
                    "snippet": message,
                    "last_message_at": received_at.isoformat(),
                    "message_count": 1,
                }
            ]
            assert await state_get(pool, sentinel_key) == sentinel
            return await pool.fetchval(
                "SELECT version FROM state WHERE key=$1", prep_key(str(event_id))
            )

        versions = {schema: await populated(schema) for schema in jobs}
        try:
            await control.execute(
                "REVOKE SELECT ON TABLE switchboard.message_inbox FROM butler_messenger_rw"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pools["messenger"].fetchval("SELECT count(*) FROM switchboard.message_inbox")
            result = await jobs["messenger"](pools["messenger"], None)
            assert result["events_written"] == result["attendees"] == result["threads"] == 0
            assert result["pruned"] == 1
            event_id, _ = events["messenger"]
            assert await state_get(pools["messenger"], prep_key(str(event_id))) is None
            assert await state_get(pools["messenger"], sentinel_key) == sentinel
            assert await populated("travel") > versions["travel"]
        finally:
            # Restore exactly the one named grant, including on failed assertions.
            await control.execute(
                "GRANT SELECT ON TABLE switchboard.message_inbox TO butler_messenger_rw"
            )
        await populated("messenger")


def test_fresh_database_grants_message_inbox_and_nothing_else(postgres_container) -> None:
    """Core runs before the Switchboard chain; sw_054 supplies the grant once the table exists."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")

    for role in _ROLES:
        grants = _select_grants(db_url, role)
        assert grants["message_inbox"] is True
        assert "butler_secrets" in grants
        assert _readable(db_url, role) == {"message_inbox"}, role

    # Migrate before opening any async control/runtime connections. Replaying
    # the real core chain supplies each LOCAL calendar projection and state.
    for schema in ("messenger", "travel"):
        for name in ("core", schema):
            command.upgrade(
                _build_alembic_config(db_url, chains=[name], target_schema=schema), f"{name}@head"
            )
    asyncio.run(_exercise_calendar_prep_readers(db_url))
    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_existing_wide_grants_are_narrowed_and_future_tables_are_not_readable(
    postgres_container,
) -> None:
    """A database already at core_268 holds the schema-wide grants; core_283 removes them."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core_268")
    command.upgrade(chain, "switchboard@sw_046")

    for role in _ROLES:
        before = _readable(db_url, role)
        assert {"message_inbox", "butler_secrets"} <= before, role

    command.upgrade(core, "core@head")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.future_goal_ledger (id int)"))
    finally:
        engine.dispose()

    for role in _ROLES:
        grants = _select_grants(db_url, role)
        assert grants["future_goal_ledger"] is False, role
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_core_283_restores_a_revoked_message_inbox_grant(postgres_container) -> None:
    """Repair path: the inbox grant is missing, core_283 runs, and the grant is restored."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    command.downgrade(core, "core_282")

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for role in _ROLES:
                connection.execute(text(f"REVOKE ALL ON switchboard.message_inbox FROM {role}"))
    finally:
        engine.dispose()
    for role in _ROLES:
        assert _select_grants(db_url, role)["message_inbox"] is False, role

    command.upgrade(core, "core@head")

    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role


def test_downgrade_restores_the_previous_grants_and_upgrade_repeats_cleanly(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    assert _readable(db_url, "butler_travel_rw") == {"message_inbox"}

    command.downgrade(core, "core_282")
    # Roll back only this unpublished narrowing revision. Public core_271..282
    # remain installed, and the managed migration identity owns this DDL.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.rollback_read_probe (id int)"))
    finally:
        engine.dispose()
    for role in _ROLES:
        assert "butler_secrets" in _readable(db_url, role), role
        assert _select_grants(db_url, role)["rollback_read_probe"] is True, role

    command.upgrade(core, "core@head")
    command.upgrade(core, "core@head")  # idempotent for the next butler schema's core run
    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role


def _relationship_switchboard_default_acl(db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl AS d "
                        "JOIN pg_catalog.pg_namespace AS n ON n.oid = d.defaclnamespace "
                        "CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) AS a "
                        "WHERE n.nspname = 'switchboard' AND d.defaclobjtype = 'r' "
                        "AND a.grantee = (SELECT oid FROM pg_catalog.pg_roles "
                        "WHERE rolname = 'butler_relationship_rw') "
                        "AND a.privilege_type = 'SELECT')"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_rerunning_the_bootstrap_after_head_does_not_rewiden_relationship_reads(
    postgres_container,
) -> None:
    """The documented bootstrap rerun must not restore the schema-wide read or its default."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core, chain = _configs(db_url)
    command.upgrade(core, "core@head")
    command.upgrade(chain, "switchboard@head")
    assert _readable(db_url, "butler_relationship_rw") == {"message_inbox"}

    migration_user = urlparse(db_url).username
    assert migration_user is not None
    _rerun_init_db_as_control(
        migration_bootstrap_db_url(postgres_container, db_name), migration_user
    )

    assert _readable(db_url, "butler_relationship_rw") == {"message_inbox"}
    assert _relationship_switchboard_default_acl(db_url) is False
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE switchboard.later_table (id int)"))
            for role in _ROLES:
                assert (
                    connection.execute(
                        text(
                            "SELECT has_schema_privilege(CAST(:role AS name), 'switchboard', 'USAGE')"
                        ),
                        {"role": role},
                    ).scalar_one()
                    is True
                )
    finally:
        engine.dispose()
    assert _select_grants(db_url, "butler_relationship_rw")["later_table"] is False
    for role in _ROLES:
        assert _readable(db_url, role) == {"message_inbox"}, role
        assert _select_grants(db_url, role)["later_table"] is False, role
