"""Publisher and reader against the activated health.dependencies profile on real PG."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine

from butlers.capability_health.model import Observation, reduce
from butlers.capability_health.publish import HealthPublisher
from butlers.capability_health.read import Availability, read_health
from butlers.capability_health.unavailable import first_unavailable
from butlers.db import register_jsonb_codec
from butlers.perception import PerceptionStore
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def reduction(state, cause, minutes=1):
    return reduce(
        "google", [Observation("google", state, cause, NOW - timedelta(minutes=minutes))], NOW
    )


@pytest.fixture
def dsn(postgres_container) -> str:
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    url = create_engine(migration_bootstrap_db_url(postgres_container, name)).url
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def transaction(dsn):
    conn = await asyncpg.connect(dsn, command_timeout=30)
    await register_jsonb_codec(conn)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def as_role(conn, role):
    await conn.execute("RESET ROLE")
    await conn.execute(f"SET LOCAL ROLE {role}")
    return PerceptionStore(conn)


async def test_publish_then_read_round_trip_with_cas_retry(dsn) -> None:
    async with transaction(dsn) as connection:
        writer = HealthPublisher(await as_role(connection, "perception_health_writer"))
        first = await writer.publish(reduction("down", "oauth_revoked"), NOW)
        assert first.published and first.generation == 1
        # A restarted publisher knows nothing: generation 0 conflicts, the retry lands at 2.
        restarted = HealthPublisher(await as_role(connection, "perception_health_writer"))
        second = await restarted.publish(reduction("available", None), NOW + timedelta(seconds=30))
        assert second.published and second.generation == 2

        reader = await as_role(connection, "perception_owner_reader")
        health = await read_health(reader, "google", NOW + timedelta(seconds=60))
        assert (health.state, health.cause, health.generation) == ("available", None, 2)
        assert health.availability is Availability.AVAILABLE and health.source_age_seconds == 120


async def test_a_known_down_dependency_yields_the_typed_result(dsn) -> None:
    async with transaction(dsn) as connection:
        writer = HealthPublisher(await as_role(connection, "perception_health_writer"))
        await writer.publish(reduction("down", "oauth_revoked", minutes=2), NOW)
        reader = await as_role(connection, "perception_owner_reader")
        health = await read_health(reader, "google", NOW + timedelta(seconds=10))
        result = first_unavailable([health])
        assert result is not None and result.cause == "oauth_revoked" and not result.retryable
        assert result.source_age_seconds == 130


async def test_unpublished_stale_and_unauthorized_reads_are_unknown(dsn) -> None:
    async with transaction(dsn) as connection:
        reader = await as_role(connection, "perception_owner_reader")
        assert (await read_health(reader, "spotify", NOW)).availability is Availability.UNKNOWN

        writer = HealthPublisher(await as_role(connection, "perception_health_writer"))
        await writer.publish(reduction("available", None), NOW)
        reader = await as_role(connection, "perception_owner_reader")
        later = NOW + timedelta(seconds=901)
        assert (await read_health(reader, "google", later)).availability is Availability.UNKNOWN

        # Not an authorized reader: the failure is swallowed into unknown.
        nobody = await as_role(connection, "perception_health_writer")
        assert (await read_health(nobody, "google", NOW)).availability is Availability.UNKNOWN


async def test_an_unmeasurable_publication_reads_as_unknown_not_healthy(dsn) -> None:
    async with transaction(dsn) as connection:
        writer = HealthPublisher(await as_role(connection, "perception_health_writer"))
        result = await writer.publish(reduce("google", [], NOW), NOW)
        assert result.published
        reader = await as_role(connection, "perception_owner_reader")
        health = await read_health(reader, "google", NOW)
        assert health.availability is Availability.UNKNOWN and health.state is None


async def test_the_writer_role_may_log_in_and_publish_directly_across_a_finalize(dsn) -> None:
    """Provisioning LOGIN on the writer role (and only it) survives the finalizer's re-run."""
    async with transaction(dsn) as connection:
        await connection.execute("ALTER ROLE perception_health_writer LOGIN PASSWORD 'x'")
        await connection.execute("SELECT perception_admin.finalize_interface()")
        attributes = await connection.fetchrow(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, "
            "rolreplication FROM pg_roles WHERE rolname='perception_health_writer'"
        )
        assert list(attributes.values()) == [True, False, False, False, False, False, False]
        assert not await connection.fetchval(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r "
            "ON r.oid IN (m.roleid, m.member) WHERE r.rolname='perception_health_writer'"
        )
        # A direct login has no SET ROLE: the writer is identified by its session user.
        await connection.execute("SET SESSION AUTHORIZATION perception_health_writer")
        writer = HealthPublisher(PerceptionStore(connection))
        assert (await writer.publish(reduction("available", None), NOW)).published


async def test_login_on_the_perception_owner_is_still_refused(dsn) -> None:
    async with transaction(dsn) as connection:
        await connection.execute("ALTER ROLE perception_owner LOGIN")
        with pytest.raises(asyncpg.PostgresError):
            await connection.execute("SELECT perception_admin.finalize_interface()")
