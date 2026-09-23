"""Real-Postgres regression: infra-state QA discovery views (bu-9r3hd.4).

Exercises migrations ``sw_024`` / ``sw_028`` (``public.v_qa_connector_state`` /
``public.v_qa_butler_heartbeat``) against a fully migrated Postgres instance
(testcontainers), not just the mocked-pool unit tests in
``tests/core/qa/test_infra_state.py``:

- Both views exist and are queryable.
- ``v_qa_connector_state`` surfaces live and registered ``connector_registry``
  rows while excluding soft-deleted, archived, and storage-only checkpoint rows.
- ``v_qa_butler_heartbeat`` surfaces a ``butler_registry`` row with its
  ``liveness_ttl_seconds`` / ``quarantined_at`` columns intact.
- Downgrade cleanly drops both views.

Uses ``schemas={"switchboard": "switchboard"}`` (rather than leaving both
chains unmapped into ``public``) to prove the ACTUAL deployed shape works:
``connector_registry`` / ``butler_registry`` live under a schema literally
named ``switchboard`` (mirroring cli.py's ``_migrate_all``, which passes
``schema=config.db_schema`` for the switchboard butler's own chain run), and
the migration's unqualified ``FROM connector_registry`` must resolve via
that chain's own search_path rather than accidentally binding to some other
schema's same-named table.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import create_engine

from butlers.db import register_jsonb_codec
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_both_views_exist_and_are_queryable(pool: asyncpg.Pool) -> None:
    await pool.execute("SELECT 1 FROM public.v_qa_connector_state LIMIT 0")
    await pool.execute("SELECT 1 FROM public.v_qa_butler_heartbeat LIMIT 0")


@pytest.mark.asyncio(loop_scope="session")
async def test_connector_view_surfaces_live_row_and_excludes_non_liveness_rows(
    pool: asyncpg.Pool,
) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at, first_seen_at,
             operational_role)
        VALUES ($1, $2, $3, $4, $5, 'runtime_instance')
        """,
        "gmail",
        "owner@example.com",
        "error",
        now - timedelta(minutes=20),
        now - timedelta(days=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at,
             first_seen_at, archived_at, operational_role)
        VALUES ($1, $2, $3, $4, $5, $6, 'runtime_instance')
        """,
        "spotify",
        "owner",
        "error",
        now - timedelta(days=90),
        now - timedelta(days=100),
        now - timedelta(days=40),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at,
             first_seen_at, deleted_at, operational_role)
        VALUES ($1, $2, $3, $4, $5, $6, 'runtime_instance')
        """,
        "owntracks",
        "owner",
        "error",
        now - timedelta(days=90),
        now - timedelta(days=100),
        now - timedelta(days=40),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, checkpoint_cursor,
             checkpoint_updated_at, first_seen_at, operational_role)
        VALUES ($1, $2, $3, $4, $5, 'checkpoint')
        """,
        "google_health",
        "google_health:user:owner@example.com:account-id:hrv",
        "cursor-value",
        now - timedelta(minutes=5),
        now - timedelta(days=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, instance_id, checkpoint_cursor,
             checkpoint_updated_at, first_seen_at, operational_role)
        VALUES ($1, $2, $3, $4, $5, $6, 'runtime_instance')
        """,
        "telegram_bot",
        "owner",
        "11111111-1111-1111-1111-111111111111",
        "cursor-value",
        now - timedelta(minutes=5),
        now - timedelta(minutes=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, registered_via, first_seen_at)
        VALUES ($1, $2, $3, $4)
        """,
        "webhook",
        "owner",
        "operator",
        now - timedelta(minutes=5),
    )

    rows = await pool.fetch("SELECT * FROM public.v_qa_connector_state ORDER BY connector_type")

    # sw_031: the view excludes rows by their persisted ``operational_role``, not
    # by column nullability. The google_health row is gone because it SAYS it is a
    # checkpoint; the webhook row (role never established, so 'unknown') stays
    # visible so an unclassified connector can be investigated rather than dropped.
    assert [r["connector_type"] for r in rows] == ["gmail", "telegram_bot", "webhook"]
    row = rows[0]
    assert row["endpoint_identity"] == "owner@example.com"
    assert row["state"] == "error"
    assert row["last_heartbeat_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_heartbeat_view_surfaces_registry_row(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO switchboard.butler_registry
            (name, endpoint_url, last_seen_at, liveness_ttl_seconds, quarantined_at,
             quarantine_reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (name) DO UPDATE SET
            last_seen_at = EXCLUDED.last_seen_at,
            liveness_ttl_seconds = EXCLUDED.liveness_ttl_seconds,
            quarantined_at = EXCLUDED.quarantined_at,
            quarantine_reason = EXCLUDED.quarantine_reason
        """,
        "finance",
        "http://finance:41100/sse",
        now - timedelta(hours=1),
        300,
        now - timedelta(minutes=30),
        "no heartbeat in ttl window",
    )

    row = await pool.fetchrow(
        "SELECT * FROM public.v_qa_butler_heartbeat WHERE name = $1", "finance"
    )

    assert row is not None
    assert row["liveness_ttl_seconds"] == 300
    assert row["quarantined_at"] is not None
    assert row["last_seen_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_qa_receiver_view_exposes_current_boot_health_without_policy_authority(
    pool: asyncpg.Pool,
) -> None:
    from butlers.core.qa.sources.infra_state import InfraStateSource

    now = datetime.now(UTC)
    name = "qa_receiver_health"
    await pool.execute(
        """
        INSERT INTO switchboard.butler_registry
            (name, endpoint_url, last_seen_at, liveness_ttl_seconds)
        VALUES ($1, 'http://localhost:41100/mcp', $2, 300)
        """,
        name,
        now - timedelta(hours=2),
    )
    await pool.execute(
        """
        UPDATE switchboard.butler_registry_control_plane
           SET policy_state = 'quarantined',
               observed_state = 'healthy',
               boot_epoch = 2,
               observed_boot_epoch = 2,
               healthy_observed_at = $2
         WHERE name = $1
        """,
        name,
        now,
    )

    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_qa_rw"')
        try:
            row = await conn.fetchrow(
                "SELECT * FROM public.v_qa_butler_receiver_state WHERE name = $1", name
            )
            assert row is not None
            assert row["observed_state"] == "healthy"
            assert row["current_boot_observed"] is True
            assert row["healthy_observed_at"] == now
            assert "policy_state" not in row.keys()
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval(
                    "SELECT policy_state FROM switchboard.butler_registry_control_plane "
                    "WHERE name = $1",
                    name,
                )
        finally:
            await conn.execute("RESET ROLE")

    source = InfraStateSource(pool=pool)
    findings = await source._check_receiver_heartbeats(now)
    assert not any(f.source_butler == name for f in findings)

    await pool.execute(
        "UPDATE switchboard.butler_registry_control_plane "
        "SET observed_state = 'unavailable' WHERE name = $1",
        name,
    )
    failed = await pool.fetchrow(
        "SELECT * FROM public.v_qa_butler_receiver_state WHERE name = $1", name
    )
    assert failed["observed_state"] == "unavailable"
    assert failed["healthy_observed_at"] == now
    findings = await source._check_receiver_heartbeats(now)
    assert any(f.source_butler == name for f in findings)

    await pool.execute(
        "UPDATE switchboard.butler_registry_control_plane "
        "SET observed_state = 'healthy', observed_boot_epoch = 1 WHERE name = $1",
        name,
    )
    old_boot = await pool.fetchrow(
        "SELECT * FROM public.v_qa_butler_receiver_state WHERE name = $1", name
    )
    assert old_boot["current_boot_observed"] is False
    findings = await source._check_receiver_heartbeats(now)
    assert any(f.source_butler == name for f in findings)


