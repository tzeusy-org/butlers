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
    """Keep malformed-history refusal separate from the healthy routed cohort.

    The preceding species deliberately retains missing/mismatched/extra/empty
    immutable artifact bundles. The global census must refuse them, even if
    they appear unrelated to a new plan. A healthy positive needs its own
    actual migration-created database, not deletion or repair of those floors.
    """
    import asyncpg
    import pytest

    from butlers.chronicler.location_catalog_copies import require_catalog_artifact_ancestry
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.db import register_jsonb_codec
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
        await require_catalog_artifact_ancestry(memory_pool)
    fresh_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler", "memory"],
        schemas={"core": "chronicler", "chronicler": "chronicler", "memory": "chronicler_mem"},
    )
    fault = SimpleNamespace(fail_receipt=False, reached_receipt=False)

    class ArtifactReceiptFaultConnection(asyncpg.Connection):
        """Actual configured driver connection; no pool/registry impersonation."""

        async def execute(self, sql, *args, **kwargs):
            result = await super().execute(sql, *args, **kwargs)
            if fault.fail_receipt and sql.startswith(
                "INSERT INTO chronicler.location_native_memory_artifact_dispositions "
            ):
                fault.reached_receipt = True
                raise RuntimeError("planted actual artifact receipt rollback")
            return result

    fresh_memory = await asyncpg.create_pool(
        fresh_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        connection_class=ArtifactReceiptFaultConnection,
        server_settings={"search_path": "chronicler_mem,public"},
    )
    try:
        await _assert_registered_catalog_transport(
            fresh_url, postgres_container, fresh_memory, embedding, fault
        )
    finally:
        await fresh_memory.close()


