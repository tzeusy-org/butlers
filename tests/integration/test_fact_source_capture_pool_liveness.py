"""Real PostgreSQL/role/registered-MCP source admission at pool capacity one.

REQ-relationship-facts-006: source liveness and unlocked resolution do not
permit stale or overwritten attribution. Requires the normal supported PG lane.
The flag-absent legacy registry route is explicit; receiver-route admission
selection has separate owning controls and is not inferred from these tests.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

import asyncpg
import pytest
from fastmcp import Client, FastMCP

from butlers.core import fact_authority
from butlers.core.fact_authority import FactSourceContextRegistry
from butlers.daemon import ButlerDaemon
from butlers.db import register_jsonb_codec
from tests.integration import test_relationship_fact_content_authority as authority_cases

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
authority_env = authority_cases.env


@pytest.fixture(autouse=True)
def legacy_registry_route(monkeypatch):
    # The retained registered-MCP fixture publishes legacy registry state.
    # Bind that actual route instead of inheriting a deployment cutover flag.
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)


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


@pytest.mark.parametrize("interruption", ["acquire-timeout", "query-timeout", "cancel-query"])
async def test_initial_driver_deadlines_release_real_pool_without_partial_authority(
    authority_env, single_slot_pool, monkeypatch, interruption
):
    """Actual asyncpg waits/driver query cancellation, not a substituted timeout result."""
    issuer = FactSourceContextRegistry(single_slot_pool)
    inbox = await owner_source(authority_env)
    budget = 0.5
    monkeypatch.setattr(fact_authority, "_CAPTURE_TIMEOUT_SECONDS", budget)
    query_entered = asyncio.Event()
    query_budgets = []
    fetchrow = asyncpg.Connection.fetchrow

    async def observe_fetchrow(connection, query, *args, **options):
        if (
            query
            == "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
            "FROM switchboard.message_inbox WHERE id=$1"
            and args == (inbox,)
        ):
            query_budgets.append(options.get("timeout"))
            query_entered.set()
        return await fetchrow(connection, query, *args, **options)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", observe_fetchrow)
    async with asyncio.timeout(10), AsyncExitStack() as stack:
        if interruption == "acquire-timeout":
            await stack.enter_async_context(single_slot_pool.acquire(timeout=5))
        else:
            locker = await stack.enter_async_context(authority_env.admin.acquire(timeout=5))
            await stack.enter_async_context(locker.transaction())
            # Block the actual unlocked snapshot SELECT in the disposable database.
            await locker.execute(
                "LOCK TABLE switchboard.message_inbox IN ACCESS EXCLUSIVE MODE", timeout=5
            )
        capture = asyncio.create_task(issuer.capture_accepted_report(inbox, authority_env.owner))
        try:
            if interruption == "cancel-query":
                async with asyncio.timeout(2):
                    await query_entered.wait()
                capture.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await capture
            else:
                with pytest.raises(TimeoutError):
                    await capture
        finally:
            capture.cancel()
            await asyncio.gather(capture, return_exceptions=True)
    if interruption == "acquire-timeout":
        assert query_budgets == []
    else:
        assert len(query_budgets) == 1
        assert query_budgets[0] is not None and 0 < query_budgets[0] < budget
    assert inbox not in issuer._reports
    assert inbox not in issuer._source_digests
    async with asyncio.timeout(5):
        assert await single_slot_pool.fetchval("SELECT 1") == 1
        assert (
            await single_slot_pool.fetchval(
                "SELECT request_context->'_fact_source_report' "
                "FROM switchboard.message_inbox WHERE id=$1",
                inbox,
            )
            is None
        )
    # Retain the real registered resolver and driver for the healthy/reuse companion.
    monkeypatch.setattr(fact_authority, "_CAPTURE_TIMEOUT_SECONDS", 5.0)
    async with asyncio.timeout(15):
        report = await issuer.capture_accepted_report(inbox, authority_env.owner)
        assert report.authority == "owner"
        assert issuer._reports[inbox] == report
        assert (
            await single_slot_pool.fetchval(
                "SELECT request_context->'_fact_source_report' "
                "FROM switchboard.message_inbox WHERE id=$1",
                inbox,
            )
            == report.to_record()
        )
        assert await single_slot_pool.fetchval("SELECT current_user") == "butler_switchboard_rw"


async def test_embedded_mcp_lifetimes_preserve_process_signal_ownership():
    """Closing one embedded server must not drain unrelated future MCP streams."""
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    @asynccontextmanager
    async def endpoint():
        mcp = FastMCP("synthetic-embedded-lifecycle")

        @mcp.tool()
        def ping() -> str:
            return "ready"

        app = ButlerDaemon._build_mcp_http_app(mcp, butler_name="relationship")
        async with authority_cases._tcp(app) as url:
            yield url

    async def ping(url):
        async with asyncio.timeout(5), Client(url + "/mcp", timeout=5) as client:
            result = await client.call_tool("ping", {})
            assert result.data == "ready"

    async with endpoint() as first:
        await ping(first)
        # Position the ownership assertion after a real MCP positive. SSE's
        # process-wide watcher must not bind to an embedded server's lifetime.
        assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
        async with endpoint() as nested:
            await ping(nested)
            assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
        await ping(first)
    async with endpoint() as subsequent:
        await ping(subsequent)
        assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
