"""Native local HTTP→registered ingest→SQL input proof, not fleet erasure.

The phone payload/token are synthetic. Pools, actual connector/ASGI constructor,
Switchboard registered ingest, message-inbox writer and end observers are real.
No planted acceptance UUID or private peer query substitutes for that writer.
This helper extends the existing migrated species; it adds no collected node.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4


def register_native_switchboard_tools(context, mcp):
    """Use the production decorator contract, including explicit MCP tool names."""
    from butlers.core_tools._switchboard import register_switchboard_tools

    def core_tool(group, **tool_kwargs):
        return mcp.tool(**tool_kwargs)

    register_switchboard_tools(context, mcp, core_tool)


async def wait_owntracks_inputs_ended(runtime, pool, point):
    """Wait only for original producer ends; malformed/unknown input still refuses."""
    from butlers.connectors.owntracks_input_copies import require_inputs_ended

    async with asyncio.timeout(10):
        while True:
            await runtime.reconcile_observed_ends()
            try:
                async with pool.acquire() as committed:
                    await require_inputs_ended(
                        committed,
                        point["source_input_generation"],
                        point["logical_source_digest"],
                        point["content_digest"],
                    )
            except ValueError as exc:
                if str(exc) not in {
                    "native input server cohort is still active",
                    "native input source cohort is still active",
                }:
                    raise
            else:
                return
            await asyncio.sleep(0.01)


async def assert_native_owntracks_input_transport(postgres_container):
    """Separate healthy database: never clean up or alter earlier planted histories."""
    import asyncpg

    from butlers.db import register_jsonb_codec
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
    )

    async def setup(conn):
        await conn.execute("SET ROLE connector_writer")

    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        setup=setup,
        server_settings={"search_path": "connectors,public"},
    )
    try:
        await _assert_native_owntracks_input_transport(url, postgres_container, pool)
    finally:
        await pool.close()


async def _assert_native_owntracks_input_transport(url, postgres_container, connector_pool):
    import asyncpg
    import httpx
    from fastmcp import FastMCP

    from butlers.config import BufferConfig
    from butlers.connectors.owntracks import OwnTracksConnector, OwnTracksConnectorConfig
    from butlers.connectors.owntracks_input_copies import require_inputs_ended
    from butlers.core.buffer import DurableBuffer
    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import SwitchboardInputCopies, require_ingress_closed
    from butlers.core_tools._base import ToolContext
    from butlers.daemon import ButlerDaemon
    from butlers.db import register_jsonb_codec
    from butlers.location_retention import content_digest
    from butlers.migrations import run_migrations
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url,
    )
    from tests.chronicler.retention_transport_helpers import _own_tcp

    # Actual independent owning schema/chain, using existing installed roles.
    # No hand-copied schema, new role, cross-schema runtime grant or peer SQL.
    await run_migrations(url, chain="core", schema="switchboard")
    await run_migrations(url, chain="switchboard", schema="switchboard")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    # Faithful existing bootstrap after the new real namespace exists, using
    # the established disposable migration owner, never a runtime Pool grant.
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
        parsed.username,
    )

    async def setup(conn):
        await conn.execute("SET ROLE butler_switchboard_rw")

    switchboard = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        setup=setup,
        server_settings={"search_path": "switchboard,public"},
    )
    assert await switchboard.fetchval("SELECT current_user") == "butler_switchboard_rw"
    assert await connector_pool.fetchval("SELECT current_user") == "connector_writer"
    connector = None
    receiving = SwitchboardInputCopies(switchboard)
    entered, release = asyncio.Event(), asyncio.Event()
    actual_refs = []
    runtime_probe = [None]

    async def owning_process(ref):
        # This is the actual configured buffer consumer lifetime; it proves
        # queue/processing SQL only, not routing or target-model completion.
        actual_refs.append(ref)
        assert ref.message_text and ref.source["provider"] == "owntracks"
        entered.set()
        await release.wait()
        if runtime_probe[0] is not None:
            await runtime_probe[0](ref)

    buffer = DurableBuffer(
        BufferConfig(worker_count=1, scanner_interval_s=3600, scanner_grace_s=0),
        switchboard,
        owning_process,
    )
    await buffer.start()
    try:
        mcp = FastMCP("switchboard")
        context = ToolContext(
            daemon=SimpleNamespace(_pipeline=object(), _buffer=buffer),
            pool=switchboard,
            spawner=None,
            butler_name="switchboard",
            butler_type=None,
            is_switchboard=True,
            is_messenger=False,
            route_metrics=None,
        )
        # Invoke the real owning registration, preserving its envelope parser,
        # global policy, dedup/advisory locks and canonical accepted-row writer.
        register_native_switchboard_tools(context, mcp)
        app = ButlerDaemon._build_mcp_http_app(
            mcp, butler_name="switchboard", location_ingress_runtime=receiving
        )
        async with _own_tcp(app) as switchboard_url:
            endpoint = "owntracks:native-http-" + str(uuid4())
            token = "synthetic-local-input-test"
            connector = OwnTracksConnector(
                OwnTracksConnectorConfig(
                    switchboard_mcp_url=switchboard_url + "/mcp",
                    tracker_id_override=endpoint.removeprefix("owntracks:"),
                    ingestion_tier="full",
                ),
                token,
                db_pool=connector_pool,
            )
            connector._main_loop = asyncio.get_running_loop()
            runtime = connector._input_copies
            assert runtime is not None and runtime.pool is connector_pool
            actual_replay_headers = []
            native_replay_reader = connector._drain_native_replay

            async def witnessed_native_replay(endpoint_identity):
                original_headers = set(runtime._servers)
                await native_replay_reader(endpoint_identity)
                # The actual reader has returned, but its original processing
                # Task has not ended yet. Observe its real constructor binding
                # before the Task-end callback can settle/remove that header.
                born = [
                    binding
                    for key, binding in runtime._servers.items()
                    if key not in original_headers
                ]
                assert len(born) == 1
                actual_replay_headers.extend(born)

            connector._drain_native_replay = witnessed_native_replay
            webhook_header_count = (
                "SELECT count(DISTINCT h.copy_generation) "
                "FROM connectors.owntracks_input_server_births h "
                "JOIN connectors.owntracks_input_copy_births b "
                "ON b.copy_generation=h.copy_generation AND b.server_generation=h.copy_generation "
                "WHERE b.copy_kind=1 AND b.incarnation=h.incarnation AND h.incarnation=$1"
            )
            before = await connector_pool.fetchval(webhook_header_count, runtime.incarnation)
            from datetime import UTC, datetime

            raw = {
                "_type": "location",
                "tid": "R7",
                "tst": int(datetime.now(UTC).timestamp()),
                "lat": 1.25,
                "lon": 103.75,
            }
            async with _own_tcp(connector._build_app()) as webhook_url:
                async with httpx.AsyncClient(timeout=10) as phone:
                    rejected = await phone.post(webhook_url + "/owntracks/webhook", json=raw)
                    assert rejected.status_code == 401
                    assert (
                        await connector_pool.fetchval(
                            "SELECT count(*) FROM connectors.owntracks_input_server_births"
                        )
                        == before
                    )
                    accepted = await phone.post(
                        webhook_url + "/owntracks/webhook",
                        json=raw,
                        headers={"Authorization": "Bearer " + token},
                    )
                    assert accepted.status_code == 200 and accepted.json() == []

                # Poll only this actual constructor's full immutable cohort.
                # Receipt/body existence, not elapsed time, establishes the end.
                async with asyncio.timeout(10):
                    while True:
                        await runtime.reconcile_observed_ends()
                        point = await connector_pool.fetchrow(
                            "SELECT * FROM connectors.owntracks_points WHERE endpoint_identity=$1",
                            endpoint,
                        )
                        if point is not None:
                            try:
                                async with connector_pool.acquire() as committed:
                                    await require_inputs_ended(
                                        committed,
                                        point["source_input_generation"],
                                        point["logical_source_digest"],
                                        point["content_digest"],
                                    )
                            except ValueError:
                                pass  # Actual birth/end readback must still settle.
                            else:
                                break
                        await asyncio.sleep(0.01)
                assert point["raw_payload"] == raw
                assert point["content_digest"] == content_digest(raw)
                assert point["accepted_request_id"] is not None
                # This observer is the Switchboard OWN configured role, not
                # connector or Chronicler querying a peer's private schema.
                async with switchboard.acquire() as committed:
                    inbox = await committed.fetchrow(
                        "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
                        "FROM switchboard.message_inbox WHERE id=$1",
                        point["accepted_request_id"],
                    )
                assert inbox is not None and inbox["lifecycle_state"] == "accepted"
                assert inbox["raw_payload"]["source"]["provider"] == "owntracks"
                assert inbox["raw_payload"]["source"]["endpoint_identity"] == endpoint
                assert inbox["raw_payload"]["payload"]["raw"] == raw
                assert (
                    content_digest({"raw": inbox["raw_payload"]["payload"]["raw"]})
                    == point["accepted_payload_digest"]
                )
                assert (
                    content_digest({"text": inbox["normalized_text"]})
                    == point["accepted_normalized_digest"]
                )
                assert str(inbox["request_context"]["request_id"]) == str(
                    point["accepted_request_id"]
                )
                # The real SDK birth cannot proxy either the actual queued
                # object or the separate processing Task. Neither receipt may
                # appear while the configured owning consumer still holds it.
                await asyncio.wait_for(entered.wait(), timeout=10)
                assert actual_refs[-1].message_text
                async with switchboard.acquire() as committed:
                    pending_queue = await committed.fetchrow(
                        "SELECT b.copy_generation,c.handler_generation,e.copy_generation AS ended "
                        "FROM location_ingress_input_births b "
                        "JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                        "LEFT JOIN location_ingress_input_claims c USING(copy_generation) "
                        "LEFT JOIN location_ingress_input_ends e USING(copy_generation) "
                        "WHERE a.request_id=$1 AND b.copy_kind=2",
                        point["accepted_request_id"],
                    )
                    assert pending_queue is not None and pending_queue["ended"] is None
                    assert pending_queue["handler_generation"] is not None
                    key = await committed.fetchval(
                        "SELECT request_context->>'dedupe_key' FROM message_inbox WHERE id=$1",
                        point["accepted_request_id"],
                    )
                    import pytest

                    with pytest.raises(CopyFloorUnavailable):
                        await require_ingress_closed(
                            committed, point["accepted_request_id"], key, inbox
                        )
                release.set()
                # Connector completion cannot proxy its actual recipient.
                # SDK handler Tasks and HTTP Tasks must independently end.
                async with asyncio.timeout(10):
                    while True:
                        await receiving.reconcile_observed_ends()
                        current = await switchboard.fetchrow(
                            "SELECT a.stored_digest,b.envelope_digest,"
                            "e.copy_generation IS NOT NULL AS handler_ended,"
                            "s.server_generation IS NOT NULL AS server_ended "
                            "FROM location_ingress_accepted_inputs a "
                            "JOIN location_ingress_input_births b USING(copy_generation) "
                            "LEFT JOIN location_ingress_input_ends e USING(copy_generation) "
                            "LEFT JOIN location_ingress_server_ends s USING(server_generation) "
                            "WHERE a.request_id=$1",
                            point["accepted_request_id"],
                        )
                        if current and current["handler_ended"] and current["server_ended"]:
                            break
                        await asyncio.sleep(0.01)
                async with asyncio.timeout(10):
                    while True:
                        await receiving.reconcile_observed_ends()
                        copies = await switchboard.fetch(
                            "SELECT b.copy_kind,e.copy_generation IS NOT NULL AS ended "
                            "FROM location_ingress_input_births b "
                            "JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                            "LEFT JOIN location_ingress_input_ends e USING(copy_generation) "
                            "WHERE a.request_id=$1 ORDER BY b.copy_kind",
                            point["accepted_request_id"],
                        )
                        if len(copies) == 3 and all(row["ended"] for row in copies):
                            break
                        await asyncio.sleep(0.01)
                assert [row["copy_kind"] for row in copies] == [1, 2, 3]
                assert actual_refs[-1].message_text == "" and actual_refs[-1].source == {}
                assert actual_refs[-1]._native_ingress is None
                assert not buffer._worker_tasks[0].done()
                async with switchboard.acquire() as committed:
                    await require_ingress_closed(
                        committed, point["accepted_request_id"], key, inbox
                    )
                assert current["stored_digest"] == content_digest(
                    {
                        "raw_payload": inbox["raw_payload"],
                        "normalized_text": inbox["normalized_text"],
                    }
                )
                assert dict(
                    await switchboard.fetchrow(
                        "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
                        "FROM message_inbox WHERE id=$1",
                        point["accepted_request_id"],
                    )
                ) == dict(inbox)
                assert (
                    await connector_pool.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_input_copy_births "
                        "WHERE logical_source_digest=$1",
                        point["logical_source_digest"],
                    )
                    == 2
                )
                # Invoke the actual configured scanner, rather than forge a
                # recovery parent or manually mark a receiving copy closed.
                entered.clear()
                release.clear()
                assert await buffer._run_scanner_sweep() == 1
                await asyncio.wait_for(entered.wait(), timeout=10)
                cold_ref = actual_refs[-1]
                assert cold_ref.message_text == inbox["normalized_text"]
                assert cold_ref._native_ingress is not None
                async with switchboard.acquire() as committed:
                    cold_parent = await committed.fetchrow(
                        "SELECT p.parent_generation,s.copy_generation AS first_generation,"
                        "a.stored_digest,e.copy_generation AS ended "
                        "FROM location_ingress_input_parents p "
                        "JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                        "JOIN location_ingress_inbox_sources s ON s.request_id=a.request_id "
                        "LEFT JOIN location_ingress_input_ends e ON e.copy_generation=p.copy_generation "
                        "WHERE p.copy_generation=$1",
                        cold_ref._native_ingress[1].generation,
                    )
                    assert cold_parent is not None and cold_parent["ended"] is None
                    assert cold_parent["parent_generation"] == cold_parent["first_generation"]
                    assert cold_parent["stored_digest"] == current["stored_digest"]
                    with pytest.raises(CopyFloorUnavailable):
                        await require_ingress_closed(
                            committed, point["accepted_request_id"], key, inbox
                        )
                release.set()
                async with asyncio.timeout(10):
                    while True:
                        await receiving.reconcile_observed_ends()
                        cold_copies = await switchboard.fetch(
                            "SELECT b.copy_kind,e.copy_generation IS NOT NULL AS ended "
                            "FROM location_ingress_input_births b "
                            "JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                            "LEFT JOIN location_ingress_input_ends e USING(copy_generation) "
                            "WHERE a.request_id=$1 ORDER BY b.copy_kind",
                            point["accepted_request_id"],
                        )
                        if len(cold_copies) == 5 and all(row["ended"] for row in cold_copies):
                            break
                        await asyncio.sleep(0.01)
                assert [row["copy_kind"] for row in cold_copies] == [1, 2, 2, 3, 3]
                assert cold_ref.message_text == "" and cold_ref._native_ingress is None
                async with switchboard.acquire() as committed:
                    await require_ingress_closed(
                        committed, point["accepted_request_id"], key, inbox
                    )
                await _assert_native_first_source_atomicity(switchboard_url, switchboard)
                # The real successful webhook also invokes an independent
                # native replay reader, even when its source query is empty.
                # Its header is not the original webhook's kind-1 birth.
                async with connector_pool.acquire() as committed:
                    webhook = await committed.fetchrow(
                        "SELECT h.copy_generation,h.incarnation,b.logical_source_digest,b.raw_digest,"
                        "e.copy_generation AS ended FROM connectors.owntracks_input_server_births h "
                        "JOIN connectors.owntracks_input_copy_births b "
                        "ON b.copy_generation=h.copy_generation AND b.server_generation=h.copy_generation "
                        "JOIN connectors.owntracks_input_copy_births processing "
                        "ON processing.copy_bundle=b.copy_bundle AND processing.copy_kind=2 "
                        "LEFT JOIN connectors.owntracks_input_server_ends e "
                        "ON e.copy_generation=h.copy_generation "
                        "WHERE processing.copy_generation=$1 AND b.copy_kind=1",
                        point["source_input_generation"],
                    )
                    assert webhook is not None and webhook["ended"] == webhook["copy_generation"]
                    assert webhook["incarnation"] == runtime.incarnation
                    assert webhook["logical_source_digest"] == point["logical_source_digest"]
                    assert webhook["raw_digest"] == point["content_digest"]
                    assert len(actual_replay_headers) == 1
                    replay_headers = await committed.fetch(
                        "SELECT h.copy_generation,e.copy_generation AS ended "
                        "FROM connectors.owntracks_input_server_births h "
                        "LEFT JOIN connectors.owntracks_input_server_ends e USING(copy_generation) "
                        "WHERE h.incarnation=$1 AND h.copy_generation=$2",
                        runtime.incarnation,
                        actual_replay_headers[0].generation,
                    )
                    assert len(replay_headers) == 1
                    assert replay_headers[0]["copy_generation"] != webhook["copy_generation"]
                    assert replay_headers[0]["ended"] == replay_headers[0]["copy_generation"]
                    before_shutdown = await committed.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_input_server_births"
                    )
                runtime.close_admission()
                async with httpx.AsyncClient(timeout=10) as phone:
                    held = await phone.post(
                        webhook_url + "/owntracks/webhook",
                        json={**raw, "tst": raw["tst"] + 1},
                        headers={"Authorization": "Bearer " + token},
                    )
                    assert held.status_code == 503
                assert (
                    await connector_pool.fetchval(webhook_header_count, runtime.incarnation)
                    == before + 1
                )
                assert (
                    await connector_pool.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_input_server_births"
                    )
                    == before_shutdown
                )
                assert dict(
                    await connector_pool.fetchrow(
                        "SELECT * FROM connectors.owntracks_points WHERE id=$1", point["id"]
                    )
                ) == dict(point)
                connector._drain_native_replay = native_replay_reader

                # A second actual configured constructor exercises the adopted
                # SSE client route. Ending its POST/202 cannot settle the SDK
                # queue/handler copy; only that original claim/Task may do so.
                await connector._shutdown()
                await connector._mcp_client.aclose()
                sse_endpoint = "owntracks:native-sse-" + str(uuid4())
                connector = OwnTracksConnector(
                    OwnTracksConnectorConfig(
                        switchboard_mcp_url=switchboard_url + "/sse",
                        tracker_id_override=sse_endpoint.removeprefix("owntracks:"),
                        ingestion_tier="full",
                    ),
                    token,
                    db_pool=connector_pool,
                )
                connector._main_loop = asyncio.get_running_loop()
                sse_runtime = connector._input_copies
                sse_raw = raw | {"tst": raw["tst"] + 2}
                async with _own_tcp(connector._build_app()) as sse_webhook:
                    async with httpx.AsyncClient(timeout=10) as phone:
                        result = await phone.post(
                            sse_webhook + "/owntracks/webhook",
                            json=sse_raw,
                            headers={"Authorization": "Bearer " + token},
                        )
                    assert result.status_code == 200 and result.json() == []
                    async with asyncio.timeout(10):
                        while True:
                            await sse_runtime.reconcile_observed_ends()
                            await receiving.reconcile_observed_ends()
                            sse_point = await connector_pool.fetchrow(
                                "SELECT * FROM connectors.owntracks_points WHERE endpoint_identity=$1",
                                sse_endpoint,
                            )
                            if sse_point is not None:
                                sse_input = await switchboard.fetchrow(
                                    "SELECT a.stored_digest,c.handler_generation,c.incarnation,"
                                    "e.copy_generation IS NOT NULL AS handler_ended,"
                                    "s.server_generation IS NOT NULL AS server_ended "
                                    "FROM location_ingress_accepted_inputs a "
                                    "JOIN location_ingress_input_births b USING(copy_generation) "
                                    "JOIN location_ingress_input_claims c USING(copy_generation) "
                                    "LEFT JOIN location_ingress_input_ends e USING(copy_generation) "
                                    "LEFT JOIN location_ingress_server_ends s USING(server_generation) "
                                    "WHERE a.request_id=$1",
                                    sse_point["accepted_request_id"],
                                )
                                if (
                                    sse_input
                                    and sse_input["handler_ended"]
                                    and sse_input["server_ended"]
                                ):
                                    break
                            await asyncio.sleep(0.01)
                    assert sse_point["raw_payload"] == sse_raw
                    assert sse_point["content_digest"] == content_digest(sse_raw)
                    assert sse_input["handler_generation"] is not None
                    assert sse_input["incarnation"] == receiving.incarnation
                    sse_inbox = await switchboard.fetchrow(
                        "SELECT raw_payload,normalized_text FROM message_inbox WHERE id=$1",
                        sse_point["accepted_request_id"],
                    )
                    assert sse_inbox["raw_payload"]["payload"]["raw"] == sse_raw
                    assert sse_input["stored_digest"] == content_digest(dict(sse_inbox))
                # The actual connector server/processing/replay Tasks have
                # independent lifetimes from the Switchboard SDK handler.
                # Teardown cannot grant an end: reconcile the original source
                # capabilities and require their separate committed readback.
                await wait_owntracks_inputs_ended(sse_runtime, connector_pool, sse_point)
                async with connector_pool.acquire() as committed:
                    await require_inputs_ended(
                        committed,
                        sse_point["source_input_generation"],
                        sse_point["logical_source_digest"],
                        sse_point["content_digest"],
                    )
                await _assert_native_no_dispatch_disposal(switchboard, point, "skip")
                await _assert_native_no_dispatch_disposal(switchboard, sse_point, "metadata_only")
                await _assert_native_ingress_runtime_reservation(
                    switchboard_url, switchboard, runtime_probe
                )
    finally:
        if connector is not None:
            await connector._shutdown()
            await connector._mcp_client.aclose()
        release.set()
        await buffer.stop()
        await asyncio.sleep(0)
        await receiving.stop()
        await switchboard.close()


async def _assert_native_first_source_atomicity(endpoint, pool):
    """Fault the real same-connection producer; separate readback proves rollback."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    import asyncpg
    import pytest
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from butlers.connectors.owntracks import build_location_envelope
    from butlers.core.location_ingress_copies import capture_canonical_ingress_source
    from butlers.location_retention import content_digest, logical_digest
    from butlers.tools.switchboard.ingestion.ingest import _compute_dedupe_key
    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    raw = {
        "_type": "location",
        "tid": "AF",
        "tst": int(datetime.now(UTC).timestamp()),
        "lat": 1.5,
        "lon": 103.5,
    }
    envelope = build_location_envelope(
        raw, "owntracks:atomic-" + str(uuid4()), datetime.now(UTC).isoformat(), "full"
    )
    key = _compute_dedupe_key(parse_ingest_envelope(envelope))
    reached = []

    async def fail_after_receipt(actual_pool, conn, request_id, context, payload, text):
        assert actual_pool is pool and isinstance(conn, asyncpg.pool.PoolConnectionProxy)
        assert conn.is_in_transaction()
        await capture_canonical_ingress_source(
            actual_pool, conn, request_id, context, payload, text
        )
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM message_inbox WHERE id=$1)", request_id
        )
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_ingress_inbox_sources WHERE request_id=$1)",
            request_id,
        )
        reached.append(True)
        raise asyncpg.RaiseError("Synthetic first-source receipt fault")

    async with Client(endpoint + "/mcp") as caller:
        with patch(
            "butlers.core.location_ingress_copies.capture_canonical_ingress_source",
            new=fail_after_receipt,
        ):
            with pytest.raises(ToolError):
                await caller.call_tool("ingest", envelope)
        assert reached == [True]
        # Different acquisition after the actual transaction has unwound.
        async with pool.acquire() as committed:
            assert not await committed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM message_inbox "
                "WHERE request_context->>'dedupe_key'=$1)",
                key,
            )
            assert not await committed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_ingress_inbox_sources "
                "WHERE dedupe_digest=$1)",
                logical_digest(key),
            )
        restored = await caller.call_tool("ingest", envelope)
        accepted = restored.data
        assert accepted["status"] == "accepted" and not accepted["duplicate"]
        from uuid import UUID

        actual_id = UUID(str(accepted["request_id"]))
        async with pool.acquire() as committed:
            stored = await committed.fetchrow(
                "SELECT raw_payload,normalized_text FROM message_inbox WHERE id=$1", actual_id
            )
            source = await committed.fetchrow(
                "SELECT s.dedupe_digest,s.stored_digest,b.copy_kind "
                "FROM location_ingress_inbox_sources s "
                "JOIN location_ingress_input_births b USING(copy_generation) WHERE s.request_id=$1",
                actual_id,
            )
        assert source is not None and source["copy_kind"] == 1
        assert source["dedupe_digest"] == logical_digest(key)
        assert source["stored_digest"] == content_digest(dict(stored))