@pytest.mark.asyncio(loop_scope="session")
async def test_fleet_board_receiver_projection_ignores_legacy_heartbeat(
    pool: asyncpg.Pool,
) -> None:
    """Execute the board's actual SQL against separated registry facts."""
    from butlers.api.routers.butlers import _BOARD_RECEIVER_REGISTRY_SQL

    now = datetime.now(UTC)
    names = ("board_ready", "board_failed_probe", "board_old_boot", "board_held")
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO switchboard, public")
        for name in names:
            await conn.execute(
                """
                INSERT INTO switchboard.butler_registry
                    (name, endpoint_url, eligibility_state, last_seen_at)
                VALUES ($1, $2, 'stale', $3)
                """,
                name,
                f"http://{name}:41100/mcp",
                now - timedelta(hours=2),
            )
            await conn.execute(
                """
                UPDATE switchboard.butler_registry_control_plane
                   SET observed_state = 'healthy', healthy_observed_at = $2,
                       boot_epoch = 1, observed_boot_epoch = 1,
                       route_compatible = true, accepting_routes = true
                 WHERE name = $1
                """,
                name,
                now,
            )

        await conn.execute(
            "UPDATE switchboard.butler_registry_control_plane "
            "SET observed_state = 'unavailable' WHERE name = 'board_failed_probe'"
        )
        await conn.execute(
            "UPDATE switchboard.butler_registry_control_plane "
            "SET observed_boot_epoch = 0 WHERE name = 'board_old_boot'"
        )
        await conn.fetchval("SELECT public.set_butler_registry_policy('board_held', 'quarantined')")

        projected = {
            row["name"]: row
            for row in await conn.fetch(_BOARD_RECEIVER_REGISTRY_SQL)
            if row["name"] in names
        }

    assert set(projected) == set(names)
    assert projected["board_ready"]["eligibility_state"] == "active"
    assert projected["board_ready"]["last_seen_at"] == now
    assert projected["board_failed_probe"]["eligibility_state"] == "stale"
    assert projected["board_old_boot"]["eligibility_state"] == "stale"
    assert projected["board_held"]["eligibility_state"] == "quarantined"
    assert projected["board_held"]["quarantine_reason"] == "protected_policy:quarantined"


async def _as_role(pool: asyncpg.Pool, role: str, sql: str, *args):
    async with pool.acquire() as conn:
        await conn.execute(f'SET ROLE "{role}"')
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.execute("RESET ROLE")


