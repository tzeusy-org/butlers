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

    async def owning_process(ref):
        # This is the actual configured buffer consumer lifetime; it proves
        # queue/processing SQL only, not routing or target-model completion.
        actual_refs.append(ref)
        assert ref.message_text and ref.source["provider"] == "owntracks"
        entered.set()
        await release.wait()

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
                    async with connector_pool.acquire() as committed:
                        await require_inputs_ended(
                            committed,
                            sse_point["source_input_generation"],
                            sse_point["logical_source_digest"],
                            sse_point["content_digest"],
                        )
                    sse_inbox = await switchboard.fetchrow(
                        "SELECT raw_payload,normalized_text FROM message_inbox WHERE id=$1",
                        sse_point["accepted_request_id"],
                    )
                    assert sse_inbox["raw_payload"]["payload"]["raw"] == sse_raw
                    assert sse_input["stored_digest"] == content_digest(dict(sse_inbox))
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