async def _assert_registered_catalog_transport(
    url, postgres_container, memory_pool, embedding, fault
):
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
    from butlers.modules._roster_chronicler import ChroniclerModule
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
    # The adopted core255 replay positions an independent schema version
    # after genuinely applied shared predecessors, without local foundation
    # state. Stamp only the already-applied public predecessor, then execute
    # our successor via the actual migration entrypoint; never stamp past it.
    from alembic import command
    from butlers.migrations import _build_alembic_config, get_chain_head, get_chain_revision_ids

    await run_migrations(url, chain="core", schema="public")
    ordinary = await asyncpg.connect(url)
    try:
        assert await ordinary.fetchval(
            "SELECT version_num FROM public.alembic_version WHERE version_num LIKE 'core_%'"
        ) == get_chain_head("core")
        assert "core_265" in get_chain_revision_ids("core")
        for name in ("health", "general"):
            config = _build_alembic_config(url, ["core"], target_schema=name)
            await asyncio.to_thread(command.stamp, config, "core_265")
            await run_migrations(url, chain="core", schema=name)
            assert await ordinary.fetchval("SELECT to_regclass($1)", name + ".state") is None
            assert (
                await ordinary.fetchval(
                    "SELECT c.relkind='r' AND c.relowner=s.relowner "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_class s ON s.relname='state' "
                    "JOIN pg_namespace p ON p.oid=s.relnamespace AND p.nspname='public' "
                    "WHERE n.nspname=$1 AND c.relname='location_retention_copy_receipts'",
                    name,
                )
                is True
            )
    finally:
        await ordinary.close()
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
                if name == "chronicler":
                    chronicler = ChroniclerModule()
                    await chronicler.register_tools(
                        _SpanWrappingMCP(mcp, name, module_name="chronicler"),
                        None,
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
            from butlers.chronicler.location_memory_copies import _receivers

            assert _receivers[source.domain][0] is memory_pool
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
            # This stored source plan is explicitly synthetic engine input;
            # the owning registered source/consumer protocol and durable role
            # readbacks below are real. It is not actual OwnTracks acceptance,
            # connector disposal, READY or complete all-holder erasure.
            from datetime import UTC, datetime, timedelta

            from butlers.chronicler.location_catalog_copies import reconcile_catalog_loans
            from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest

            decision, run = uuid4(), uuid4()
            cutoff = datetime.now(UTC)
            frozen = FrozenRaw(
                raw_id=uuid4(),
                source_revision=1,
                logical_source_digest=(b"l" * 32).hex(),
                content_digest=(b"c" * 32).hex(),
                retention_at=cutoff - timedelta(days=31),
                accepted_request_id=uuid4(),
                accepted_payload_digest=(b"a" * 32).hex(),
                accepted_normalized_digest=(b"z" * 32).hex(),
            )
            manifest = frozen_manifest(decision, 1, cutoff, [frozen])
            async with pools["chronicler"].acquire() as conn:
                async with conn.transaction():
                    await source.lock_domain(conn)
                    await conn.execute(
                        "INSERT INTO location_retention_runs "
                        "(run_id,policy_version,cutoff,lease_until,status) "
                        "VALUES($1,1,$2,$3,'pending')",
                        run,
                        cutoff,
                        cutoff + timedelta(minutes=5),
                    )
                    await conn.execute(
                        "INSERT INTO location_retention_plans "
                        "(decision_id,run_id,policy_version,cutoff,manifest_digest,state) "
                        "VALUES($1,$2,1,$3,$4,'holder_pending')",
                        decision,
                        run,
                        cutoff,
                        manifest,
                    )
                    await conn.execute(
                        "INSERT INTO location_retention_plan_rows "
                        "(decision_id,raw_id,source_revision,logical_source_digest,content_digest,"
                        "retention_at,accepted_request_id,accepted_payload_digest,"
                        "accepted_normalized_digest) VALUES($1,$2,1,$3,$4,$5,$6,$7,$8)",
                        decision,
                        frozen.raw_id,
                        bytes.fromhex(frozen.logical_source_digest),
                        bytes.fromhex(frozen.content_digest),
                        frozen.retention_at,
                        frozen.accepted_request_id,
                        bytes.fromhex(frozen.accepted_payload_digest),
                        bytes.fromhex(frozen.accepted_normalized_digest),
                    )
                    await conn.execute(
                        "INSERT INTO location_retention_plan_outputs "
                        "(decision_id,raw_id,source_revision,adapter_name,mapping_revision,"
                        "output_kind,output_id) VALUES($1,$2,1,'synthetic_source',$3,'point_event',$4)",
                        decision,
                        frozen.raw_id,
                        b"m" * 32,
                        output,
                    )
            # A locator without a stored source decision cannot mint closure.
            async with Client(endpoints["finance"] + "/mcp") as owning:
                denied_plan = await owning.call_tool(
                    "location_retention_prepare_copy",
                    {"decision_id": str(uuid4())},
                    raise_on_error=False,
                )
                assert denied_plan.is_error
                denied_receipt = await owning.call_tool(
                    "location_retention_copy_status",
                    {"decision_id": str(decision), "receipt_id": str(uuid4())},
                    raise_on_error=False,
                )
                assert denied_receipt.is_error
            from butlers.chronicler.location_catalog_copies import dispose_catalog_artifacts

            independent = uuid4()
            independent_body = "synthetic independently authored ordinary fact"
            await memory_pool.execute(
                "INSERT INTO facts(id,subject,predicate,content) "
                "VALUES($1,'registered-independent','ordinary',$2)",
                independent,
                independent_body,
            )
            # A real consumer loan without its source-owned committed terminal
            # receipt prevents actual source destruction, even after SEND ends.
            await dispose_catalog_artifacts(pools["chronicler"], decision)
            async with memory_pool.acquire() as unclosed_readback:
                assert (
                    await unclosed_readback.fetchval(
                        "SELECT content FROM facts WHERE id=$1", artifact
                    )
                    == summary
                )
            assert not await pools["chronicler"].fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_artifact_dispositions "
                "WHERE artifact_generation=$1)",
                source_generation,
            )

            # Source requests prepare/status through its actual Switchboard
            # registry, then reads its own durable receipt on another acquisition.
            await reconcile_catalog_loans(pools["chronicler"], decision)
            async with pools["finance"].acquire() as consumer_readback:
                disposition = await consumer_readback.fetchrow(
                    "SELECT * FROM location_catalog_copy_dispositions "
                    "WHERE loan_id=$1 AND decision_id=$2",
                    loan["loan_id"],
                    decision,
                )
            assert disposition is not None and disposition["manifest_digest"] == manifest
            async with pools["chronicler"].acquire() as source_readback:
                observed = await source_readback.fetchrow(
                    "SELECT * FROM location_retention_holder_receipts "
                    "WHERE decision_id=$1 AND owning_butler='finance' "
                    "AND holder_kind='catalog_consumer' AND holder_generation=$2",
                    decision,
                    loan["loan_id"],
                )
            assert observed is not None and observed["receipt_id"] == disposition["receipt_id"]
            assert observed["source_digest"] == loan["body_digest"]
            await reconcile_catalog_loans(pools["chronicler"], decision)
            assert (
                await pools["finance"].fetchval(
                    "SELECT count(*) FROM location_catalog_copy_dispositions "
                    "WHERE loan_id=$1 AND decision_id=$2",
                    loan["loan_id"],
                    decision,
                )
                == 1
            )
            assert (
                await pools["chronicler"].fetchval(
                    "SELECT count(*) FROM location_retention_holder_receipts "
                    "WHERE decision_id=$1 AND holder_kind='catalog_consumer' "
                    "AND holder_generation=$2",
                    decision,
                    loan["loan_id"],
                )
                == 1
            )
            # Keep the planted fact intact: terminal response-copy disposal does
            # not delete its source or claim a remote recipient was erased.
            assert (
                await memory_pool.fetchval(
                    "SELECT content FROM facts WHERE id=$1",
                    artifact,
                )
                == summary
            )

            # Fault the ACTUAL source producer after its real public reduction,
            # native DELETE and receipt INSERT, inside that producer's acquired
            # transaction. Separate acquisitions must see original survivors.
            fault.fail_receipt = True
            try:
                with pytest.raises(RuntimeError, match="actual artifact receipt rollback"):
                    await dispose_catalog_artifacts(pools["chronicler"], decision)
            finally:
                fault.fail_receipt = False
            assert fault.reached_receipt is True
            async with memory_pool.acquire() as rollback_readback:
                assert (
                    await rollback_readback.fetchval(
                        "SELECT content FROM facts WHERE id=$1", artifact
                    )
                    == summary
                )
                assert (
                    await rollback_readback.fetchval(
                        "SELECT summary FROM public.memory_catalog WHERE id=$1", catalog["id"]
                    )
                    == expected_body["summary"]
                )
            assert not await pools["chronicler"].fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_memory_artifact_dispositions "
                "WHERE artifact_generation=$1)",
                source_generation,
            )
            await dispose_catalog_artifacts(pools["chronicler"], decision)
            async with memory_pool.acquire() as artifact_readback:
                assert not await artifact_readback.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM facts WHERE id=$1)", artifact
                )
                reduced = await artifact_readback.fetchrow(
                    "SELECT summary,title,predicate,scope,embedding,search_vector "
                    "FROM public.memory_catalog WHERE id=$1",
                    catalog["id"],
                )
                assert reduced is not None and reduced["summary"] == ""
                assert all(
                    reduced[key] is None
                    for key in ("title", "predicate", "scope", "embedding", "search_vector")
                )
                assert (
                    await artifact_readback.fetchval(
                        "SELECT content FROM facts WHERE id=$1", independent
                    )
                    == independent_body
                )
            source_receipt = await pools["chronicler"].fetchval(
                "SELECT receipt_id FROM location_native_memory_artifact_dispositions "
                "WHERE artifact_generation=$1 AND decision_id=$2",
                source_generation,
                decision,
            )
            assert source_receipt is not None
            await dispose_catalog_artifacts(pools["chronicler"], decision)
            assert (
                await pools["chronicler"].fetchval(
                    "SELECT receipt_id FROM location_native_memory_artifact_dispositions "
                    "WHERE artifact_generation=$1 AND decision_id=$2",
                    source_generation,
                    decision,
                )
                == source_receipt
            )
            from butlers.chronicler.location_catalog_copies import catalog_holder_inventory

            async with pools["chronicler"].acquire() as final_cohort:
                async with final_cohort.transaction():
                    await source.lock_domain(final_cohort)
                    holders = await catalog_holder_inventory(final_cohort, decision)
                    assert holders and all(holder["receipt_id"] is not None for holder in holders)
            from butlers.chronicler.location_catalog_copies import catalog_frontier_closed

            async with pools["chronicler"].acquire() as current_body:
                async with current_body.transaction():
                    await source.lock_domain(current_body)
                    assert await catalog_frontier_closed(current_body, decision)
            # An immutable receipt must not bless subsequently reintroduced
            # retained public discovery text. This disposable tamper leaves
            # original receipts/history intact and uses no production bypass.
            await memory_pool.execute(
                "UPDATE public.memory_catalog SET title=$2 WHERE id=$1",
                catalog["id"],
                "synthetic resurrected retained title",
            )
            async with pools["chronicler"].acquire() as changed_body:
                async with changed_body.transaction():
                    await source.lock_domain(changed_body)
                    assert not await catalog_frontier_closed(changed_body, decision)
            await memory_pool.execute(
                "UPDATE public.memory_catalog SET title=NULL WHERE id=$1", catalog["id"]
            )
            async with pools["chronicler"].acquire() as restored_body:
                async with restored_body.transaction():
                    await source.lock_domain(restored_body)
                    assert await catalog_frontier_closed(restored_body, decision)

            # This is the selected native/catalog/server/consumer cohort only;
            # no planted plan becomes actual OwnTracks/raw or full-fleet proof.

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