@pytest.mark.asyncio(loop_scope="session")
async def test_registry_boot_epochs_and_probe_fences_use_database_authority(
    pool: asyncpg.Pool,
) -> None:
    from butlers.tools.switchboard.registry.registry import (
        get_control_plane_state,
        record_probe,
        register_boot,
        reserve_probe,
    )

    await pool.execute(
        "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
        "VALUES ('finance', 'http://finance:41101/mcp') "
        "ON CONFLICT (name) DO NOTHING"
    )

    async def boot():
        async with pool.acquire() as conn:
            await conn.execute('SET ROLE "butler_finance_rw"')
            try:
                return await register_boot(conn, "finance", uuid4())
            finally:
                await conn.execute("RESET ROLE")

    first, second = await asyncio.gather(boot(), boot())
    assert sorted((first, second)) == [1, 2]

    # A rolled-back successor cannot become the current epoch.
    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_finance_rw"')
        try:
            with pytest.raises(RuntimeError, match="abort registration"):
                async with conn.transaction():
                    assert await register_boot(conn, "finance", uuid4()) == 3
                    raise RuntimeError("abort registration")
        finally:
            await conn.execute("RESET ROLE")

    state = await get_control_plane_state(pool, "finance")
    assert state is not None and state["boot_epoch"] == 2

    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_switchboard_rw"')
        try:
            epoch, sequence = await reserve_probe(conn, "finance")
            assert epoch == 2
            server_before = await conn.fetchval("SELECT clock_timestamp()")
            assert not await record_probe(
                conn,
                "finance",
                boot_epoch=1,
                probe_sequence=sequence,
                healthy=True,
                compatible=True,
                accepting=True,
            )
            assert await record_probe(
                conn,
                "finance",
                boot_epoch=epoch,
                probe_sequence=sequence,
                healthy=True,
                compatible=True,
                accepting=True,
            )
            server_after = await conn.fetchval("SELECT clock_timestamp()")
        finally:
            await conn.execute("RESET ROLE")

    state = await get_control_plane_state(pool, "finance")
    assert state is not None
    healthy_at = state["healthy_observed_at"]
    assert state["observed_state"] == "healthy"
    assert state["observed_boot_epoch"] == 2
    assert healthy_at is not None
    assert server_before <= healthy_at <= server_after

    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_switchboard_rw"')
        try:
            _, older_sequence = await reserve_probe(conn, "finance")
            _, newer_sequence = await reserve_probe(conn, "finance")
            assert newer_sequence > older_sequence
            assert not await record_probe(
                conn,
                "finance",
                boot_epoch=2,
                probe_sequence=older_sequence,
                healthy=True,
                compatible=True,
                accepting=True,
            )
            assert await record_probe(
                conn,
                "finance",
                boot_epoch=2,
                probe_sequence=newer_sequence,
                healthy=False,
                compatible=None,
                accepting=None,
                failure_class="timeout",
            )
        finally:
            await conn.execute("RESET ROLE")

    failed = await get_control_plane_state(pool, "finance")
    assert failed is not None
    assert failed["observed_state"] == "unavailable"
    assert failed["last_probe_at"] >= healthy_at
    assert failed["healthy_observed_at"] == healthy_at
    assert failed["probe_failure_class"] == "timeout"
    assert failed["policy_state"] == "active"

    assert (
        await pool.fetchval("SELECT public.set_butler_registry_policy('finance', 'quarantined')")
        == "quarantined"
    )
    successor = await boot()
    assert successor == 3
    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_switchboard_rw"')
        try:
            epoch, sequence = await reserve_probe(conn, "finance")
            assert epoch == successor
            assert not await record_probe(
                conn,
                "finance",
                boot_epoch=2,
                probe_sequence=sequence,
                healthy=True,
                compatible=True,
                accepting=True,
            )
            assert await record_probe(
                conn,
                "finance",
                boot_epoch=successor,
                probe_sequence=sequence,
                healthy=True,
                compatible=True,
                accepting=True,
            )
        finally:
            await conn.execute("RESET ROLE")
    held = await get_control_plane_state(pool, "finance")
    assert held is not None and held["policy_state"] == "quarantined"
    assert held["observed_state"] == "healthy"
    assert held["legacy_eligibility_state"] == "quarantined"


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_registration_retry_cannot_reclaim_after_successor(
    pool: asyncpg.Pool,
) -> None:
    """The immutable UUID ledger makes lost-response retries harmless."""
    from butlers.tools.switchboard.registry.registry import register_boot

    await pool.execute(
        "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
        "VALUES ('general', 'http://general:41101/mcp') "
        "ON CONFLICT (name) DO NOTHING"
    )

    async def boot(instance_id):
        async with pool.acquire() as conn:
            await conn.execute('SET ROLE "butler_general_rw"')
            try:
                return await register_boot(conn, "general", instance_id)
            finally:
                await conn.execute("RESET ROLE")

    first_instance = uuid4()
    assert await asyncio.gather(boot(first_instance), boot(first_instance)) == [1, 1]
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM switchboard.butler_boot_registrations WHERE name = 'general'"
        )
        == 1
    )

    successor_instance = uuid4()
    assert await boot(successor_instance) == 2
    with pytest.raises(asyncpg.PostgresError):
        await boot(first_instance)
    assert await boot(successor_instance) == 2

    row = await pool.fetchrow(
        "SELECT boot_epoch, boot_instance_id FROM "
        "switchboard.butler_registry_control_plane WHERE name = 'general'"
    )
    assert row["boot_epoch"] == 2
    assert row["boot_instance_id"] == successor_instance
    ledger = await pool.fetch(
        "SELECT boot_instance_id, boot_epoch FROM "
        "switchboard.butler_boot_registrations WHERE name = 'general' "
        "ORDER BY boot_epoch"
    )
    assert [(row["boot_instance_id"], row["boot_epoch"]) for row in ledger] == [
        (first_instance, 1),
        (successor_instance, 2),
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_registry_runtime_roles_cannot_forge_policy_or_observation(
    pool: asyncpg.Pool,
    migrated_db_url: str,
) -> None:
    await pool.execute(
        "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
        "VALUES ('health', 'http://health:41102/mcp') "
        "ON CONFLICT (name) DO NOTHING"
    )
    assert (
        await pool.fetchval(
            "SELECT policy_state FROM switchboard.butler_registry_control_plane "
            "WHERE name = 'health'"
        )
        == "active"
    )
    async with pool.acquire() as conn:
        await conn.execute('SET ROLE "butler_health_rw"')
        try:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    "UPDATE switchboard.butler_registry_control_plane "
                    "SET policy_state = 'active' WHERE name = 'health'"
                )
            with pytest.raises(asyncpg.PostgresError):
                await conn.fetchval("SELECT public.reserve_butler_probe('health')")
            with pytest.raises(asyncpg.PostgresError):
                await conn.fetchval("SELECT public.set_butler_registry_policy('health', 'active')")
            assert (
                await conn.fetchval("SELECT public.register_butler_boot('health', $1)", uuid4())
                == 1
            )
            with pytest.raises(asyncpg.PostgresError):
                await conn.fetchval("SELECT public.register_butler_boot('finance', $1)", uuid4())
        finally:
            await conn.execute("RESET ROLE")

    with pytest.raises(asyncpg.PostgresError):
        await _as_role(
            pool,
            "butler_switchboard_rw",
            "SELECT public.set_butler_registry_policy('health', 'active')",
        )
    assert (
        await _as_role(
            pool,
            "butler_switchboard_rw",
            "SELECT policy_state FROM switchboard.butler_registry_control_plane "
            "WHERE name = 'health'",
        )
        == "active"
    )
    # RLS may reject with an error or silently filter the UPDATE to zero rows.
    assert (
        await _as_role(
            pool,
            "butler_switchboard_rw",
            "UPDATE switchboard.butler_registry_control_plane "
            "SET observed_state = 'healthy' WHERE name = 'health' RETURNING boot_epoch",
        )
        is None
    )
    await _assert_l2_roster_seed_and_boot_retry_preserve_existing_authority(migrated_db_url)
    await _assert_l2_receiver_records_under_narrow_role_without_dashboard_owner_session(
        migrated_db_url
    )


