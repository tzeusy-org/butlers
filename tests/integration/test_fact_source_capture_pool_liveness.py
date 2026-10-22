"""Real PostgreSQL/role/registered-MCP source admission at pool capacity one.

REQ-relationship-facts-006: source liveness and unlocked resolution do not
permit stale or overwritten attribution. Requires the normal supported PG lane.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from butlers.core.fact_authority import FactSourceContextRegistry
from butlers.db import register_jsonb_codec
from tests.integration import test_relationship_fact_content_authority as authority_cases

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
authority_env = authority_cases.env


@pytest.fixture(scope="module")
async def single_slot_pool(authority_env):
    async def setup(conn):
        await conn.execute("SET ROLE butler_switchboard_rw")

    pool = await asyncpg.create_pool(
        authority_env.url,
        min_size=1,
        max_size=1,
        init=register_jsonb_codec,
        setup=setup,
        server_settings={"search_path": "switchboard,public"},
    )
    try:
        yield pool
    finally:
        try:
            await asyncio.wait_for(pool.close(), 5)
        except TimeoutError:
            pool.terminate()
            raise


async def owner_source(env):
    address = f"{uuid.uuid4()}@example.test"
    await env.admin.execute(
        "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
        "VALUES($1,'has-email',$2,'literal','legacy')",
        env.owner,
        address,
    )
    return await authority_cases._accepted(env, address)


async def test_capacity_one_registered_owner_capture_and_concurrent_sources(
    authority_env, single_slot_pool
):
    issuer = FactSourceContextRegistry(single_slot_pool)
    inboxes = [await owner_source(authority_env) for _ in range(3)]
    async with asyncio.timeout(15):
        reports = await asyncio.gather(
            *(issuer.capture_accepted_report(inbox, authority_env.owner) for inbox in inboxes)
        )
        for inbox, report in zip(inboxes, reports, strict=True):
            stored = await single_slot_pool.fetchval(
                "SELECT request_context->'_fact_source_report' "
                "FROM switchboard.message_inbox WHERE id=$1",
                inbox,
            )
            assert report.authority == "owner"
            assert report.original_entity_id == authority_env.owner
            assert report.entity_created_at is not None
            assert stored == report.to_record()
            token = issuer.issue(inbox, "relationship")
            assert await issuer.verify(token, "relationship", "incarnation", str(inbox)) == report
        assert await single_slot_pool.fetchval("SELECT current_user") == "butler_switchboard_rw"


async def test_source_update_during_owning_lookup_refuses_stale_attribution(
    authority_env, single_slot_pool
):
    issuer = FactSourceContextRegistry(single_slot_pool)
    inbox = await owner_source(authority_env)
    actual_resolve = issuer.resolve_identity
    paused, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        result = await actual_resolve(*args, **kwargs)
        paused.set()
        await release.wait()
        return result

    issuer.resolve_identity = resolve
    capture = asyncio.create_task(issuer.capture_accepted_report(inbox, authority_env.owner))
    try:
        async with asyncio.timeout(10):
            await paused.wait()
            # A separate real transaction must not wait for capture's inbox lock.
            async with asyncio.timeout(1):
                await authority_env.sw.execute(
                    "UPDATE switchboard.message_inbox SET normalized_text=$2 WHERE id=$1",
                    inbox,
                    "Changed accepted source",
                )
            release.set()
            with pytest.raises(ValueError, match="accepted source.*changed"):
                await capture
        assert (
            await single_slot_pool.fetchval(
                "SELECT request_context->'_fact_source_report' "
                "FROM switchboard.message_inbox WHERE id=$1",
                inbox,
            )
            is None
        )
        assert inbox not in issuer._reports
        assert await single_slot_pool.fetchval("SELECT 1") == 1
    finally:
        capture.cancel()
        await asyncio.gather(capture, return_exceptions=True)


async def test_concurrent_committed_report_wins_over_an_earlier_lookup(
    authority_env, single_slot_pool
):
    issuer = FactSourceContextRegistry(single_slot_pool)
    winner_issuer = FactSourceContextRegistry(single_slot_pool)
    inbox = await owner_source(authority_env)
    actual_resolve = issuer.resolve_identity
    paused, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        result = await actual_resolve(*args, **kwargs)
        paused.set()
        await release.wait()
        return result

    issuer.resolve_identity = resolve
    capture = asyncio.create_task(issuer.capture_accepted_report(inbox, authority_env.owner))
    try:
        async with asyncio.timeout(10):
            await paused.wait()
            winner = await winner_issuer.capture_accepted_report(inbox, None, mixed=True)
            assert winner.authority == "mixed"
            release.set()
            assert await capture == winner
        assert (
            await single_slot_pool.fetchval(
                "SELECT request_context->'_fact_source_report' "
                "FROM switchboard.message_inbox WHERE id=$1",
                inbox,
            )
            == winner.to_record()
        )
        assert issuer._reports[inbox] == winner
    finally:
        capture.cancel()
        await asyncio.gather(capture, return_exceptions=True)
