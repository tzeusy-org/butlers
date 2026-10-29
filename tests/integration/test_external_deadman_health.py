"""Migrated SQL proof for the deadman claim/readback and receiver-health gate.

Only HTTP is controlled; no outside endpoint is called. Local collection is
not an executed PostgreSQL result. REQ-infrastructure-reliability-002.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.external_deadman import run_external_deadman_check
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


async def test_real_committed_tick_claim_refuses_concurrent_duplicate_and_unhealthy_fleet(
    migrated_db_url,
    monkeypatch,
):
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    started, release = asyncio.Event(), asyncio.Event()
    sends = []
    tasks = []
    tick = uuid.uuid4()

    async def ping(url):
        sends.append(True)
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr("butlers.jobs.external_deadman.ping_external_deadman", ping)
    try:
        await pool.execute(
            "INSERT INTO switchboard.butler_registry(name, endpoint_url) "
            "VALUES ('home', 'http://home.invalid/mcp')"
        )
        await pool.execute(
            "INSERT INTO switchboard.butler_registry_control_plane "
            "(name, policy_state, observed_state, boot_epoch, observed_boot_epoch, "
            "route_compatible, accepting_routes, healthy_observed_at) "
            "VALUES ('home', 'active', 'healthy', 1, 1, true, true, clock_timestamp()) "
            "ON CONFLICT(name) DO UPDATE SET policy_state='active', observed_state='healthy', "
            "boot_epoch=1, observed_boot_epoch=1, route_compatible=true, accepting_routes=true, "
            "healthy_observed_at=clock_timestamp()"
        )
        first = asyncio.create_task(
            run_external_deadman_check(
                pool,
                "https://monitor.invalid/ping",
                expected_names=("home",),
                tick_id=tick,
            )
        )
        tasks.append(first)
        await asyncio.wait_for(started.wait(), 5)
        # The first task is explicitly parked at HTTP, AFTER the real claim
        # commits. A separate acquisition proves durable state before retry.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log "
                "WHERE action='external_deadman_tick' AND request_id=$1",
                tick,
            )
            == 1
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log "
                "WHERE action='external_deadman_ping_success' AND request_id=$1",
                tick,
            )
            == 0
        )
        duplicate = await run_external_deadman_check(
            pool,
            "https://monitor.invalid/ping",
            expected_names=("home",),
            tick_id=tick,
        )
        assert duplicate == {"success": False, "cause": "duplicate_tick"}
        release.set()
        assert await asyncio.wait_for(first, 5) == {"success": True, "recorded": True}
        assert sends == [True]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log "
                "WHERE action='external_deadman_ping_success' AND request_id=$1",
                tick,
            )
            == 1
        )
        await pool.execute(
            "UPDATE switchboard.butler_registry_control_plane "
            "SET observed_state='unavailable' WHERE name='home'"
        )
        blocked_tick = uuid.uuid4()
        blocked = await run_external_deadman_check(
            pool,
            "https://monitor.invalid/ping",
            expected_names=("home",),
            tick_id=blocked_tick,
        )
        assert blocked == {"success": False, "cause": "fleet_unhealthy"}
        assert sends == [True]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.audit_log WHERE request_id=$1",
                blocked_tick,
            )
            == 0
        )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                await asyncio.wait_for(pool.close(), 5)
            except BaseException:
                pool.terminate()
                raise