async def _assert_l2_roster_seed_and_boot_retry_preserve_existing_authority(
    migrated_db_url: str,
) -> None:
    """A daemon can start before classification without seeding over owner policy."""
    from butlers.tools.switchboard.registry.registry import seed_missing_roster_butlers

    roster_dir = Path(__file__).resolve().parents[2] / "roster"
    pool = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=4, init=register_jsonb_codec
    )
    assert pool is not None
    try:
        await pool.execute(
            "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
            "VALUES ('health', 'http://health:41103/mcp') ON CONFLICT (name) DO NOTHING"
        )
        previous_endpoint = await pool.fetchval(
            "SELECT endpoint_url FROM switchboard.butler_registry WHERE name = 'health'"
        )
        await pool.fetchval("SELECT public.set_butler_registry_policy('health', 'quarantined')")
        previous_epoch = await pool.fetchval(
            "SELECT boot_epoch FROM switchboard.butler_registry_control_plane WHERE name = 'health'"
        )
        old_uuid = uuid4()
        health_epoch = await _as_role(
            pool,
            "butler_health_rw",
            "SELECT public.register_butler_boot('health', $1)",
            old_uuid,
        )
        assert health_epoch == previous_epoch + 1

        education_uuid = uuid4()
        # No classification request has populated Education yet.  The narrow
        # operation refuses to invent its row; Switchboard's insert-only seed
        # creates it and a retry with the same UUID commits exactly one epoch.
        with pytest.raises(asyncpg.PostgresError):
            await _as_role(
                pool,
                "butler_education_rw",
                "SELECT public.register_butler_boot('education', $1)",
                education_uuid,
            )

        async def register_after_seed() -> int:
            for _ in range(100):
                try:
                    return await _as_role(
                        pool,
                        "butler_education_rw",
                        "SELECT public.register_butler_boot('education', $1)",
                        education_uuid,
                    )
                except asyncpg.PostgresError:
                    await asyncio.sleep(0.01)
            raise AssertionError("boot registration never observed committed roster seed")

        _, registered_epoch = await asyncio.gather(
            seed_missing_roster_butlers(pool, roster_dir), register_after_seed()
        )
        assert registered_epoch == 1
        assert (
            await _as_role(
                pool,
                "butler_education_rw",
                "SELECT public.register_butler_boot('education', $1)",
                education_uuid,
            )
            == 1
        )
        await seed_missing_roster_butlers(pool, roster_dir)
        rows = await pool.fetch(
            "SELECT c.name, c.policy_state, c.boot_epoch, r.endpoint_url "
            "FROM switchboard.butler_registry_control_plane AS c "
            "JOIN switchboard.butler_registry AS r USING (name) "
            "WHERE c.name IN ('health', 'education')"
        )
        by_name = {row["name"]: row for row in rows}
        assert by_name["health"]["policy_state"] == "quarantined"
        assert by_name["health"]["boot_epoch"] == health_epoch
        assert by_name["health"]["endpoint_url"] == previous_endpoint
        assert by_name["education"]["boot_epoch"] == 1
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM switchboard.butler_boot_registrations "
                "WHERE name = 'education'"
            )
            == 1
        )
    finally:
        await pool.close()