async def _assert_native_no_dispatch_disposal(pool, point, terminal):
    """Real configured owning reducer/roles; fixed plan and terminal are planted.

    This is not registered online Chronicler plan/expiry or actual classifier
    proof. The accepted canonical body and complete input lifetime were produced
    by actual TCP/SDK ingestion above; no lineage/end/role is invented here.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    import pytest

    from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest
    from butlers.core import location_copy_retention as copies
    from butlers.core.location_ingress_copies import require_ingress_closed
    from butlers.location_retention import content_digest
    from butlers.modules.pipeline import MessagePipeline

    request = point["accepted_request_id"]
    async with asyncio.timeout(10):
        while True:
            async with pool.acquire() as conn:
                original = await conn.fetchrow("SELECT * FROM message_inbox WHERE id=$1", request)
                key = original["request_context"]["dedupe_key"]
                try:
                    await require_ingress_closed(conn, request, key, original)
                except copies.CopyFloorUnavailable:
                    pass
                else:
                    break
            await asyncio.sleep(0.01)
    assert original["raw_payload"]["payload"]["raw"] == point["raw_payload"]
    frozen = FrozenRaw(
        **{key: point[key] for key in ("source_revision", "retention_at", "accepted_request_id")},
        raw_id=point["id"],
        **{key: point[key].hex() for key in ("logical_source_digest", "content_digest")},
        accepted_payload_digest=content_digest(
            {"raw": original["raw_payload"]["payload"]["raw"]}
        ).hex(),
        accepted_normalized_digest=content_digest({"text": original["normalized_text"]}).hex(),
    )
    decision, cutoff = uuid4(), datetime.now(UTC)
    manifest = frozen_manifest(decision, 1, cutoff, (frozen,))
    plan = {
        "decision_id": str(decision),
        "policy_version": 1,
        "cutoff": cutoff.isoformat(),
        "rows": [frozen.model_dump(mode="json")],
        "manifest_digest": manifest.hex(),
        "state": "holder_pending",
    }
    actual_pipeline = MessagePipeline(pool, AsyncMock(side_effect=AssertionError("no runtime")))
    lifecycle = "skipped" if terminal == "skip" else terminal
    # Invoke the actual canonical lifecycle writer used by the fixed native
    # policy branch. Branch selection is explicitly planted, not a claimed
    # global-policy evaluation or receipt proxy for classifier termination.
    await actual_pipeline._update_message_inbox_lifecycle(
        message_inbox_id=request,
        decomposition_output={
            "request_id": str(request),
            "policy_bypass": True,
            "triage_decision": terminal,
        },
        dispatch_outcomes={"request_id": str(request)},
        response_summary="Policy bypass: " + terminal,
        lifecycle_state=lifecycle,
        classified_at=cutoff,
        classification_duration_ms=0.0,
        final_state_at=cutoff,
    )
    terminal_row = dict(await pool.fetchrow("SELECT * FROM message_inbox WHERE id=$1", request))
    assert terminal_row["lifecycle_state"] == lifecycle
    assert terminal_row["final_state_at"] is not None
    # Existing session provenance uses TEXT; the owning inbox/source uses
    # UUID. Preserve that original identity and convert only this text bind.
    from butlers.core.sessions import session_create

    independent_request = str(uuid4())
    independent_session = await session_create(
        pool,
        "Synthetic independent ordinary session",
        "trigger",
        request_id=independent_request,
    )
    assert await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM sessions WHERE request_id=$1)", independent_request
    )
    assert (
        await pool.fetchval("SELECT request_id FROM sessions WHERE id=$1", independent_session)
        == independent_request
    )
    assert not await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM sessions WHERE request_id=$1)", str(request)
    )
    with patch.object(copies, "_registered_plan", AsyncMock(return_value=plan)):
        # A contradictory canonical decision cannot qualify either terminal.
        await pool.execute(
            "UPDATE message_inbox SET decomposition_output=jsonb_set(decomposition_output,'{triage_decision}',to_jsonb($2::text)) WHERE id=$1",
            request,
            "skip" if terminal == "metadata_only" else "metadata_only",
        )
        with pytest.raises(copies.CopyFloorUnavailable, match="source_copy_cohort_pending"):
            await copies.forget_skipped_source(pool, decision)
        async with pool.acquire() as observed:
            assert (
                await observed.fetchval(
                    "SELECT raw_payload FROM message_inbox WHERE id=$1", request
                )
                == terminal_row["raw_payload"]
            )
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_copy_receipts WHERE decision_id=$1)",
                decision,
            )
        await pool.execute(
            "UPDATE message_inbox SET decomposition_output=$2::jsonb WHERE id=$1",
            request,
            terminal_row["decomposition_output"],
        )
        # Call the actual reducer with the actual acquired writer; fault
        # AFTER its original UPDATE, not a handwritten rollback proxy.
        from contextlib import asynccontextmanager

        import asyncpg

        reached = []

        class FaultConnection:
            def __init__(self, actual):
                self.actual = actual

            def __getattr__(self, name):
                return getattr(self.actual, name)

            async def execute(self, query, *args):
                result = await self.actual.execute(query, *args)
                if "UPDATE switchboard.message_inbox" in query:
                    assert self.actual.is_in_transaction()
                    assert await self.actual.fetchval(
                        "SELECT raw_payload FROM message_inbox WHERE id=$1", request
                    ) == {"retention": "forgotten"}
                    assert await self.actual.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_retention_copy_receipts WHERE decision_id=$1)",
                        decision,
                    )
                    reached.append(True)
                    raise asyncpg.RaiseError("Synthetic own source reduction fault")
                return result

        class FaultPool:
            @asynccontextmanager
            async def acquire(self):
                async with pool.acquire() as actual:
                    assert isinstance(actual, asyncpg.pool.PoolConnectionProxy)
                    yield FaultConnection(actual)

        with pytest.raises(asyncpg.RaiseError, match="Synthetic own source reduction fault"):
            await copies.forget_skipped_source(FaultPool(), decision)
        assert reached == [True]
        async with pool.acquire() as observed:
            assert (
                await observed.fetchval(
                    "SELECT raw_payload FROM message_inbox WHERE id=$1", request
                )
                == terminal_row["raw_payload"]
            )
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_copy_receipts WHERE decision_id=$1)",
                decision,
            )
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_source_floors WHERE request_id=$1)",
                request,
            )
        receipt = await copies.forget_skipped_source(pool, decision)
        assert receipt["manifest_digest"] == manifest.hex() and receipt["forgotten_count"] == 1
        assert receipt["source_kind"] == "switchboard_skipped"
        async with pool.acquire() as observed:
            reduced = await observed.fetchrow(
                "SELECT raw_payload,normalized_text,lifecycle_state FROM message_inbox WHERE id=$1",
                request,
            )
            assert reduced["raw_payload"] == {"retention": "forgotten"}
            assert reduced["normalized_text"] == "[OwnTracks exact evidence forgotten]"
            assert reduced["lifecycle_state"] == lifecycle
            assert (
                await observed.fetchval(
                    "SELECT logical_source_digest FROM location_retention_source_floors WHERE request_id=$1",
                    request,
                )
                == point["logical_source_digest"]
            )
            assert (
                await observed.fetchval(
                    "SELECT receipt_id FROM location_retention_copy_receipts WHERE decision_id=$1",
                    decision,
                )
                == receipt["receipt_id"]
            )
        assert await copies.forget_skipped_source(pool, decision) == receipt


async def _assert_native_ingress_runtime_reservation(endpoint, pool, runtime_probe):
    """Actual TCP/processing and core-only constructor; classifier body is synthetic.

    No provider/model invocation, configured Memory or runtime disposal is
    credited. The copied prompt relation remains a distinct held descendant.
    """
    import hashlib
    from datetime import UTC, datetime
    from pathlib import Path
    from unittest.mock import patch
    from uuid import UUID

    import asyncpg
    import pytest
    from fastmcp import Client

    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_input_binding import _dispatchers, register_dispatch_runtime
    from butlers.chronicler.location_memory_context import (
        begin_runtime_context,
        capture_context_prompt,
        end_runtime_context,
    )
    from butlers.config import ButlerConfig
    from butlers.connectors.owntracks import build_location_envelope
    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import _processing_scope, require_ingress_closed
    from butlers.core.location_ingress_runtime import (
        capture_structured_ingress_output,
        reserve_ingress_runtime,
        reserve_structured_ingress_input,
    )
    from butlers.core.sessions import session_complete, session_create
    from butlers.core.spawner import Spawner, SpawnerResult
    from butlers.location_retention import content_digest

    runtime = await NativeDelegationRuntime.create(
        domain=pool, name="switchboard", schema="switchboard", registry=None
    )
    spawner = Spawner(
        ButlerConfig(name="switchboard", port=41101, modules={}),
        Path(__file__).parents[2] / "roster/switchboard",
        pool=pool,
        runtime=object(),  # No external model/runtime is invoked by this SQL species.
    )
    register_dispatch_runtime(spawner, SpawnerResult)
    done = asyncio.Event()
    errors = []
    reached = []
    completed = []
    prompt, system = "Synthetic full classifier input", "Synthetic independent classifier system"

    async def process(ref):
        try:
            request_id = UUID(str(ref.request_id))
            captured = _processing_scope.get()
            assert captured is not None and captured[0].pool is pool
            child = captured[1]
            assert child.kind == 3 and child.task is asyncio.current_task()

            async def fault(conn, owned, context, actual_prompt):
                assert isinstance(conn, asyncpg.pool.PoolConnectionProxy)
                assert conn.is_in_transaction() and owned == captured
                await reserve_ingress_runtime(conn, owned, context, actual_prompt)
                assert await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_ingress_runtime_inputs "
                    "WHERE input_generation=$1 AND copy_generation=$2)",
                    context.generation,
                    child.generation,
                )
                reached.append(context.generation)
                raise asyncpg.RaiseError("Synthetic ingress runtime reservation fault")

            with patch("butlers.core.location_ingress_runtime.reserve_ingress_runtime", new=fault):
                with pytest.raises(
                    asyncpg.RaiseError, match="Synthetic ingress runtime reservation fault"
                ):
                    await begin_runtime_context(pool, spawner, prompt=prompt)
            assert len(reached) == 1
            async with pool.acquire() as observed:
                assert not await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_context_intents "
                    "WHERE input_generation=$1)",
                    reached[0],
                )
                assert not await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_ingress_runtime_inputs "
                    "WHERE input_generation=$1)",
                    reached[0],
                )

            handle = await begin_runtime_context(pool, spawner, prompt=prompt)
            binding = handle[0]
            try:
                assert binding.ingress_input == captured
                assert binding.generated_prompt and not binding.known_context
                capture_context_prompt(None, system)
                session = await session_create(
                    pool,
                    prompt,
                    "classification",
                    request_id=request_id,
                    ingestion_event_id=request_id,
                    effective_system_prompt=system,
                    prompt_digest=hashlib.sha256(system.encode()).hexdigest(),
                    prompt_provenance=[],
                )
                assert session == binding.session
                await session_complete(pool, session, "Synthetic classifier result", [], 1, True)
            finally:
                await end_runtime_context(handle)
            # Independent structured adapter inputs share the actual original
            # processing source, but never invent a session or SDK end witness.
            structured_faults = []
            original_execute = asyncpg.pool.PoolConnectionProxy.execute

            async def structured_fault(conn, sql, *args, **kwargs):
                result = await original_execute(conn, sql, *args, **kwargs)
                if sql.startswith("INSERT INTO location_ingress_structured_inputs "):
                    assert conn.is_in_transaction()
                    assert await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_inputs "
                        "WHERE input_generation=$1 AND copy_generation=$2)",
                        args[0],
                        child.generation,
                    )
                    structured_faults.append(args[0])
                    raise asyncpg.RaiseError("Synthetic structured input reservation fault")
                return result

            tools = [{"name": "synthetic fixed tool", "input_schema": {"type": "object"}}]
            with patch.object(asyncpg.pool.PoolConnectionProxy, "execute", new=structured_fault):
                with pytest.raises(
                    asyncpg.RaiseError, match="Synthetic structured input reservation fault"
                ):
                    await reserve_structured_ingress_input(
                        pool, prompt=prompt, system_prompt=system, tools=tools
                    )
            assert len(structured_faults) == 1
            async with pool.acquire() as observed:
                assert not await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_inputs "
                    "WHERE input_generation=$1)",
                    structured_faults[0],
                )
            structured = await reserve_structured_ingress_input(
                pool, prompt=prompt, system_prompt=system, tools=tools
            )
            async with pool.acquire() as observed:
                structured_row = await observed.fetchrow(
                    "SELECT * FROM location_ingress_structured_inputs WHERE input_generation=$1",
                    structured,
                )
                assert structured_row["copy_generation"] == child.generation
                assert structured_row["request_id"] == request_id
                assert structured_row["prompt_digest"] == hashlib.sha256(prompt.encode()).digest()
                assert structured_row["system_digest"] == hashlib.sha256(system.encode()).digest()
                assert structured_row["tools_digest"] == content_digest({"tools": tools})
                with pytest.raises(
                    asyncpg.RaiseError, match="Location source floors are permanent"
                ):
                    async with observed.transaction():
                        await observed.execute(
                            "UPDATE location_ingress_structured_inputs SET tools_digest=$2 "
                            "WHERE input_generation=$1",
                            structured,
                            b"x" * 32,
                        )
                assert await observed.fetchval(
                    "SELECT tools_digest FROM location_ingress_structured_inputs "
                    "WHERE input_generation=$1",
                    structured,
                ) == content_digest({"tools": tools})
            result_calls = [{"name": "synthetic fixed tool", "input": {"synthetic": True}}]
            output_faults = []

            async def output_fault(conn, sql, *args, **kwargs):
                result = await original_execute(conn, sql, *args, **kwargs)
                if sql.startswith("INSERT INTO location_ingress_structured_outputs "):
                    assert conn.is_in_transaction()
                    assert await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_outputs "
                        "WHERE input_generation=$1 AND output_digest=$2)",
                        *args,
                    )
                    output_faults.append(args[0])
                    raise asyncpg.RaiseError("Synthetic structured output reservation fault")
                return result

            with patch.object(asyncpg.pool.PoolConnectionProxy, "execute", new=output_fault):
                with pytest.raises(
                    asyncpg.RaiseError, match="Synthetic structured output reservation fault"
                ):
                    await capture_structured_ingress_output(
                        pool, structured, tool_calls=result_calls, text="Synthetic result"
                    )
            assert output_faults == [structured]
            async with pool.acquire() as observed:
                assert not await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_outputs "
                    "WHERE input_generation=$1)",
                    structured,
                )
                assert await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_inputs "
                    "WHERE input_generation=$1 AND copy_generation=$2)",
                    structured,
                    child.generation,
                )
            await capture_structured_ingress_output(
                pool, structured, tool_calls=result_calls, text="Synthetic result"
            )
            async with pool.acquire() as observed:
                digest = content_digest({"tool_calls": result_calls, "text": "Synthetic result"})
                assert (
                    await observed.fetchval(
                        "SELECT output_digest FROM location_ingress_structured_outputs "
                        "WHERE input_generation=$1",
                        structured,
                    )
                    == digest
                )
                with pytest.raises(
                    asyncpg.RaiseError, match="Location source floors are permanent"
                ):
                    async with observed.transaction():
                        await observed.execute(
                            "UPDATE location_ingress_structured_outputs SET output_digest=$2 "
                            "WHERE input_generation=$1",
                            structured,
                            b"x" * 32,
                        )
                assert (
                    await observed.fetchval(
                        "SELECT output_digest FROM location_ingress_structured_outputs "
                        "WHERE input_generation=$1",
                        structured,
                    )
                    == digest
                )
            async with pool.acquire() as observed:
                frozen = await observed.fetchrow(
                    "SELECT r.*,b.prompt_digest AS composed_prompt,b.exclusive_input,"
                    "e.receipt_id AS context_ended FROM location_ingress_runtime_inputs r "
                    "LEFT JOIN location_runtime_context_bindings b USING(input_generation) "
                    "LEFT JOIN location_runtime_context_ended e USING(input_generation) "
                    "WHERE r.input_generation=$1",
                    binding.generation,
                )
                canonical = await observed.fetchrow(
                    "SELECT raw_payload,normalized_text,request_context FROM message_inbox WHERE id=$1",
                    request_id,
                )
                assert frozen["copy_generation"] == child.generation
                assert frozen["request_id"] == request_id
                assert frozen["receiving_session"] == session
                assert frozen["envelope_digest"] == child.envelope_digest
                assert frozen["prompt_digest"] == hashlib.sha256(prompt.encode()).digest()
                assert frozen["composed_prompt"] == frozen["prompt_digest"]
                assert frozen["stored_digest"] == content_digest(
                    {
                        "raw_payload": canonical["raw_payload"],
                        "normalized_text": canonical["normalized_text"],
                    }
                )
                assert frozen["exclusive_input"] is False and frozen["context_ended"] is not None
                with pytest.raises(
                    asyncpg.RaiseError, match="Location source floors are permanent"
                ):
                    async with observed.transaction():
                        await observed.execute(
                            "UPDATE location_ingress_runtime_inputs SET prompt_digest=$2 "
                            "WHERE input_generation=$1",
                            binding.generation,
                            b"x" * 32,
                        )
                assert (
                    await observed.fetchval(
                        "SELECT prompt_digest FROM location_ingress_runtime_inputs "
                        "WHERE input_generation=$1",
                        binding.generation,
                    )
                    == frozen["prompt_digest"]
                )
                with pytest.raises(asyncpg.ForeignKeyViolationError):
                    async with observed.transaction():
                        await observed.execute(
                            "INSERT INTO location_ingress_runtime_inputs "
                            "(input_generation,copy_generation,receiving_session,request_id,"
                            "stored_digest,envelope_digest,prompt_digest) "
                            "VALUES($1,$2,$3,$4,$5,$6,$7)",
                            uuid4(),
                            uuid4(),
                            uuid4(),
                            request_id,
                            frozen["stored_digest"],
                            frozen["envelope_digest"],
                            frozen["prompt_digest"],
                        )
                completed.append((request_id, session, canonical["request_context"]["dedupe_key"]))
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    runtime_probe[0] = process
    raw = dict(
        _type="location", tid="RC", tst=int(datetime.now(UTC).timestamp()), lat=1.2, lon=103.6
    )
    envelope = build_location_envelope(
        raw, "owntracks:runtime-" + str(uuid4()), datetime.now(UTC).isoformat(), "full"
    )
    try:
        async with Client(endpoint + "/mcp") as caller:
            result = await caller.call_tool("ingest", envelope)
            assert result.data["status"] == "accepted"
        async with asyncio.timeout(10):
            await done.wait()
        if errors:
            raise errors[0]
        assert len(completed) == 1
        request_id, session, key = completed[0]
        from butlers.core.location_ingress_copies import _writers

        async with asyncio.timeout(10):
            while True:
                await _writers[pool].reconcile_observed_ends()
                async with pool.acquire() as observed:
                    canonical = await observed.fetchrow(
                        "SELECT raw_payload,normalized_text FROM message_inbox WHERE id=$1",
                        request_id,
                    )
                    try:
                        async with observed.transaction():
                            await require_ingress_closed(observed, request_id, key, canonical)
                    except CopyFloorUnavailable as exc:
                        if str(exc) == "ingress_runtime_cohort_pending":
                            break  # Every original ingress end is reached; runtime still survives.
                        if str(exc) not in {
                            "ingress_input_cohort_pending",
                            "ingress_server_cohort_pending",
                        }:
                            raise
                    else:
                        raise AssertionError("Stored classifier runtime must remain held")
                await asyncio.sleep(0.01)
        async with pool.acquire() as observed:
            assert (
                await observed.fetchval("SELECT prompt FROM sessions WHERE id=$1", session)
                == prompt
            )
            assert not await observed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions d "
                "JOIN location_ingress_runtime_inputs r USING(input_generation) "
                "WHERE r.receiving_session=$1)",
                session,
            )
    finally:
        runtime_probe[0] = None
        _dispatchers.pop(pool, None)
        runtime.close()
