"""Actual registered local TCP/SQL transport; synthetic origin, no fleet erasure.

Called by the existing migrated Memory species. No new collected node or
private-peer query supplies receiver proof. The source's native origin below is
explicitly planted; actual OwnTracks/input authentication remains separate.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4


@asynccontextmanager
async def _own_tcp(app):
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    server = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="warning", timeout_graceful_shutdown=2)
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Registered listener stopped before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 3)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listener.close()


async def assert_registered_catalog_transport(url, postgres_container, memory_pool, embedding):
    """Real registered consumer→Switchboard→source and online callback controls.

    Domain connections use the existing managed runtime identities. Chronicler's
    configured private chronicler_mem pool retains the adopted role=None bridge,
    exactly like MemoryModule._ensure_memory_schema_pool; no grant extends it to
    a consumer. Same-host TCP proves this topology only, not separate OS hosts,
    real provider source, remote-client disposal or complete all-holder erasure.
    """
    import asyncpg
    import pytest
    from fastmcp import Client, FastMCP
    from fastmcp.client.transports import StreamableHttpTransport

    from butlers.chronicler.location_catalog_copies import _HEADER, _body
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_projection import _digest_value
    from butlers.connectors.mcp_client import CachedMCPClient
    from butlers.daemon import ButlerDaemon
    from butlers.db import register_jsonb_codec
    from butlers.location_retention import content_digest
    from butlers.mcp_wrappers import _SpanWrappingMCP
    from butlers.migrations import run_migrations
    from butlers.modules._roster_switchboard import SwitchboardModule
    from butlers.modules.memory import MemoryModule, MemoryModuleConfig
    from butlers.modules.memory.storage import _upsert_catalog
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url,
    )
    from butlers.tools.switchboard.registry.registry import register_butler

    # Actual chains provide every consumer/core/Memory dependency. Bootstrap is
    # replayed by the existing trusted disposable migration owner, not by the
    # runtime pools; no copied DDL or new role/membership/grant is installed here.
    for name in ("finance", "switchboard"):
        await run_migrations(url, chain="core", schema=name)
        await run_migrations(url, chain="memory", schema=name)
    await run_migrations(url, chain="switchboard", schema="switchboard")
    parsed = urlparse(url)
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
        parsed.username,
    )

    pools, modules = {}, {}
    source_generation, artifact, original_input, parent, output = [uuid4() for _ in range(5)]
    summary = "synthetic registered retentiontransportcatalog"
    try:
        for name in ("chronicler", "finance", "switchboard"):
            role = f"butler_{name}_rw"

            async def setup(conn, *, expected=role):
                await conn.execute(f'SET ROLE "{expected}"')

            pools[name] = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=3,
                init=register_jsonb_codec,
                setup=setup,
                server_settings={"search_path": f"{name},public"},
            )
            assert await pools[name].fetchval("SELECT current_user") == role
            assert await pools[name].fetchval("SELECT current_schema()") == name
            module = MemoryModule()
            own_memory = memory_pool if name == "chronicler" else pools[name]
            # Use this actual already configured pool; it never enrolls a name
            # or caller header as a receiving invocation/source identity.
            module._memory_db = SimpleNamespace(pool=own_memory, close=_leave_pool_open)
            module._get_embedding_engine = lambda: embedding
            config = MemoryModuleConfig(
                memory_schema="chronicler_mem" if name == "chronicler" else None
            )
            db = SimpleNamespace(pool=pools[name], schema=name, owner_butler=name)
            await module.on_startup(config, db)
            modules[name] = module

        async with AsyncExitStack() as stack:
            apps, endpoints = {}, {}
            for name, module in modules.items():
                mcp = FastMCP(name)
                await module.register_tools(
                    _SpanWrappingMCP(mcp, name, module_name="memory"),
                    module._config,
                    module._db,
                    name,
                )
                if name == "switchboard":
                    routing = SwitchboardModule()
                    await routing.register_tools(
                        _SpanWrappingMCP(mcp, name, module_name="switchboard"),
                        None,
                        module._db,
                        name,
                    )
                apps[name] = ButlerDaemon._build_mcp_http_app(
                    mcp,
                    butler_name=name,
                    location_retention_routes=[module.location_retention_route()],
                    location_retention_adapters=[module.location_retention_admission],
                )
                endpoints[name] = await stack.enter_async_context(_own_tcp(apps[name]))
            registry = await stack.enter_async_context(
                Client(StreamableHttpTransport(endpoints["switchboard"] + "/mcp"))
            )
            for name, module in modules.items():
                await register_butler(pools["switchboard"], name, endpoints[name] + "/mcp")
                module.wire_runtime(
                    SimpleNamespace(_pool=pools[name], _config=SimpleNamespace(name=name)),
                    None,
                    switchboard_client=registry,
                )
            source = modules["chronicler"]._location_catalog_runtime
            receiver = modules["finance"]._location_catalog_runtime
            assert source.memory is memory_pool and receiver.domain is pools["finance"]
            # Consumer cannot read the source's private Memory table. Its body
            # must cross the actual owning registered tool, never peer SQL.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pools["finance"].fetch("SELECT * FROM chronicler_mem.facts")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pools["finance"].fetch("SELECT * FROM chronicler.location_native_copy_births")

            # Synthetic native origin and canonical artifact; the actual owning
            # catalog producer freezes its generation/body on the real writer.
            async with memory_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO facts(id,subject,predicate,content) "
                        "VALUES($1,'registered-transport','location',$2)",
                        artifact,
                        summary,
                    )
                    row = await conn.fetchrow("SELECT * FROM facts WHERE id=$1", artifact)
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_copy_births "
                        "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                        "exclusive_input,producer_kind) VALUES($1,'point_event',$2,$3,true,true,'native_memory')",
                        parent,
                        output,
                        b"n" * 32,
                    )
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_dispatch_inputs "
                        "(input_generation,server_request,prompt_digest,parent_count,origin_kind) "
                        "VALUES($1,$2,$3,1,'native_memory')",
                        original_input,
                        uuid4(),
                        b"p" * 32,
                    )
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_dispatch_parents "
                        "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                        original_input,
                        parent,
                        b"n" * 32,
                    )
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_memory_bundles "
                        "(input_generation,bundle_digest,exclusive_input) VALUES($1,$2,true)",
                        original_input,
                        b"b" * 32,
                    )
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_memory_artifacts "
                        "(artifact_generation,input_generation,memory_table,artifact_id,body_digest,content_digest) "
                        "VALUES($1,$2,'facts',$3,$4,$5)",
                        source_generation,
                        original_input,
                        artifact,
                        content_digest({"memory_artifact": _digest_value(dict(row))}),
                        artifact_content_digest("facts", row),
                    )
            await _upsert_catalog(
                memory_pool,
                source_schema="chronicler_mem",
                source_table="facts",
                source_id=artifact,
                source_butler="chronicler",
                tenant_id="shared",
                entity_id=None,
                summary=summary,
                embedding=[0.0] * 384,
                search_text=summary,
                memory_type="fact",
                sensitivity="normal",
            )
            catalog = await memory_pool.fetchrow(
                "SELECT * FROM public.memory_catalog WHERE source_schema='chronicler_mem' "
                "AND source_table='facts' AND source_id=$1",
                artifact,
            )
            assert catalog is not None
            expected_body = _body(catalog)
            async with Client(endpoints["finance"] + "/mcp") as consumer:
                result = await consumer.call_tool(
                    "memory_catalog_search",
                    {"query": "retentiontransportcatalog", "mode": "keyword"},
                )
                rows = CachedMCPClient._parse_result(result, "memory_catalog_search")
                assert len(rows) == 1 and rows[0]["summary"] == summary
                assert {key: rows[0][key] for key in expected_body} == expected_body
            # Distinct acquisitions bind exact same actual loan to both owners;
            # response completion settles only each source-owned server copy.
            loan = await pools["finance"].fetchrow(
                "SELECT * FROM location_catalog_copy_loans WHERE catalog_id=$1", catalog["id"]
            )
            assert loan is not None and loan["receiving_incarnation"] == receiver.incarnation
            assert loan["body_digest"] == content_digest({"catalog_body": expected_body})
            async with pools["chronicler"].acquire() as readback:
                assert await readback.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_catalog_loans "
                    "WHERE loan_id=$1 AND source_generation=$2 AND body_digest=$3 AND receiving_incarnation=$4)",
                    loan["loan_id"],
                    loan["source_generation"],
                    loan["body_digest"],
                    receiver.incarnation,
                )
            async with asyncio.timeout(5):
                while not await pools["finance"].fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_finished WHERE loan_id=$1 AND body_digest=$2)",
                    loan["loan_id"],
                    loan["body_digest"],
                ):
                    await asyncio.sleep(0.01)
            assert await pools["chronicler"].fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_catalog_server_dispositions "
                "WHERE loan_id=$1 AND body_digest=$2)",
                loan["loan_id"],
                loan["body_digest"],
            )
            # A real current loan UUID and a caller-invented header both refuse
            # through the actual source guard/tool; the positive above proves
            # this is not an absent source or an always-failing native helper.
            async with Client(endpoints["chronicler"] + "/mcp") as caller:
                denied = await caller.call_tool(
                    "location_catalog_loan_body",
                    {"loan_id": str(loan["loan_id"])},
                    raise_on_error=False,
                )
                assert denied.is_error
            import httpx

            async with httpx.AsyncClient(
                trust_env=False, timeout=5, follow_redirects=False
            ) as caller:
                denied_header = await caller.post(
                    endpoints["chronicler"] + "/mcp",
                    headers={_HEADER: "x" * 43},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "location_catalog_loan_body",
                            "arguments": {"loan_id": str(loan["loan_id"])},
                        },
                    },
                )
                assert denied_header.status_code == 503
                assert denied_header.json() == {"status": "unavailable"}
            assert (
                await pools["finance"].fetchval(
                    "SELECT count(*) FROM location_catalog_copy_loans WHERE catalog_id=$1",
                    catalog["id"],
                )
                == 1
            )
    finally:
        for module in reversed(list(modules.values())):
            await module.on_shutdown()
        for pool in pools.values():
            await pool.close()


async def _leave_pool_open():
    """The caller's fixture closes its own pool; no module steals that lifetime."""