async def _assert_l2_receiver_records_under_narrow_role_without_dashboard_owner_session(
    migrated_db_url: str,
) -> None:
    """Mounted owner auth stays closed while Switchboard's probe uses its DB role."""
    from butlers.api.app import create_app
    from butlers.config import load_config
    from butlers.core.control_plane_identity import (
        DashboardProbeRoleView,
        ExpectedDaemon,
        SingleFlightProber,
        run_shadow_cycle,
    )
    from butlers.core.utils import generate_uuid7_string

    config = load_config(Path(__file__).resolve().parents[2] / "roster" / "health")
    expected = ExpectedDaemon(config.name, config.port, "butlers-up")
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    assert pool is not None
    try:
        await pool.execute(
            "INSERT INTO switchboard.butler_registry (name, endpoint_url, capabilities) "
            "VALUES ('health', 'http://health:41103/mcp', '[\"trigger\"]'::jsonb) "
            "ON CONFLICT (name) DO NOTHING"
        )
        await pool.execute(
            "UPDATE switchboard.butler_registry SET capabilities = '[\"trigger\"]'::jsonb "
            "WHERE name = 'health'"
        )
        await pool.fetchval("SELECT public.set_butler_registry_policy('health', 'quarantined')")
        await pool.execute(
            "UPDATE switchboard.butler_registry SET endpoint_url = "
            "'http://stored-registry-is-not-roster:49999/mcp' WHERE name = 'health'"
        )
        boot_uuid = UUID(generate_uuid7_string())
        epoch = await _as_role(
            pool,
            "butler_health_rw",
            "SELECT public.register_butler_boot('health', $1)",
            boot_uuid,
        )
        identity = {
            "schema_version": "butler.control.v1",
            "butler_name": config.name,
            "boot_instance_id": str(boot_uuid),
            "boot_epoch": epoch,
            "route_contract": {"min": 1, "max": 1},
            "accepting_routes": True,
        }
        app = create_app(api_key="synthetic-mounted-owner-key")
        app.state.ready = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://butlers.example.test"
        ) as browser:
            assert (await browser.get("/api/butlers")).status_code in (401, 503)
            public_health = await browser.get("/api/health")
            assert "boot_instance_id" not in public_health.text
            assert "boot_epoch" not in public_health.text

        def respond(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == expected.url
            assert "cookie" not in request.headers
            assert "x-api-key" not in request.headers
            return httpx.Response(200, json=identity)

        receiver = DashboardProbeRoleView(pool)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            prober = SingleFlightProber(receiver, client=client)
            cycle = await run_shadow_cycle(receiver, (expected,), prober)
        assert cycle.complete and cycle.recorded_count == 1
        assert (
            await receiver.fetchval(
                "SELECT observed_state FROM switchboard.butler_registry_control_plane "
                "WHERE name = 'health'"
            )
            == "healthy"
        )
        healthy_at = await receiver.fetchval(
            "SELECT healthy_observed_at FROM switchboard.butler_registry_control_plane "
            "WHERE name = 'health'"
        )
        older = await receiver.fetchrow(
            "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe('health')"
        )
        # Another process using the Switchboard runtime role reserves a newer
        # sequence and records a failed attempt before the Dashboard result.
        async with pool.acquire() as conn:
            await conn.execute('SET ROLE "butler_switchboard_rw"')
            try:
                newer = await conn.fetchrow(
                    "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe('health')"
                )
                assert newer["probe_sequence"] > older["probe_sequence"]
                assert await conn.fetchval(
                    "SELECT public.record_butler_probe($1,$2,$3,$4,$5,$6,$7)",
                    "health",
                    epoch,
                    newer["probe_sequence"],
                    False,
                    None,
                    None,
                    "timeout",
                )
            finally:
                await conn.execute("RESET ROLE")
        assert not await receiver.fetchval(
            "SELECT public.record_butler_probe($1,$2,$3,$4,$5,$6,$7)",
            "health",
            epoch,
            older["probe_sequence"],
            True,
            True,
            True,
            None,
        )
        state = await pool.fetchrow(
            "SELECT observed_state, healthy_observed_at, policy_state "
            "FROM switchboard.butler_registry_control_plane WHERE name = 'health'"
        )
        assert state["observed_state"] == "unavailable"
        assert state["healthy_observed_at"] == healthy_at
        assert state["policy_state"] == "quarantined"
        # No owner cookie or key was involved, but raw policy writes are still
        # denied to the observer's effective role on each pool operation.
        assert (
            await receiver.fetchval(
                "UPDATE switchboard.butler_registry_control_plane "
                "SET policy_state = 'active' WHERE name = 'health' "
                "RETURNING policy_state"
            )
            is None
        )
        with pytest.raises(asyncpg.PostgresError):
            await receiver.fetchval("SELECT public.set_butler_registry_policy('health', 'active')")

        # L3's prospective Switchboard route consumes the same CAS ledger as
        # this Dashboard receiver, but only when its default-off cutover is
        # explicitly enabled.  A late Dashboard result cannot undo the route
        # probe, and an owner hold remains a separate terminal policy gate.
        from unittest.mock import patch

        from butlers.tools.switchboard.routing.route import route

        await pool.fetchval("SELECT public.set_butler_registry_policy('health', 'active')")
        older = await receiver.fetchrow(
            "SELECT boot_epoch, probe_sequence FROM public.reserve_butler_probe('health')"
        )

        async def switchboard_role_setup(conn: asyncpg.Connection) -> None:
            await conn.execute('SET ROLE "butler_switchboard_rw"')

        route_pool = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=2, setup=switchboard_role_setup
        )
        assert route_pool is not None
        target_calls: list[str] = []

        async def accepted_target(endpoint_url: str, _tool: str, _args: dict) -> dict:
            target_calls.append(endpoint_url)
            return {"status": "accepted"}

        async def route_identity(request: httpx.Request) -> httpx.Response:
            assert request.url.port == config.port
            assert request.url.path == "/internal/control-plane/identity"
            return httpx.Response(200, json=identity)

        try:
            route_client = httpx.AsyncClient(transport=httpx.MockTransport(route_identity))
            with (
                patch.dict("os.environ", {"BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER": "1"}),
                patch(
                    "butlers.tools.switchboard.routing.route.httpx.AsyncClient",
                    return_value=route_client,
                ),
            ):
                routed = await route(
                    route_pool,
                    "health",
                    "route.execute",
                    {},
                    required_capability="trigger",
                    call_fn=accepted_target,
                )
            assert routed["transport"]["outcome"] == "confirmed", routed
            assert target_calls == [f"http://localhost:{config.port}/mcp"]
            assert not await receiver.fetchval(
                "SELECT public.record_butler_probe($1,$2,$3,$4,$5,$6,$7)",
                "health",
                epoch,
                older["probe_sequence"],
                False,
                None,
                None,
                "timeout",
            )
            latest = await pool.fetchrow(
                "SELECT policy_state, observed_state, recorded_probe_sequence "
                "FROM switchboard.butler_registry_control_plane WHERE name = 'health'"
            )
            assert latest["policy_state"] == "active" and latest["observed_state"] == "healthy"
            assert latest["recorded_probe_sequence"] > older["probe_sequence"]

            await pool.fetchval("SELECT public.set_butler_registry_policy('health', 'quarantined')")
            with patch.dict("os.environ", {"BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER": "1"}):
                denied = await route(
                    route_pool,
                    "health",
                    "route.execute",
                    {},
                    allow_stale=True,
                    allow_quarantined=True,
                    call_fn=accepted_target,
                )
            assert denied["transport"]["outcome"] == "not_attempted"
            assert denied["retryable"] is False
            assert len(target_calls) == 1
        finally:
            await route_pool.close()
        await _assert_l2_dashboard_probe_role_resets(migrated_db_url)
    finally:
        await pool.close()


async def _assert_l2_dashboard_probe_role_resets(
    migrated_db_url: str,
) -> None:
    from butlers.core.control_plane_identity import DashboardProbeRoleView

    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=1)
    assert pool is not None

    async def assert_reused_connection_is_unprivileged() -> None:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_setting('role')") == "none"

    try:
        receiver = DashboardProbeRoleView(pool)
        assert await receiver.fetchval("SELECT current_user") == "butler_switchboard_rw"
        await assert_reused_connection_is_unprivileged()

        with pytest.raises(asyncpg.PostgresError):
            await receiver.fetchval("SELECT 1 / 0")
        await assert_reused_connection_is_unprivileged()

        in_flight = asyncio.create_task(receiver.fetchval("SELECT pg_sleep(5)"))
        await asyncio.sleep(0.05)
        in_flight.cancel()
        with pytest.raises(asyncio.CancelledError):
            await in_flight
        await assert_reused_connection_is_unprivileged()
    finally:
        await pool.close()


def test_registry_quarantine_migration_and_rollback_preserve_authority(
    postgres_container,
) -> None:
    """Proven TTL, proven owner, and ambiguous histories remain distinct."""
    from alembic import command
    from butlers.migrations import _build_alembic_config
    from butlers.tools.switchboard.registry.registry import register_butler
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep
    from butlers.tools.switchboard.routing.route import _touch_registry_liveness

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        revisions={"switchboard": "sw_034"},
    )
    stamp = datetime.now(UTC) - timedelta(minutes=20)

    async def seed_and_check() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
        try:
            for name, reason in (
                ("finance", "operator_action"),
                ("health", "liveness_ttl_2x_expired"),
                ("general", None),
            ):
                await p.execute(
                    """
                    INSERT INTO switchboard.butler_registry (
                        name, endpoint_url, eligibility_state, quarantined_at,
                        eligibility_updated_at, last_seen_at
                    ) VALUES ($1, $2, 'quarantined', $3, $3, $4)
                    """,
                    name,
                    f"http://{name}:41100/mcp",
                    stamp,
                    stamp - timedelta(hours=1),
                )
                if reason:
                    await p.execute(
                        """
                        INSERT INTO switchboard.butler_registry_eligibility_log (
                            butler_name, previous_state, new_state, reason,
                            observed_at
                        ) VALUES ($1, 'stale', 'quarantined', $2, $3)
                        """,
                        name,
                        reason,
                        stamp,
                    )
            for name, reason in (
                ("messenger", "operator_action"),
                ("travel", "liveness_ttl_expired"),
                ("home", None),
            ):
                await p.execute(
                    """
                    INSERT INTO switchboard.butler_registry (
                        name, endpoint_url, eligibility_state,
                        eligibility_updated_at, last_seen_at
                    ) VALUES ($1, $2, 'stale', $3, $4)
                    """,
                    name,
                    f"http://{name}:41100/mcp",
                    stamp,
                    stamp - timedelta(hours=1),
                )
                if reason:
                    await p.execute(
                        """
                        INSERT INTO switchboard.butler_registry_eligibility_log (
                            butler_name, previous_state, new_state, reason,
                            observed_at
                        ) VALUES ($1, 'active', 'stale', $2, $3)
                        """,
                        name,
                        reason,
                        stamp,
                    )
        finally:
            await p.close()

    asyncio.run(seed_and_check())
    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_035")

    async def assert_classification_and_writes() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
        try:
            rows = await p.fetch(
                "SELECT name, policy_state, policy_provenance, observed_state, "
                "legacy_evidence FROM switchboard.butler_registry_control_plane"
            )
            by_name = {row["name"]: row for row in rows}
            assert by_name["finance"]["policy_state"] == "quarantined"
            assert by_name["finance"]["policy_provenance"] == "legacy_operator"
            assert by_name["health"]["policy_state"] == "active"
            assert by_name["health"]["observed_state"] == "stale"
            assert by_name["health"]["policy_provenance"] == "legacy_ttl"
            assert by_name["general"]["policy_state"] == "review_required"
            assert by_name["messenger"]["policy_state"] == "paused"
            assert by_name["messenger"]["policy_provenance"] == "legacy_operator"
            assert by_name["travel"]["policy_state"] == "active"
            assert by_name["travel"]["observed_state"] == "stale"
            assert by_name["home"]["policy_state"] == "review_required"
            original_evidence = by_name["finance"]["legacy_evidence"]

            await register_butler(p, "finance", "http://finance:41100/mcp")
            await _touch_registry_liveness(p, "finance")
            await register_butler(p, "general", "http://general:41100/mcp")
            await register_butler(p, "messenger", "http://messenger:41100/mcp")
            for name in ("finance", "general", "messenger", "home"):
                assert (
                    await p.fetchval(
                        "SELECT eligibility_state FROM switchboard.butler_registry WHERE name = $1",
                        name,
                    )
                    == "quarantined"
                )

            # A proven TTL transition does not become sticky operator policy.
            await register_butler(p, "health", "http://health:41100/mcp")
            assert (
                await p.fetchval(
                    "SELECT policy_state FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'health'"
                )
                == "active"
            )
            await register_butler(p, "concierge", "http://concierge:41100/mcp")
            await p.execute(
                "UPDATE switchboard.butler_registry SET last_seen_at = $1 WHERE name = 'concierge'",
                datetime.now(UTC) - timedelta(hours=1),
            )
            await run_eligibility_sweep(p)
            assert (
                await p.fetchval(
                    "SELECT eligibility_state FROM switchboard.butler_registry "
                    "WHERE name = 'concierge'"
                )
                == "quarantined"
            )
            assert (
                await p.fetchval(
                    "SELECT policy_state FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'concierge'"
                )
                == "active"
            )

            # Simulate the old writer after Alembic rollback.  The table and
            # trigger remain, so rollback cannot clear either restrictive row.
            old_instance = uuid4()
            current_instance = uuid4()
            assert (
                await _as_role(
                    p,
                    "butler_finance_rw",
                    "SELECT public.register_butler_boot('finance', $1)",
                    old_instance,
                )
                == 1
            )
            assert (
                await _as_role(
                    p,
                    "butler_finance_rw",
                    "SELECT public.register_butler_boot('finance', $1)",
                    current_instance,
                )
                == 2
            )
            command.downgrade(config, "switchboard@sw_034")
            with pytest.raises(asyncpg.PostgresError):
                await _as_role(
                    p,
                    "butler_finance_rw",
                    "SELECT public.register_butler_boot('finance', $1)",
                    old_instance,
                )
            assert (
                await _as_role(
                    p,
                    "butler_finance_rw",
                    "SELECT public.register_butler_boot('finance', $1)",
                    current_instance,
                )
                == 2
            )
            assert (
                await p.fetchval(
                    "SELECT count(*) FROM switchboard.butler_boot_registrations "
                    "WHERE name = 'finance'"
                )
                == 2
            )
            await p.execute(
                "UPDATE switchboard.butler_registry SET eligibility_state = 'active', "
                "quarantined_at = NULL, quarantine_reason = NULL "
                "WHERE name IN ('finance', 'general')"
            )
            for name in ("finance", "general"):
                assert (
                    await p.fetchval(
                        "SELECT eligibility_state FROM switchboard.butler_registry WHERE name = $1",
                        name,
                    )
                    == "quarantined"
                )
            reserved = await _as_role(
                p,
                "butler_switchboard_rw",
                "SELECT probe_sequence FROM public.reserve_butler_probe('finance')",
            )
            assert reserved is not None
            assert not await _as_role(
                p,
                "butler_switchboard_rw",
                "SELECT public.record_butler_probe('finance', 1, $1, true, true, true, NULL)",
                reserved,
            )
            with pytest.raises(asyncpg.PostgresError):
                await _as_role(
                    p,
                    "butler_switchboard_rw",
                    "SELECT public.record_butler_probe("
                    "'finance', NULL, $1, true, true, true, NULL)",
                    reserved,
                )
            command.upgrade(config, "switchboard@sw_035")
            assert (
                await p.fetchval(
                    "SELECT legacy_evidence FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'finance'"
                )
                == original_evidence
            )
            assert (
                await p.fetchval("SELECT public.set_butler_registry_policy('general', 'active')")
                == "active"
            )
            assert (
                await p.fetchval(
                    "SELECT eligibility_state FROM switchboard.butler_registry "
                    "WHERE name = 'general'"
                )
                == "active"
            )
            assert (
                await p.fetchval(
                    "SELECT policy_provenance FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'general'"
                )
                == "operator"
            )
        finally:
            await p.close()

    asyncio.run(assert_classification_and_writes())


def test_legacy_read_path_ttl_receipt_reclassification_preserves_owner_holds(
    postgres_container,
) -> None:
    """Only a sw_035 receipt matching the old ttl_expired writer is repaired."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        revisions={"switchboard": "sw_034"},
    )
    stamp = datetime.now(UTC) - timedelta(minutes=20)

    async def seed() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
        try:
            for name, reason, log_at in (
                ("general", "ttl_expired", stamp),
                ("health", "operator_action", stamp),
                ("finance", "ttl_expired", stamp - timedelta(seconds=1)),
                ("home", None, None),
                ("travel", "ttl_expired", stamp),
            ):
                await p.execute(
                    """
                    INSERT INTO switchboard.butler_registry (
                        name, endpoint_url, eligibility_state,
                        eligibility_updated_at, last_seen_at
                    ) VALUES ($1, $2, 'stale', $3, $4)
                    """,
                    name,
                    f"http://{name}:41100/mcp",
                    stamp,
                    stamp - timedelta(hours=1),
                )
                if reason is not None:
                    await p.execute(
                        """
                        INSERT INTO switchboard.butler_registry_eligibility_log (
                            butler_name, previous_state, new_state, reason, observed_at
                        ) VALUES ($1, 'active', 'stale', $2, $3)
                        """,
                        name,
                        reason,
                        log_at,
                    )
            await p.execute(
                """
                INSERT INTO switchboard.butler_registry (
                    name, endpoint_url, eligibility_state, quarantined_at,
                    eligibility_updated_at, last_seen_at
                ) VALUES ('concierge', 'http://concierge:41100/mcp',
                          'quarantined', $1, $1, $2)
                """,
                stamp,
                stamp - timedelta(hours=1),
            )
            await p.execute(
                """
                INSERT INTO switchboard.butler_registry_eligibility_log (
                    butler_name, previous_state, new_state, reason, observed_at
                ) VALUES ('concierge', 'stale', 'quarantined', 'ttl_expired', $1)
                """,
                stamp,
            )
        finally:
            await p.close()

    asyncio.run(seed())
    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_035")

    async def owner_override_and_snapshot() -> dict:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
        try:
            assert (
                await p.fetchval(
                    "SELECT policy_state FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'general'"
                )
                == "review_required"
            )
            receipt = await p.fetchval(
                "SELECT legacy_evidence FROM switchboard.butler_registry_control_plane "
                "WHERE name = 'general'"
            )
            await p.fetchval("SELECT public.set_butler_registry_policy('travel', 'quarantined')")
            return receipt
        finally:
            await p.close()

    receipt = asyncio.run(owner_override_and_snapshot())
    command.upgrade(config, "switchboard@sw_036")

    async def assert_repair() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
        try:
            rows = await p.fetch(
                "SELECT c.name, c.policy_state, c.policy_provenance, "
                "c.observed_state, c.legacy_evidence, r.eligibility_state, "
                "r.quarantined_at, r.quarantine_reason "
                "FROM switchboard.butler_registry_control_plane AS c "
                "JOIN switchboard.butler_registry AS r USING (name)"
            )
            by_name = {row["name"]: row for row in rows}
            repaired = by_name["general"]
            assert sum(row["policy_provenance"] == "legacy_ttl" for row in rows) == 1
            assert repaired["policy_state"] == "active"
            assert repaired["policy_provenance"] == "legacy_ttl"
            assert repaired["observed_state"] == "stale"
            assert repaired["legacy_evidence"] == receipt
            assert repaired["eligibility_state"] == "stale"
            assert repaired["quarantined_at"] is None
            assert repaired["quarantine_reason"] is None

            assert by_name["health"]["policy_state"] == "paused"
            assert by_name["health"]["policy_provenance"] == "legacy_operator"
            for name in ("finance", "home", "concierge"):
                assert by_name[name]["policy_state"] == "review_required"
                assert by_name[name]["eligibility_state"] == "quarantined"
            assert by_name["travel"]["policy_state"] == "quarantined"
            assert by_name["travel"]["policy_provenance"] == "operator"
            assert by_name["travel"]["eligibility_state"] == "quarantined"
        finally:
            await p.close()

    asyncio.run(assert_repair())

    # A migration replay must not rewrite the repaired receipt or authority.
    command.downgrade(config, "switchboard@sw_035")
    command.upgrade(config, "switchboard@sw_036")
    asyncio.run(assert_repair())


def test_registry_policy_rls_survives_bootstrap_grant_replay(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )

    async def seed() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
        try:
            await p.execute(
                "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
                "VALUES ('health', 'http://health:41102/mcp')"
            )
            assert (
                await p.fetchval(
                    "SELECT public.set_butler_registry_policy('health', 'quarantined')"
                )
                == "quarantined"
            )
        finally:
            await p.close()

    asyncio.run(seed())

    # Replay the production bootstrap that can re-widen table grants.  RLS and
    # the role checks inside definer operations must remain the authority.
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name),
        isolation_level="AUTOCOMMIT",
    )
    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (urlparse(db_url).username,),
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()

    async def assert_fence() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
        try:
            assert (
                await _as_role(
                    p,
                    "butler_switchboard_rw",
                    "UPDATE switchboard.butler_registry_control_plane "
                    "SET policy_state = 'active' WHERE name = 'health' "
                    "RETURNING policy_state",
                )
                is None
            )
            with pytest.raises(asyncpg.PostgresError):
                await _as_role(
                    p,
                    "butler_switchboard_rw",
                    "INSERT INTO switchboard.butler_boot_registrations "
                    "(name, boot_instance_id, boot_epoch) "
                    "VALUES ('health', $1, 99) RETURNING boot_epoch",
                    uuid4(),
                )
            assert (
                await p.fetchval(
                    "SELECT policy_state FROM switchboard.butler_registry_control_plane "
                    "WHERE name = 'health'"
                )
                == "quarantined"
            )
            assert (
                await p.fetchval(
                    "SELECT eligibility_state FROM switchboard.butler_registry "
                    "WHERE name = 'health'"
                )
                == "quarantined"
            )
        finally:
            await p.close()

    asyncio.run(assert_fence())


def test_downgrade_drops_both_views(postgres_container) -> None:
    """Mirrors test_switchboard_spot_check_index_migration.py's downgrade test shape:
    a standalone DB (not the shared module fixture) so the downgrade never
    affects the other tests in this module.
    """
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )

    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_023")

    async def _assert_views_gone() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
        try:
            row = await p.fetchrow(
                "SELECT to_regclass('public.v_qa_connector_state') AS connector_view, "
                "to_regclass('public.v_qa_butler_heartbeat') AS heartbeat_view"
            )
            assert row["connector_view"] is None
            assert row["heartbeat_view"] is None
        finally:
            await p.close()

    import asyncio

    asyncio.run(_assert_views_gone())
