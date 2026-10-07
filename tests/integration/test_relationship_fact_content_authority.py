"""Migrated local Relationship authority; no provider/deployed/custody proof.

The existing bootstrap and actual SET ROLE pools run the production writer,
registered MCP and protected HTTP handlers. Synthetic accepted inbox rows are
local input, not evidence that an external transport was authenticated.
REQ-relationship-facts-006, REQ-relationship-facts-007,
REQ-relationship-facts-008, REQ-entity-identity-003,
REQ-dashboard-relationship-004.
"""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from starlette.applications import Starlette

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import OwnerAuthService
from butlers.core import fact_authority as admission
from butlers.core.fact_authority import FactSourceContextRegistry, FactWriteContext
from butlers.daemon import ButlerDaemon
from butlers.db import register_jsonb_codec
from butlers.identity import resolve_contact_by_channel, resolve_outbound_channel
from butlers.migrations import run_migrations
from butlers.modules._roster_relationship import RelationshipModule
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.operations import approve_action
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from butlers.tools.relationship.fact_authority import stored_gap_authority
from butlers.tools.relationship.fact_identity_decisions import (
    IdentityDecisionConflict,
    attribution_select_sql,
    decide_identity_fact,
)
from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    assert_prefers_channel,
    relationship_assert_fact,
)
from butlers.tools.switchboard.registry.registry import register_butler
from tests.relationship_authority_helpers import baseline_writer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@asynccontextmanager
async def _tcp(app):
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
                    pytest.fail("registered listener stopped before startup")
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


@pytest.fixture(scope="module")
async def env(postgres_container):
    # Real lifecycle topology: core in public and both daemons, then their
    # actual module/roster chains. No copied table or foreign grant.
    url = await asyncio.to_thread(
        create_migrated_test_db, postgres_container, migration_db_name(), ["core"]
    )
    for schema in ("relationship", "switchboard"):
        await run_migrations(url, chain="core", schema=schema)
    for chain, schema in (
        ("memory", "relationship"),
        ("approvals", "relationship"),
        ("switchboard", "switchboard"),
        ("relationship", "relationship"),
    ):
        await run_migrations(url, chain=chain, schema=schema)
    # Replay the genuine privileged bootstrap against this disposable database
    # after all chains, so its grant loop cannot be mistaken for an ACL boundary.
    # Domain controls below still execute under the actual runtime SET ROLE pools.
    parsed = urlparse(url)
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
        parsed.username,
    )

    async def pool(schema, role=None):
        async def setup(conn):
            if role:
                await conn.execute(f"SET ROLE {role}")

        return await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=6,
            init=register_jsonb_codec,
            setup=setup,
            server_settings={"search_path": f"{schema},public"},
        )

    admin = await pool("public")
    rel = await pool("relationship", "butler_relationship_rw")
    switchboard = await pool("switchboard", "butler_switchboard_rw")
    owner = await admin.fetchval(
        "INSERT INTO public.entities(canonical_name,entity_type,roles) "
        "VALUES('Synthetic owner','person',ARRAY['owner']) RETURNING id"
    )
    approvals = ApprovalsModule()
    await approvals.on_startup({}, SimpleNamespace(pool=rel))
    try:
        state = SimpleNamespace(url=url, admin=admin, rel=rel, sw=switchboard, owner=owner)
        # A migrated daemon owns one registered MCP lifecycle until shutdown.
        # Reconstructing the entire tool server for every report can time out
        # during Client initialization before the owning resolver ever runs.
        # Nested report scopes below reuse this actual live endpoint, never a
        # substitute resolver or typed report.
        async with _registered(state):
            yield state
    finally:
        await approvals.on_shutdown()
        await asyncio.gather(rel.close(), switchboard.close(), admin.close())
        _ISSUERS.clear()


async def _person(env, *, name="Synthetic person", metadata=None):
    return await env.admin.fetchval(
        "INSERT INTO public.entities(canonical_name,entity_type,metadata) "
        "VALUES($1,'person',$2) RETURNING id",
        f"{name} {uuid.uuid4()}",
        metadata or {},
    )


_ISSUERS: list[FactSourceContextRegistry] = []


def _source_issuer(env):
    issuer = FactSourceContextRegistry(env.sw)
    _ISSUERS.append(issuer)
    return issuer


async def _report(env, entity, authority="third_party"):
    """Capture through the actual owning MCP and accepted-row producer.

    A synthetic legacy handle is fixture input, not sender authentication.
    The expected authority checks the source-derived result; it cannot supply it.
    """
    address = f"{entity}@example.test"
    await env.admin.execute(
        "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
        "VALUES($1,'has-email',$2,'literal','legacy') ON CONFLICT DO NOTHING",
        entity,
        address,
    )
    async with _registered(env):
        issuer = _source_issuer(env)
        owning = await resolve_contact_by_channel(env.rel, "email", address, raise_on_error=True)
        assert owning is not None and owning.entity_id == entity
        registered = (await issuer.resolve_identities("email", [address]))[address]
        assert registered is not None and registered.entity_id == entity
        accepted = await _accepted(env, address)
        source = await env.sw.fetchrow(
            "SELECT request_context FROM switchboard.message_inbox WHERE id=$1", accepted
        )
        assert source["request_context"]["source_channel"] == "email"
        assert source["request_context"]["source_sender_identity"] == address
        report = await issuer.capture_accepted_report(accepted, entity)
    assert report.authority == authority
    return report


@asynccontextmanager
async def _context(report):
    """Online source verification precedes the direct owning-writer scope.

    This uses real disposable HTTP and the production verifier. An arbitrary
    typed report/copy cannot enter the fixture: it must be the actual object
    captured from an issuer's durable accepted row. MCP invocation and runtime
    guard coverage remains a separate registered-tool assertion below.
    """
    issuer = next(
        (
            source
            for source in _ISSUERS
            if any(value is report for value in source._reports.values())
        ),
        None,
    )
    if issuer is None:
        if report != FactWriteContext("system"):
            raise ValueError("test report lacks an actual accepted-source producer")
        # Existing no-context trusted internal policy; never a caller SYSTEM flag.
        token = admission._current_report.set(None)
        try:
            yield
        finally:
            admission._current_report.reset(token)
        return
    request_id = next(key for key, value in issuer._reports.items() if value is report)
    capability = issuer.issue(request_id, "relationship")
    source_token = admission._incoming_source.set(capability)
    receipt_token = admission._incoming_receipt.set(None)
    try:
        async with _tcp(Starlette(routes=[issuer.route()])) as endpoint:
            verified = await admission.admit_incoming_source(
                "relationship", str(uuid.uuid4()), endpoint
            )
            assert verified.to_record() == report.to_record()
            token = admission._current_report.set(verified)
            try:
                yield
            finally:
                admission._current_report.reset(token)
    finally:
        admission._incoming_receipt.reset(receipt_token)
        admission._incoming_source.reset(source_token)


async def _assert(env, subject, predicate, value, report, **kwargs):
    async with _context(report):
        return await relationship_assert_fact(
            env.rel, subject, predicate, value, src="relationship", **kwargs
        )


@asynccontextmanager
async def _registered(env):
    endpoint = getattr(env, "_registered_endpoint", None)
    if endpoint is not None:
        # Restore the fixed live target after a receiver-recovery negative
        # temporarily publishes its own endpoint; do not reconstruct a daemon.
        await register_butler(env.sw, "relationship", endpoint + "/mcp")
        yield endpoint
        return
    module = RelationshipModule()
    mcp = FastMCP("relationship")
    await module.register_tools(mcp, None, SimpleNamespace(pool=env.rel), "relationship")
    app = ButlerDaemon._build_mcp_http_app(mcp, butler_name="relationship")
    try:
        async with _tcp(app) as endpoint:
            await register_butler(env.sw, "relationship", endpoint + "/mcp")
            env._registered_endpoint = endpoint
            try:
                yield endpoint
            finally:
                del env._registered_endpoint
    finally:
        await module.on_shutdown()


async def _accepted(
    env, sender, *, channel="email", metadata=None, text="Synthetic report", canonical=None
):
    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    context = {"source_channel": channel, "source_sender_identity": sender}
    raw = {"metadata": metadata or {}}
    if canonical is not None:
        from butlers.tools.switchboard.ingestion.ingest import (
            IngestEnvelopeV1,
            _build_request_context,
        )

        parsed = IngestEnvelopeV1.model_validate(canonical)
        context = _build_request_context(parsed, request_id=row_id, received_at=now)
        raw = {key: canonical[key] for key in ("source", "event", "sender", "payload", "control")}
        text = parsed.payload.normalized_text
    # Production partition installer is outside the insert transaction.
    await env.sw.execute("SELECT switchboard_message_inbox_ensure_partition($1)", now)
    await env.sw.execute(
        "INSERT INTO switchboard.message_inbox "
        "(id,received_at,request_context,raw_payload,normalized_text) "
        "VALUES($1,$2,$3,$4,$5)",
        row_id,
        now,
        context,
        raw,
        text,
    )
    return row_id


async def _wait_for_lock(pool, pid):
    async with asyncio.timeout(5):
        while not await pool.fetchval(
            "SELECT wait_event_type='Lock' FROM pg_catalog.pg_stat_activity WHERE pid=$1",
            pid,
        ):
            await asyncio.sleep(0.01)


async def _router():
    name = "relationship_api_router"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[2] / "roster/relationship/api/router.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@asynccontextmanager
async def _owner_app(env, *, browser=False):
    # The real SQL authentication service and actual middleware admit this
    # synthetic configured credential. No synthetic principal override.
    config = OwnerAuthConfig(
        "https://butlers.example.test",
        "butlers.example.test",
        api_key=None if browser else "synthetic-current-test-key",
    )
    service = OwnerAuthService(env.admin, config)
    outcome = await env.admin.fetchval(
        "SELECT dashboard_auth.host($1,$2::jsonb)",
        "reconcile_mode",
        service._config() | {"confirm_revoke": True},
    )
    assert "error" not in outcome
    app = FastAPI()
    app.state.owner_auth_service = service
    app.add_middleware(OwnerAuthMiddleware, config=config)
    module = await _router()
    app.include_router(module.router)
    manager = SimpleNamespace(pool=lambda _name: env.rel)
    app.dependency_overrides[module._get_db_manager] = lambda: manager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://butlers.example.test"
    ) as client:
        if browser:
            from tests.api.test_owner_auth_store import enrolled

            _, _, issued = await enrolled(service)
            client.cookies.set(config.owner_cookie, issued.token)
            client._authority_test_session = issued
            yield (
                client,
                service,
                {"Origin": config.origin, "X-CSRF-Token": issued.data["csrf_token"]},
            )
        else:
            yield client, service, {"X-API-Key": config.api_key}


async def test_registered_source_resolver_and_writer_are_role_owned(env):
    """Owning MCP positive accompanies genuine Switchboard SQL denial."""
    reporter, subject = await _person(env), await _person(env)
    address = f"{reporter}@example.test"
    legacy = await env.admin.fetchval(
        "INSERT INTO relationship.entity_facts "
        "(subject,predicate,object,object_kind,src,verified) "
        "VALUES($1,'has-email',$2,'literal','legacy',true) RETURNING id",
        reporter,
        address,
    )
    async with env.sw.acquire() as conn:
        assert await conn.fetchval("SELECT current_user") == "butler_switchboard_rw"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT count(*) FROM relationship.entity_facts")
    async with _registered(env) as endpoint:
        issuer = _source_issuer(env)
        accepted = await _accepted(env, address)
        with pytest.raises(ValueError, match="sender resolution mismatch"):
            await issuer.capture_accepted_report(accepted, subject)
        report = await issuer.capture_accepted_report(accepted, reporter)
        assert report.authority == "third_party" and report.live_entity_id == reporter
        assert (
            await env.admin.fetchval(
                "SELECT content_authority FROM relationship.entity_facts WHERE id=$1", legacy
            )
            is None
        )
        prior = admission.source_registry()
        admission.register_source_registry(issuer)
        source_token = admission._pipeline_source.set(accepted)
        try:
            invocation = await admission.register_invocation(
                "relationship", str(uuid.uuid4()), source_endpoint=None, routed=True
            )
            async with Client(
                StreamableHttpTransport(
                    endpoint + "/mcp", headers={admission.INVOCATION_HEADER: invocation}
                )
            ) as client:
                result = await client.call_tool(
                    "relationship_assert_fact",
                    {
                        "subject": str(subject),
                        "predicate": "has-email",
                        "object": "reported@example.test",
                    },
                )
            assert result.data["outcome"] == "candidate"
            row = await env.admin.fetchrow(
                "SELECT * FROM relationship.entity_facts WHERE id=$1",
                uuid.UUID(result.data["fact_id"]),
            )
            assert row["content_authority"] == "third_party" and not row["verified"]
            assert row["authority_original_entity_id"] == reporter
            assert row["authority_entity_created_at"] == report.entity_created_at
            assert await issuer.resolve_identity(None, "email", address) is not None
            assert await issuer.resolve_identity(None, "email", "reported@example.test") is None
            unknown = await _person(env, metadata={"unidentified": True})
            await issuer.assert_sender_channel(unknown, "email", f"{unknown}@example.test")
            resolved = await issuer.resolve_identity(None, "email", f"{unknown}@example.test")
            assert resolved.entity_id == unknown and resolved.is_unidentified
            # A copied known entity cannot use the deterministic unknown hook.
            await issuer.assert_sender_channel(subject, "email", "hook-forgery@example.test")
            assert await issuer.resolve_identity(None, "email", "hook-forgery@example.test") is None
            await env.admin.execute(
                "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
                "VALUES($1,'has-email',$2,'literal','legacy')",
                env.owner,
                f"{env.owner}@example.test",
            )
            owner_id = await _accepted(env, f"{env.owner}@example.test")
            owner_report = await issuer.capture_accepted_report(owner_id, env.owner)
            assert (
                owner_report.authority == "owner" and owner_report.original_entity_id == env.owner
            )
            # Source audience, claim identity and expiry are actual controls on
            # the same database-backed issuer, not a caller ContextVar producer.
            cap = issuer.issue(owner_id, "relationship")
            with pytest.raises(ValueError, match="unavailable"):
                await issuer.verify(cap, "finance", "incarnation", "invocation")
            assert (
                await issuer.verify(cap, "relationship", "incarnation", "invocation")
            ).owner_class
            with pytest.raises(ValueError, match="already claimed"):
                await issuer.verify(cap, "relationship", "incarnation", "other")
            issuer._claims[cap].expires = 0
            with pytest.raises(ValueError, match="unavailable"):
                await issuer.verify(cap, "relationship", "incarnation", "invocation")

            second_cap = issuer.issue(owner_id, "relationship")
            await issuer.verify(second_cap, "relationship", "first-incarnation", "same-invocation")
            with pytest.raises(ValueError, match="already claimed"):
                await issuer.verify(
                    second_cap, "relationship", "other-incarnation", "same-invocation"
                )

            # A fresh issuer has no old in-memory report. Recovery must obtain
            # the actual receiver's current durable processing lease, then
            # reuse the original source-owned frozen report and exact digest.
            from butlers.core.route_inbox import route_inbox_claim_processing, route_inbox_insert

            original_row = await env.sw.fetchrow(
                "SELECT request_context,raw_payload,normalized_text FROM message_inbox WHERE id=$1",
                owner_id,
            )
            receipt = {
                "request_id": str(owner_id),
                "digest": admission.accepted_source_digest(original_row),
            }
            local_row = await route_inbox_insert(
                env.rel, route_envelope={"_fact_source_receipt": receipt}
            )
            processing_claim = await route_inbox_claim_processing(env.rel, local_row)
            assert processing_claim is not None
            receiver = admission.FactReceiverContextRegistry(env.rel, "relationship")
            restarted = _source_issuer(env)
            assert owner_id not in restarted._reports
            receipt_token = admission._incoming_receipt.set(None)
            try:
                async with (
                    _tcp(Starlette(routes=[receiver.route()])) as receiver_url,
                    _tcp(
                        Starlette(routes=[restarted.route(), restarted.recovery_route()])
                    ) as source_url,
                ):
                    await register_butler(env.sw, "relationship", receiver_url + "/mcp")
                    recovered = await receiver.recover_report(
                        local_row, processing_claim, source_url
                    )
                    assert recovered.to_record() == owner_report.to_record()
                    assert admission._incoming_receipt.get() == receipt
                    with pytest.raises(httpx.HTTPStatusError):
                        await receiver.recover_report(local_row, uuid.uuid4(), source_url)
                    with pytest.raises(ValueError, match="unavailable"):
                        await receiver.recover_report(local_row, processing_claim, None)
                    await env.sw.execute(
                        "UPDATE message_inbox SET normalized_text='Changed synthetic content' WHERE id=$1",
                        owner_id,
                    )
                    with pytest.raises(httpx.HTTPStatusError):
                        await receiver.recover_report(local_row, processing_claim, source_url)
                    await env.sw.execute(
                        "UPDATE message_inbox SET normalized_text=$2 WHERE id=$1",
                        owner_id,
                        original_row["normalized_text"],
                    )
                    restored = await receiver.recover_report(
                        local_row, processing_claim, source_url
                    )
                    assert restored.to_record() == owner_report.to_record()
            finally:
                admission._incoming_receipt.reset(receipt_token)
                await register_butler(env.sw, "relationship", endpoint + "/mcp")

        finally:
            admission.settle_invocation(invocation)
            admission._pipeline_source.reset(source_token)
            admission.register_source_registry(prior)


async def test_caller_verified_and_context_copy_cannot_mint_owner_report(env):
    """The original protected writer demonstrably accepted caller verified."""
    subject = await _person(env)
    historical = baseline_writer()
    before = await historical.relationship_assert_fact(
        env.rel,
        subject,
        "has-email",
        "old-verified@example.test",
        src="relationship",
        verified=True,
    )
    assert (
        await env.admin.fetchval(
            "SELECT verified FROM relationship.entity_facts WHERE id=$1", before.fact_id
        )
        is True
    )
    report = await _report(env, await _person(env))
    # A copied typed value is not the source-owned private producer object.
    with pytest.raises(ValueError, match="actual accepted-source producer"):
        async with _context(FactWriteContext.from_record(report.to_record())):
            await relationship_assert_fact(
                env.rel, subject, "has-email", "copied-context@example.test", src="relationship"
            )
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object='copied-context@example.test'"
        )
        == 0
    )
    corrected = await _assert(
        env, subject, "has-email", "new-unverified@example.test", report, verified=True, conf=0.99
    )
    async with env.admin.acquire() as readback:
        row = await readback.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", corrected.fact_id
        )
        assert row["validity"] == "candidate" and not row["verified"]
        assert row["content_authority"] == "third_party"
    async with _registered(env) as endpoint, Client(endpoint + "/mcp") as client:
        schema = next(
            t for t in await client.list_tools() if t.name == "relationship_assert_fact"
        ).inputSchema
        assert not {"verified", "author", "content_authority", "src"} & schema["properties"].keys()
        forged = await client.call_tool(
            "relationship_assert_fact",
            {
                "subject": str(subject),
                "predicate": "has-email",
                "object": "forged@example.test",
                "verified": True,
            },
            raise_on_error=False,
        )
        assert forged.is_error
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object=$1", "forged@example.test"
        )
        == 0
    )


async def test_protected_adoption_rejection_and_unknown_ack_readback(env, monkeypatch):
    subject, reporter = await _person(env), await _person(env)
    report = await _report(env, reporter)
    adopt = await _assert(env, subject, "has-email", "adopted@example.test", report)
    reject = await _assert(env, subject, "has-email", "rejected@example.test", report)
    async with _owner_app(env) as (client, service, headers):
        path = f"/api/relationship/entities/{subject}/identity-facts/{adopt.fact_id}"
        assert (
            await client.post(path + "/adopt", headers={"X-Butlers-Actor": "owner"})
        ).status_code == 401
        assert (await client.post(path + "/adopt", headers=headers)).status_code == 200
        receipt = await client.get(path + "/decision", headers=headers)
        assert receipt.status_code == 200 and receipt.json()["decision"] == "adopt"
        assert (await client.post(path + "/adopt", headers=headers)).json()["replayed"]
        assert (await client.post(path + "/reject", headers=headers)).status_code == 409
        rejection = await client.post(
            f"/api/relationship/entities/{subject}/identity-facts/{reject.fact_id}/reject",
            headers=headers,
        )
        assert rejection.status_code == 200
        unknown = await _assert(env, subject, "has-email", "unknown-ack@example.test", report)
        unknown_path = f"/api/relationship/entities/{subject}/identity-facts/{unknown.fact_id}"
        actual_transport = client._transport.handle_async_request

        async def lose_only_completed_ack(request):
            response = await actual_transport(request)
            if request.url.path == unknown_path + "/adopt":
                assert response.status_code == 200
                raise httpx.ReadError("synthetic response loss after actual committed mutation")
            return response

        with monkeypatch.context() as patch:
            patch.setattr(client._transport, "handle_async_request", lose_only_completed_ack)
            with pytest.raises(httpx.ReadError, match="response loss"):
                await client.post(unknown_path + "/adopt", headers=headers)
        # Unknown ACK is resolved through the protected durable readback door,
        # not guessed from the response loss or a duplicate domain operation.
        unknown_receipt = await client.get(unknown_path + "/decision", headers=headers)
        assert unknown_receipt.status_code == 200
        assert unknown_receipt.json()["decision"] == "adopt"
        assert (await client.post(unknown_path + "/adopt", headers=headers)).json()["replayed"]
        async with env.admin.acquire() as independent_readback:
            assert (
                await independent_readback.fetchval(
                    "SELECT count(*) FROM relationship.fact_identity_decisions WHERE fact_id=$1",
                    unknown.fact_id,
                )
                == 1
            )
            assert (
                await independent_readback.fetchval(
                    "SELECT validity FROM relationship.entity_facts WHERE id=$1", unknown.fact_id
                )
                == "active"
            )
        assert (
            await client.get(
                f"/api/relationship/entities/{subject}/identity-candidates", headers=headers
            )
        ).json()["facts"] == []
        await service.revoke(api_key=service.config.api_key, all_sessions=True)
    async with _owner_app(env, browser=True) as (client, service, headers):
        assert (
            await client.post(path + "/adopt", headers={"Origin": service.config.origin})
        ).status_code == 403
        assert (
            await client.post(
                path + "/adopt", headers=headers | {"Origin": "https://wrong.example.test"}
            )
        ).status_code == 403
        assert (await client.post(path + "/adopt", headers=headers)).status_code == 200
        issued = client._authority_test_session
        await service.revoke(issued.token, csrf_token=issued.data["csrf_token"])
        assert (await client.post(path + "/adopt", headers=headers)).status_code == 401
        # A fresh real authenticator/session is the positive after revocation;
        # a planted expiry on that actual session makes mutation unavailable.
        from butlers.api.owner_auth.service import _digest
        from tests.api.test_owner_auth_store import enrolled

        _, _, fresh = await enrolled(service, operation="recover")
        client.cookies.set(service.config.owner_cookie, fresh.token)
        headers = {"Origin": service.config.origin, "X-CSRF-Token": fresh.data["csrf_token"]}
        assert (await client.post(path + "/adopt", headers=headers)).status_code == 200
        await env.admin.execute(
            "UPDATE dashboard_auth.sessions SET expires_at=clock_timestamp()-interval '1 second' WHERE digest=$1",
            _digest(fresh.token),
        )
        assert (await client.post(path + "/adopt", headers=headers)).status_code == 401
    async with env.admin.acquire() as readback:
        row = await readback.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", adopt.fact_id
        )
        assert (
            row["content_authority"] == "third_party"
            and row["authority_original_entity_id"] == reporter
        )
        assert row["verified"] and row["confirmed_by_original_entity_id"] == env.owner
        assert (
            await readback.fetchval(
                "SELECT validity FROM relationship.entity_facts WHERE id=$1", reject.fact_id
            )
            == "retracted"
        )
        assert (
            await readback.fetchval(
                "SELECT count(*) FROM relationship.fact_identity_decisions WHERE fact_id=$1",
                adopt.fact_id,
            )
            == 1
        )
        assert (
            await resolve_contact_by_channel(env.rel, "email", "adopted@example.test")
        ).entity_id == subject
        assert await resolve_contact_by_channel(env.rel, "email", "rejected@example.test") is None
        assert (
            await resolve_outbound_channel(env.rel, subject, deliverable_channels={"email"})
            == "email"
        )


async def test_report_versions_birth_witness_and_author_deletion(env):
    subject, first, second = await _person(env), await _person(env), await _person(env)
    # Capture both birth witnesses on the actual accepted-source producer.
    async with _registered(env):
        issuer = _source_issuer(env)
        for entity in (first, second):
            await env.admin.execute(
                "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
                "VALUES($1,'has-email',$2,'literal','legacy')",
                entity,
                f"{entity}@example.test",
            )
        report1 = await issuer.capture_accepted_report(
            await _accepted(env, f"{first}@example.test"), first
        )
        report2 = await issuer.capture_accepted_report(
            await _accepted(env, f"{second}@example.test"), second
        )

    one = await _assert(env, subject, "knows", str(env.owner), report1, object_kind="entity")
    same = await _assert(env, subject, "knows", str(env.owner), report1, object_kind="entity")
    assert same.fact_id == one.fact_id
    two = await _assert(env, subject, "knows", str(env.owner), report2, object_kind="entity")
    assert two.fact_id != one.fact_id
    assert (
        await env.admin.fetchval(
            "SELECT authority_original_entity_id FROM relationship.entity_facts WHERE id=$1",
            one.fact_id,
        )
        == first
    )
    await env.admin.execute("DELETE FROM public.entities WHERE id=$1", second)
    await env.admin.execute(
        "INSERT INTO public.entities(id,canonical_name,entity_type,created_at) VALUES($1,'Replacement','person',$2)",
        second,
        report2.entity_created_at + timedelta(seconds=1),
    )
    retry = await _assert(env, subject, "knows", str(env.owner), report2, object_kind="entity")
    assert retry.fact_id == two.fact_id
    row = await env.admin.fetchrow(
        f"SELECT f.*, {attribution_select_sql()} FROM relationship.entity_facts f WHERE id=$1",
        two.fact_id,
    )
    assert row["authority_entity_id"] is None and row["authority_original_entity_id"] == second
    assert row["reported_by"]["availability"] == "deleted" and row["reported_by"]["name"] is None
    with pytest.raises(asyncpg.RaiseError):
        await env.rel.execute(
            "UPDATE relationship.entity_facts SET authority_original_entity_id=$2 WHERE id=$1",
            two.fact_id,
            first,
        )
    # The read model reports distinct ordinary soft-forget and merge lifecycle
    # states without exposing a stale name/link. No foreign lifecycle service
    # completion is inferred from these deliberately planted canonical rows.
    for marker, availability in (("deleted_at", "forgotten"), ("merged_into", "merged")):
        soft_reporter = await _person(env)
        soft_report = await _report(env, soft_reporter)
        soft_fact = await _assert(
            env, subject, "knows", str(env.owner), soft_report, object_kind="entity"
        )
        await env.admin.execute(
            "UPDATE public.entities SET metadata=metadata || $2::jsonb WHERE id=$1",
            soft_reporter,
            {
                marker: str(uuid.uuid4())
                if marker == "merged_into"
                else datetime.now(UTC).isoformat()
            },
        )
        lifecycle = await env.admin.fetchrow(
            f"SELECT f.*, {attribution_select_sql()} FROM relationship.entity_facts f WHERE id=$1",
            soft_fact.fact_id,
        )
        assert lifecycle["reported_by"]["availability"] == availability
        assert (
            lifecycle["reported_by"]["entity_id"] is None
            and lifecycle["reported_by"]["name"] is None
        )
        assert lifecycle["confirmation_status"] == "unconfirmed"

    # Both lock orders use real connections. No sleeps establish the ordering:
    # the actual entity lock is acquired before the competing task starts.
    delete_first = await _person(env)
    async with _registered(env):
        await env.admin.execute(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES($1,'has-email',$2,'literal','legacy')",
            delete_first,
            f"{delete_first}@example.test",
        )
        issuer = _source_issuer(env)
        old_source = await issuer.capture_accepted_report(
            await _accepted(env, f"{delete_first}@example.test"), delete_first
        )
    async with env.admin.acquire() as deleting, env.rel.acquire() as writing:
        tx = deleting.transaction()
        await tx.start()
        await deleting.execute(
            "SELECT id FROM public.entities WHERE id=$1 FOR UPDATE", delete_first
        )
        pid = await writing.fetchval("SELECT pg_backend_pid()")

        async def write_after_delete():
            async with _context(old_source):
                return await relationship_assert_fact(
                    env.rel,
                    subject,
                    "knows",
                    str(env.owner),
                    object_kind="entity",
                    src="relationship",
                    conn=writing,
                )

        write = asyncio.create_task(write_after_delete())
        await _wait_for_lock(env.admin, pid)
        await deleting.execute("DELETE FROM public.entities WHERE id=$1", delete_first)
        await tx.commit()
        after_delete = await asyncio.wait_for(write, 5)
    assert (
        await env.admin.fetchval(
            "SELECT authority_entity_id FROM relationship.entity_facts WHERE id=$1",
            after_delete.fact_id,
        )
        is None
    )
    async with env.rel.acquire() as writing, env.admin.acquire() as deleting:
        tx = writing.transaction()
        await tx.start()
        async with _context(report1):
            result = await relationship_assert_fact(
                env.rel,
                subject,
                "knows",
                str(env.owner),
                object_kind="entity",
                src="relationship",
                conn=writing,
            )
        pid = await deleting.fetchval("SELECT pg_backend_pid()")
        deletion = asyncio.create_task(
            deleting.execute("DELETE FROM public.entities WHERE id=$1", first)
        )
        await _wait_for_lock(env.admin, pid)
        await tx.commit()
        await asyncio.wait_for(deletion, 5)
    survivor = await env.admin.fetchrow(
        "SELECT authority_entity_id,authority_original_entity_id FROM relationship.entity_facts WHERE id=$1",
        result.fact_id,
    )
    assert (
        survivor["authority_entity_id"] is None
        and survivor["authority_original_entity_id"] == first
    )


async def test_family_gate_and_stored_gap_authority(env):
    subject, relative, reporter = await _person(env), await _person(env), await _person(env)
    report = await _report(env, reporter)
    result = await _assert(
        env,
        subject,
        "parent-of",
        str(relative),
        report,
        object_kind="entity",
        conf=0.95,
        why="Synthetic source report",
        evidence=[],
    )
    assert result.outcome is AssertOutcome.pending_approval and result.action_id
    assert (
        await env.admin.fetchval(
            "SELECT status FROM relationship.pending_actions WHERE id=$1", result.action_id
        )
        == "pending"
    )
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE subject=$1 AND predicate='parent-of'",
            subject,
        )
        == 0
    )
    gap = await env.rel.fetchval(
        "INSERT INTO knowledge_gaps(entity_id,predicate,question_summary) VALUES($1,'knows','Synthetic gap') RETURNING id",
        subject,
    )
    fact = await _assert(env, subject, "knows", str(relative), report, object_kind="entity")
    async with _context(await _report(env, env.owner, "owner")):
        async with env.rel.acquire() as conn:
            stamped = await stored_gap_authority(conn, fact.fact_id)
            assert stamped.authority == "third_party" and stamped.entity_id == reporter
    row = await env.admin.fetchrow("SELECT * FROM relationship.knowledge_gaps WHERE id=$1", gap)
    assert row["status"] == "answerable" and row["answered_authority"] == "third_party"
    assert row["answered_authority_entity_id"] == reporter
    owner_positive = await _assert(
        env,
        subject,
        "parent-of",
        str(relative),
        await _report(env, env.owner, "owner"),
        object_kind="entity",
        conf=0.95,
    )
    assert owner_positive.fact_id and owner_positive.outcome is not AssertOutcome.pending_approval


async def test_approval_replay_original_args_and_legacy_normalization(env, monkeypatch):
    from butlers.modules.approvals.executor import execute_approved_action

    subject = await _person(env)
    report = await _report(env, await _person(env))
    parked = await _assert(
        env,
        env.owner,
        "has-email",
        f"{subject}@example.test",
        report,
        why="Synthetic proposed owner handle",
    )
    assert parked.action_id
    original_args = await env.rel.fetchval(
        "SELECT tool_args FROM pending_actions WHERE id=$1", parked.action_id
    )
    await env.rel.execute(
        "UPDATE pending_actions SET tool_args=$2 WHERE id=$1",
        parked.action_id,
        original_args | {"verified": True},
    )
    # Actual OwnerAuth admission supplies owner_device context to the unchanged
    # approval business transition. The test endpoint owns no synthetic principal.
    async with _owner_app(env) as (client, _service, headers):

        @client._transport.app.post("/api/test-approve")
        async def approve():
            return await approve_action(env.rel, str(parked.action_id))

        assert (await client.post("/api/test-approve")).status_code == 401
        response = await client.post("/api/test-approve", headers=headers)
        assert response.status_code == 200 and "error" not in response.json()

    args = await env.rel.fetchval(
        "SELECT tool_args FROM pending_actions WHERE id=$1", parked.action_id
    )

    # Exact original digest must bind before private keyword retirement. A
    # mismatch cannot reach the fact writer or consume terminal state.
    async def handler(**values):
        assert "verified" not in values
        values["subject"] = uuid.UUID(values["subject"])
        values["approval_action_id"] = uuid.UUID(values["approval_action_id"])
        result = await relationship_assert_fact(env.rel, src="relationship", **values)
        return {"fact_id": str(result.fact_id), "outcome": result.outcome.value}

    wrong = await execute_approved_action(
        env.rel,
        parked.action_id,
        "relationship_assert_fact",
        args | {"object": "wrong@example.test"},
        handler,
    )
    assert not wrong.success and "binding" in wrong.error
    success = await asyncio.wait_for(
        execute_approved_action(
            env.rel, parked.action_id, "relationship_assert_fact", args, handler
        ),
        5,
    )
    assert success.success
    replay = await execute_approved_action(
        env.rel, parked.action_id, "relationship_assert_fact", args, handler
    )
    assert replay.result == success.result
    row = await env.admin.fetchrow(
        "SELECT * FROM relationship.entity_facts WHERE id=$1", uuid.UUID(success.result["fact_id"])
    )
    assert (
        row["content_authority"] == "third_party"
        and row["authority_original_entity_id"] == report.original_entity_id
    )
    assert row["verified"] and row["confirmed_by_original_entity_id"] == env.owner
    assert (
        await env.admin.fetchval(
            "SELECT status FROM relationship.pending_actions WHERE id=$1", parked.action_id
        )
        == "executed"
    )

    # Owner confirmation admits this exact identity assertion. The original
    # reporter remains third-party; confirmation makes its handle eligible.
    assert row["validity"] == "active"
    assert (
        await resolve_contact_by_channel(env.rel, "email", args["object"])
    ).entity_id == env.owner

    from butlers.config import DEFAULT_APPROVAL_RULE_PRECEDENCE, ApprovalRiskTier
    from butlers.modules.approvals.gate import _make_gate_wrapper
    from butlers.modules.approvals.operations import create_approval_rule
    from butlers.tools.relationship.fact_authority import admitted_rule_report

    rule_args = {
        "subject": str(subject),
        "predicate": "has-email",
        "object": f"standing-{subject}@example.test",
        "object_kind": "literal",
    }
    constraints = {key: {"type": "exact", "value": value} for key, value in rule_args.items()}
    # The actual protected creation request supplies owner admission. Actor
    # spelling on an ordinary rule cannot supply that private lineage.
    async with _owner_app(env) as (client, _service, headers):

        @client._transport.app.post("/api/test-rule")
        async def create_rule():
            return await create_approval_rule(
                env.rel, "relationship_assert_fact", constraints, "Synthetic bounded permission"
            )

        assert (await client.post("/api/test-rule")).status_code == 401
        created = await client.post("/api/test-rule", headers=headers)
        assert created.status_code == 200 and "error" not in created.json()
        rule_id = uuid.UUID(created.json()["id"])
    assert (await admitted_rule_report(env.rel, rule_id)).owner_class
    gate = _make_gate_wrapper(
        "relationship_assert_fact",
        handler,
        env.rel,
        48,
        ApprovalRiskTier.MEDIUM,
        DEFAULT_APPROVAL_RULE_PRECEDENCE,
        butler_name="relationship",
    )
    async with _context(report):
        automatic = await asyncio.wait_for(
            gate(**rule_args, why="Synthetic exact standing permission", evidence=[]), 5
        )
    assert "error" not in automatic and automatic["outcome"] == "inserted"
    async with env.admin.acquire() as separate_readback:
        automatic_row = await separate_readback.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", uuid.UUID(automatic["fact_id"])
        )
        assert automatic_row["validity"] == "active" and automatic_row["verified"]
        assert automatic_row["content_authority"] == "third_party"
        assert automatic_row["authority_original_entity_id"] == report.original_entity_id
        assert automatic_row["confirmed_by_original_entity_id"] == env.owner
        assert automatic_row["confirmation_source"] == "standing_rule"
        assert (
            await separate_readback.fetchval(
                "SELECT use_count FROM relationship.approval_rules WHERE id=$1", rule_id
            )
            == 1
        )
        assert (
            await separate_readback.fetchval(
                "SELECT count(*) FROM relationship.approval_events e "
                "JOIN relationship.pending_actions a ON a.id=e.action_id "
                "WHERE e.event_type='action_auto_approved' AND e.rule_id=$1 "
                "AND a.approval_rule_id=$1 AND a.status='executed'",
                rule_id,
            )
            == 1
        )
    assert (
        await resolve_contact_by_channel(env.rel, "email", rule_args["object"])
    ).entity_id == subject

    creation_event = await env.rel.fetchval(
        "SELECT creation_event_id FROM fact_approval_rule_context WHERE rule_id=$1", rule_id
    )
    # A planted wrong creation-event selector neutralizes the new lineage
    # predicate. It cannot become owner permission merely from a copied rule ID.
    await env.rel.execute(
        "UPDATE fact_approval_rule_context SET creation_event_id=$2 WHERE rule_id=$1",
        rule_id,
        str(uuid.uuid4()),
    )
    assert await admitted_rule_report(env.rel, rule_id) is None
    await env.rel.execute(
        "UPDATE fact_approval_rule_context SET creation_event_id=$2 WHERE rule_id=$1",
        rule_id,
        creation_event,
    )
    assert (await admitted_rule_report(env.rel, rule_id)).owner_class

    forged_args = rule_args | {"object": f"forged-rule-{subject}@example.test"}
    forged = await create_approval_rule(
        env.rel,
        "relationship_assert_fact",
        {key: {"type": "exact", "value": value} for key, value in forged_args.items()},
        "Synthetic caller actor is not admission",
        actor_id="owner",
    )
    assert await admitted_rule_report(env.rel, uuid.UUID(forged["id"])) is None
    async with _context(report):
        waiting = await gate(**forged_args, why="Synthetic unadmitted permission", evidence=[])
    assert waiting["status"] == "pending_approval"
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object=$1", forged_args["object"]
        )
        == 0
    )

    # Fact COMMIT and terminal approval acknowledgement are separate. Inject a
    # transport failure only at the latter's write after the real fact handler
    # has returned; no SQL query or writer result is mocked.
    from butlers.modules.approvals import executor

    uncertain_action = await _assert(
        env,
        env.owner,
        "has-email",
        f"uncertain-{subject}@example.test",
        report,
        why="Synthetic approval acknowledgement control",
    )
    assert uncertain_action.action_id
    async with _owner_app(env) as (client, _service, headers):

        @client._transport.app.post("/api/test-approve-uncertain")
        async def approve_uncertain():
            return await approve_action(env.rel, str(uncertain_action.action_id))

        approved = await client.post("/api/test-approve-uncertain", headers=headers)
        assert approved.status_code == 200 and "error" not in approved.json()
    uncertain_args = await env.rel.fetchval(
        "SELECT tool_args FROM pending_actions WHERE id=$1", uncertain_action.action_id
    )
    original_transaction = executor._approval_write_transaction

    @asynccontextmanager
    async def lose_terminal_ack(pool):
        async with original_transaction(pool) as actual_connection:

            class AckLoss:
                def __getattr__(self, name):
                    return getattr(actual_connection, name)

                async def execute(self, query, *values):
                    if query.startswith("UPDATE pending_actions SET status = $1, execution_result"):
                        raise ConnectionError("planted terminal acknowledgement interruption")
                    return await actual_connection.execute(query, *values)

            yield AckLoss()

    with monkeypatch.context() as patch:
        patch.setattr(executor, "_approval_write_transaction", lose_terminal_ack)
        uncertain = await execute_approved_action(
            env.rel, uncertain_action.action_id, "relationship_assert_fact", uncertain_args, handler
        )
    assert not uncertain.success and uncertain.result["outcome"] == "unknown"
    uncertain_fact = uuid.UUID(uncertain.result["fact_id"])
    async with env.admin.acquire() as separate_readback:
        assert (
            await separate_readback.fetchval(
                "SELECT validity FROM relationship.entity_facts WHERE id=$1", uncertain_fact
            )
            == "active"
        )
        pending = await separate_readback.fetchrow(
            "SELECT status,execution_result FROM relationship.pending_actions WHERE id=$1",
            uncertain_action.action_id,
        )
        assert pending["status"] == "approved" and pending["execution_result"] is None
    acknowledged = await execute_approved_action(
        env.rel, uncertain_action.action_id, "relationship_assert_fact", uncertain_args, handler
    )
    assert acknowledged.success and uuid.UUID(acknowledged.result["fact_id"]) == uncertain_fact
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE object=$1",
            uncertain_args["object"],
        )
        == 1
    )


async def test_candidate_lifecycle_merge_collisions_and_concurrent_adoption(env):
    reporter = await _person(env)
    report = await _report(env, reporter)
    left, right = await _person(env), await _person(env)
    handle = f"race-{left}@example.test"
    first = await _assert(env, left, "has-email", handle, report)
    second = await _assert(env, right, "has-email", handle, report)
    owner = await _report(env, env.owner, "owner")

    async def choose(entity, fact):
        async with _context(owner), env.rel.acquire() as conn, conn.transaction():
            return await decide_identity_fact(
                conn, entity_id=entity, fact_id=fact, decision="adopt"
            )

    results = await asyncio.wait_for(
        asyncio.gather(
            choose(left, first.fact_id), choose(right, second.fact_id), return_exceptions=True
        ),
        5,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, IdentityDecisionConflict) for result in results) == 1
    rows = await env.admin.fetch(
        "SELECT validity FROM relationship.entity_facts WHERE object=$1", handle
    )
    assert sorted(row["validity"] for row in rows) == ["active", "candidate"]
    # A compatible active occurrence survives adoption. Its original report
    # stays intact; the candidate and both append-only ledgers remain history.
    compatible_subject = await _person(env)
    compatible_value = f"compatible-{compatible_subject}@example.test"
    survivor = await _assert(
        env,
        compatible_subject,
        "has-email",
        compatible_value,
        owner,
        evidence=[{"type": "text", "ref": "Synthetic owner evidence"}],
    )
    candidate = await _assert(
        env,
        compatible_subject,
        "has-email",
        compatible_value,
        report,
        evidence=[{"type": "text", "ref": "Synthetic reporter evidence"}],
    )
    before_candidate = dict(
        await env.admin.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", candidate.fact_id
        )
    )
    before_survivor = dict(
        await env.admin.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", survivor.fact_id
        )
    )
    converged = await choose(compatible_subject, candidate.fact_id)
    assert converged["fact_id"] == survivor.fact_id and not converged["replayed"]
    assert (await choose(compatible_subject, candidate.fact_id))["replayed"]
    async with env.admin.acquire() as separate:
        historical = dict(
            await separate.fetchrow(
                "SELECT * FROM relationship.entity_facts WHERE id=$1", candidate.fact_id
            )
        )
        assert historical["validity"] == "superseded"
        assert {k: v for k, v in historical.items() if k not in {"validity", "updated_at"}} == {
            k: v for k, v in before_candidate.items() if k not in {"validity", "updated_at"}
        }
        active = await separate.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", survivor.fact_id
        )
        assert active["validity"] == "active" and active["verified"]
        assert (
            active["authority_original_entity_id"]
            == before_survivor["authority_original_entity_id"]
        )
        assert active["confirmed_by_original_entity_id"] == env.owner
        assert (
            await separate.fetchval(
                "SELECT fact_result_id FROM relationship.fact_identity_decisions WHERE fact_id=$1",
                candidate.fact_id,
            )
            == survivor.fact_id
        )
        assert (
            await separate.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id=$1", survivor.fact_id
            )
            == 2
        )
        assert (
            await separate.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id=$1",
                candidate.fact_id,
            )
            == 1
        )

    # A stored known default packet is synthetic fixture input, not temporal
    # cutover admission. The ordinary current writer must preserve it, and an
    # incompatible active packet cannot be silently selected as its survivor.
    packet_subject = await _person(env)
    packet_value = f"packet-{packet_subject}@example.test"
    known = await _assert(env, packet_subject, "has-email", packet_value, report)
    bound = datetime(2026, 1, 1, tzinfo=UTC)
    await env.admin.execute(
        "UPDATE relationship.entity_facts SET effective_from=$2,effective_from_precision='instant' "
        "WHERE id=$1",
        known.fact_id,
        bound,
    )
    replacement = await _assert(env, packet_subject, "has-email", packet_value, report, conf=0.8)
    assert replacement.fact_id != known.fact_id
    replacement_row = await env.admin.fetchrow(
        "SELECT * FROM relationship.entity_facts WHERE id=$1", replacement.fact_id
    )
    assert replacement_row["effective_from"] == bound
    assert replacement_row["effective_from_precision"] == "instant"
    active_packet = await _assert(env, packet_subject, "has-email", packet_value, owner)
    assert active_packet.outcome == AssertOutcome.inserted
    before_packet_rows = [
        dict(r)
        for r in await env.admin.fetch(
            "SELECT * FROM relationship.entity_facts WHERE subject=$1 ORDER BY id", packet_subject
        )
    ]
    with pytest.raises(IdentityDecisionConflict, match="incompatible"):
        await choose(packet_subject, replacement.fact_id)
    assert [
        dict(r)
        for r in await env.admin.fetch(
            "SELECT * FROM relationship.entity_facts WHERE subject=$1 ORDER BY id", packet_subject
        )
    ] == before_packet_rows
    assert not await env.admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM relationship.fact_identity_decisions WHERE fact_id=$1)",
        replacement.fact_id,
    )

    # The adopted core233 phone fallback treats these spellings as one
    # identity. Hold only the native slot in one real transaction so the
    # shared owner-lifetime row cannot mask the exact advisory-lock control.
    phone_left, phone_right = await _person(env), await _person(env)
    phone_a = await _assert(env, phone_left, "has-phone", "+12025550123", report)
    phone_b = await _assert(env, phone_right, "has-phone", "+2025550123", report)
    async with env.rel.acquire() as holding, env.rel.acquire() as competing:
        held = holding.transaction()
        await held.start()
        blocked_phone = None
        try:
            from butlers.tools.relationship.identity_slots import lock_identity_slot

            await lock_identity_slot(holding, "has-phone", "+12025550123", phone_left)
            holding_pid = await holding.fetchval("SELECT pg_backend_pid()")
            competing_pid = await competing.fetchval("SELECT pg_backend_pid()")

            async def competing_phone_adoption():
                async with _context(owner), competing.transaction():
                    return await decide_identity_fact(
                        competing,
                        entity_id=phone_right,
                        fact_id=phone_b.fact_id,
                        decision="adopt",
                    )

            blocked_phone = asyncio.create_task(competing_phone_adoption())
            # An equality-only key would let this alias commit immediately.
            # The holder owns no entity/fact/auth row; the actual adopter must
            # wait specifically on this production phone-slot connection.
            await _wait_for_lock(env.admin, competing_pid)
            assert holding_pid in await env.admin.fetchval(
                "SELECT pg_catalog.pg_blocking_pids($1)", competing_pid
            )
            await held.commit()
            phone_adopted = await asyncio.wait_for(blocked_phone, 5)
            assert phone_adopted["decision"] == "adopt"
        finally:
            if holding.is_in_transaction():
                await held.rollback()
            if blocked_phone is not None:
                if not blocked_phone.done():
                    blocked_phone.cancel()
                await asyncio.gather(blocked_phone, return_exceptions=True)
    with pytest.raises(IdentityDecisionConflict):
        await choose(phone_left, phone_a.fact_id)
    async with env.admin.acquire() as separate_phone_readback:
        assert sorted(
            row["validity"]
            for row in await separate_phone_readback.fetch(
                "SELECT validity FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                [phone_a.fact_id, phone_b.fact_id],
            )
        ) == ["active", "candidate"]
    resolved_phone = await resolve_contact_by_channel(
        env.rel, "whatsapp_jid", "12025550123@s.whatsapp.net"
    )
    assert resolved_phone is not None
    assert resolved_phone.entity_id == phone_right
    # Preference is single-valued per subject, not a shared recipient slot.
    # Both reachable people may independently adopt their email preference.
    preferences = []
    for entity in (phone_left, phone_right):
        reachable = await _assert(
            env, entity, "has-email", f"preferred-{entity}@example.test", owner
        )
        async with _context(report):
            preference = await assert_prefers_channel(env.rel, entity, "email")
        assert reachable.fact_id != preference.fact_id
        preferences.append((entity, preference.fact_id))
    preference_results = await asyncio.wait_for(
        asyncio.gather(*(choose(entity, fact) for entity, fact in preferences)), 5
    )
    assert all(result["decision"] == "adopt" for result in preference_results)
    async with env.admin.acquire() as separate_preference_readback:
        assert (
            await separate_preference_readback.fetchval(
                "SELECT count(*) FROM relationship.entity_facts "
                "WHERE id=ANY($1::uuid[]) AND predicate='prefers-channel' AND validity='active'",
                [fact for _entity, fact in preferences],
            )
            == 2
        )
    # A failed outer domain transaction leaves candidate, effects and receipt
    # untouched. The positive after rollback commits and is separately readable.
    extra = await _assert(env, left, "has-email", f"rollback-{left}@example.test", report)
    with pytest.raises(RuntimeError, match="planted rollback"):
        async with _context(owner), env.rel.acquire() as conn, conn.transaction():
            await decide_identity_fact(
                conn, entity_id=left, fact_id=extra.fact_id, decision="adopt"
            )
            raise RuntimeError("planted rollback")
    assert (
        await env.admin.fetchval(
            "SELECT validity FROM relationship.entity_facts WHERE id=$1", extra.fact_id
        )
        == "candidate"
    )
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.fact_identity_decisions WHERE fact_id=$1",
            extra.fact_id,
        )
        == 0
    )
    await choose(left, extra.fact_id)

    contested = await _assert(env, left, "has-email", f"decision-{left}@example.test", report)
    async with _owner_app(env) as (client, _service, headers):
        path = f"/api/relationship/entities/{left}/identity-facts/{contested.fact_id}"
        replies = await asyncio.wait_for(
            asyncio.gather(
                client.post(path + "/adopt", headers=headers),
                client.post(path + "/reject", headers=headers),
            ),
            5,
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        receipt = await client.get(path + "/decision", headers=headers)
        assert receipt.status_code == 200
        chosen = receipt.json()["decision"]
        assert chosen in {"adopt", "reject"}
    async with env.admin.acquire() as independent_readback:
        assert await independent_readback.fetchval(
            "SELECT validity FROM relationship.entity_facts WHERE id=$1", contested.fact_id
        ) == ("active" if chosen == "adopt" else "retracted")
        assert (
            await independent_readback.fetchval(
                "SELECT count(*) FROM relationship.fact_identity_decisions WHERE fact_id=$1",
                contested.fact_id,
            )
            == 1
        )

    from butlers.tools.relationship.entity_merge import merge_entity_pair

    source, target = await _person(env), await _person(env)
    moving = await _assert(env, source, "has-email", f"merge-{source}@example.test", report)
    await merge_entity_pair(
        env.rel, source_entity_id=source, target_entity_id=target, target_schemas=()
    )
    moved = await env.admin.fetchrow(
        "SELECT * FROM relationship.entity_facts WHERE id=$1", moving.fact_id
    )
    assert moved["subject"] == target and moved["validity"] == "candidate"
    assert moved["authority_original_entity_id"] == reporter
    assert await resolve_contact_by_channel(env.rel, "email", moved["object"]) is None
    # Merge wins its actual row locks before a stale exact-row adoption starts.
    # The adopter must recheck after waiting, not silently retarget its command.
    racing_source, racing_target = await _person(env), await _person(env)
    racing = await _assert(
        env, racing_source, "has-email", f"merge-race-{racing_source}@example.test", report
    )
    async with env.rel.acquire() as merging, env.rel.acquire() as adopting:
        transaction = merging.transaction()
        await transaction.start()

        @asynccontextmanager
        async def merge_connection():
            yield merging

        await merge_entity_pair(
            SimpleNamespace(acquire=merge_connection),
            source_entity_id=racing_source,
            target_entity_id=racing_target,
            target_schemas=(),
        )
        pid = await adopting.fetchval("SELECT pg_backend_pid()")

        async def stale_adoption():
            async with _context(owner), adopting.transaction():
                return await decide_identity_fact(
                    adopting, entity_id=racing_source, fact_id=racing.fact_id, decision="adopt"
                )

        delayed = asyncio.create_task(stale_adoption())
        await _wait_for_lock(env.admin, pid)
        await transaction.commit()
        with pytest.raises(IdentityDecisionConflict):
            await asyncio.wait_for(delayed, 5)
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM relationship.fact_identity_decisions WHERE fact_id=$1",
            racing.fact_id,
        )
        == 0
    )
    await choose(racing_target, racing.fact_id)
    assert (
        await resolve_contact_by_channel(
            env.rel, "email", f"merge-race-{racing_source}@example.test"
        )
    ).entity_id == racing_target
    assert (
        await env.admin.fetchval(
            "SELECT validity FROM relationship.entity_facts WHERE id=$1", extra.fact_id
        )
        == "active"
    )


async def test_google_and_steam_delete_preserve_surviving_report_provenance(env):
    from butlers.google_account_registry import create_google_account
    from butlers.google_account_registry import disconnect_account as google_delete
    from butlers.steam_account_registry import create_steam_account
    from butlers.steam_account_registry import disconnect_account as steam_delete

    subject = await _person(env)
    accounts = [
        (await create_google_account(env.admin, email=f"{subject}@example.test"), google_delete),
        (
            await create_steam_account(
                env.admin, steam_id=76561198000000000 + subject.int % 100000
            ),
            steam_delete,
        ),
    ]
    for account, delete in accounts:
        report = await _report(env, account.entity_id)
        evidence = [
            {
                "type": "url",
                "ref": "https://example.test/synthetic-cleanup",
                "note": "Synthetic reference",
            }
        ]
        surviving = await _assert(
            env, subject, "has-email", f"{account.id}@example.test", report, evidence=evidence
        )
        attached = await _assert(
            env,
            account.entity_id,
            "knows",
            str(subject),
            FactWriteContext("system"),
            object_kind="entity",
            evidence=evidence,
        )
        subject_old = await _assert(
            env, account.entity_id, "knows", str(subject), report, object_kind="entity"
        )
        object_old = await _assert(
            env,
            subject,
            "knows",
            str(account.entity_id),
            FactWriteContext("system"),
            object_kind="entity",
            evidence=evidence,
        )
        object_new = await _assert(
            env, subject, "knows", str(account.entity_id), report, object_kind="entity"
        )
        assert object_old.fact_id != object_new.fact_id
        before_survivor = dict(
            await env.admin.fetchrow(
                "SELECT * FROM relationship.entity_facts WHERE id=$1", surviving.fact_id
            )
        )
        victims = [attached.fact_id, subject_old.fact_id, object_old.fact_id, object_new.fact_id]
        before_evidence = [
            dict(row)
            for row in await env.admin.fetch(
                "SELECT * FROM relationship.fact_evidence WHERE fact_id=$1 ORDER BY id",
                surviving.fact_id,
            )
        ]
        assert before_evidence
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM relationship.fact_evidence WHERE fact_id=ANY($1::uuid[])",
                victims,
            )
            >= 4
        )
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges WHERE source_id=ANY($1::uuid[])",
                victims,
            )
            == 2
        )
        # The production cleanup executes against one genuine acquired
        # connection; its nested transaction is a savepoint. A planted outer
        # rollback must restore both destructive cascades and nullable author.
        async with env.admin.acquire() as connection:

            @asynccontextmanager
            async def held_connection():
                yield connection

            with pytest.raises(RuntimeError, match="planted cleanup rollback"):
                async with connection.transaction():
                    await delete(
                        SimpleNamespace(acquire=held_connection), account.id, hard_delete=True
                    )
                    raise RuntimeError("planted cleanup rollback")
        async with env.admin.acquire() as rollback_readback:
            assert (
                await rollback_readback.fetchval(
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                    victims,
                )
                == 4
            )
            assert (
                dict(
                    await rollback_readback.fetchrow(
                        "SELECT * FROM relationship.entity_facts WHERE id=$1", surviving.fact_id
                    )
                )
                == before_survivor
            )
            assert await rollback_readback.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.entities WHERE id=$1)", account.entity_id
            )
        await delete(env.admin, account.id, hard_delete=True)
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE id=ANY($1::uuid[])",
                [attached.fact_id, subject_old.fact_id, object_old.fact_id, object_new.fact_id],
            )
            == 0
        )

        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM public.entities WHERE id=$1", account.entity_id
            )
            == 0
        )
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE id=$1", attached.fact_id
            )
            == 0
        )
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges WHERE source_id=$1",
                attached.fact_id,
            )
            == 0
        )
        row = await env.admin.fetchrow(
            "SELECT * FROM relationship.entity_facts WHERE id=$1", surviving.fact_id
        )
        assert (
            row["authority_entity_id"] is None
            and row["authority_original_entity_id"] == account.entity_id
        )
        assert row["content_authority"] == "third_party" and row["validity"] == "candidate"
        assert dict(row) == before_survivor | {"authority_entity_id": None}
        assert [
            dict(item)
            for item in await env.admin.fetch(
                "SELECT * FROM relationship.fact_evidence WHERE fact_id=$1 ORDER BY id",
                surviving.fact_id,
            )
        ] == before_evidence
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges WHERE source_id=ANY($1::uuid[])",
                victims,
            )
            == 0
        )

    # A companion with no Relationship references remains ordinarily deletable.
    spare_google = await create_google_account(env.admin, email=f"spare-{subject}@example.test")
    spare_steam = await create_steam_account(
        env.admin, steam_id=76561198100000000 + subject.int % 100000
    )
    for spare, delete in ((spare_google, google_delete), (spare_steam, steam_delete)):
        await delete(env.admin, spare.id, hard_delete=True)
        assert not await env.admin.fetchval(
            "SELECT EXISTS(SELECT 1 FROM public.entities WHERE id=$1)", spare.entity_id
        )


async def test_core_only_dashboard_stamp_and_rollback_refusal(env, postgres_container):
    from butlers.api.routers.conversations import _persist_dashboard_user_message

    # Start below the additive common-table migration. Relationship is absent
    # both before and after the ordinary core upgrade; dashboard cannot depend
    # on a roster daemon having started.
    core_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        revisions={"core": "core_258"},  # pinned-revision: exercise pre-stamp core-only topology
    )
    core = await asyncpg.create_pool(core_url, init=register_jsonb_codec)
    assert await core.fetchval("SELECT to_regnamespace('relationship')") is None
    core_owner = await core.fetchval(
        "INSERT INTO public.entities(canonical_name,entity_type,roles) "
        "VALUES('Synthetic core-only owner','person',ARRAY['owner']) RETURNING id"
    )
    config = OwnerAuthConfig(
        "https://butlers.example.test", "butlers.example.test", api_key="synthetic-current-test-key"
    )
    service = OwnerAuthService(core, config)
    await core.fetchval(
        "SELECT dashboard_auth.host($1,$2::jsonb)",
        "reconcile_mode",
        service._config() | {"confirm_revoke": True},
    )
    app = FastAPI()
    app.state.owner_auth_service = service
    app.add_middleware(OwnerAuthMiddleware, config=config)
    conversation, message_id = uuid.uuid4(), uuid.uuid4()
    await core.execute(
        "INSERT INTO public.dashboard_conversations(id,butler_name) VALUES($1,'relationship')",
        conversation,
    )

    @app.post("/api/test-stamp")
    async def stamp():
        row, inserted = await _persist_dashboard_user_message(
            core,
            conversation_id=conversation,
            message="Synthetic owner content",
            message_id=message_id,
        )
        return {"id": str(row["id"]), "inserted": inserted}

    # Ordinary pre-upgrade requests still work without a fabricated owner
    # stamp. Upgrade installs the common field independently of Relationship.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://butlers.example.test"
    ) as old_client:
        assert (
            await old_client.post("/api/test-stamp", headers={"X-API-Key": config.api_key})
        ).status_code == 200
    await run_migrations(core_url, chain="core")
    await core.expire_connections()
    assert await core.fetchval("SELECT to_regnamespace('relationship')") is None
    assert (
        await core.fetchval(
            "SELECT fact_owner_admission FROM public.dashboard_messages WHERE id=$1", message_id
        )
        is None
    )
    # A retry of an unstamped legacy message must stay unknown. A genuinely
    # new admitted message after upgrade receives its real producer witness.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://butlers.example.test"
    ) as client:
        assert (await client.post("/api/test-stamp")).status_code == 401
        assert (
            await client.post("/api/test-stamp", headers={"X-API-Key": config.api_key})
        ).status_code == 200
        assert (
            await core.fetchval(
                "SELECT fact_owner_admission FROM public.dashboard_messages WHERE id=$1", message_id
            )
            is None
        )
        message_id = uuid.uuid4()
        assert (
            await client.post("/api/test-stamp", headers={"X-API-Key": config.api_key})
        ).status_code == 200
        original = await core.fetchval(
            "SELECT fact_owner_admission FROM public.dashboard_messages WHERE id=$1", message_id
        )
        assert original["authority"] == "owner_device" and original["original_entity_id"] == str(
            core_owner
        )
        assert (await client.post("/api/test-stamp", headers={"X-API-Key": config.api_key})).json()[
            "inserted"
        ] is False
    assert (
        await core.fetchval(
            "SELECT fact_owner_admission FROM public.dashboard_messages WHERE id=$1", message_id
        )
        == original
    )
    await core.close()

    from butlers.api.conversation_envelope import build_dashboard_envelope
    from butlers.api.routers.conversations import _submit_to_switchboard

    # Real protected API writers stamp the exact content before transport.
    # This acceptance adapter stores synthetic local ingress; it does not
    # authenticate an external transport or replace the source verifier.
    live_conversation, live_message = uuid.uuid4(), uuid.uuid4()
    await env.admin.execute(
        "INSERT INTO public.dashboard_conversations(id,butler_name) VALUES($1,'relationship')",
        live_conversation,
    )
    captured_envelopes = []

    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    ingress_mcp = FastMCP("synthetic-local-switchboard")

    @ingress_mcp.tool(name="ingest")
    async def actual_ingest(
        schema_version: str, source: dict, event: dict, sender: dict, payload: dict, control: dict
    ):
        envelope = dict(
            schema_version=schema_version,
            source=source,
            event=event,
            sender=sender,
            payload=payload,
            control=control,
        )
        captured_envelopes.append(envelope)
        response = await ingest_v1(env.sw, envelope, enable_thread_affinity=False)
        return response.model_dump(mode="json")

    # Actual fixed ingest handler/SQL and registered transport, synthetic local
    # network input. This does not authenticate an external dashboard channel.
    class AcceptedLocalIngress:
        async def call_tool(self, name, envelope):
            async with Client(ingress_mcp) as registered_client:
                return await registered_client.call_tool(name, envelope)

    async def local_client(name):
        assert name == "switchboard"
        return AcceptedLocalIngress()

    async with _registered(env), _owner_app(env) as (client, _service, headers):

        @client._transport.app.post("/api/test-dashboard-submit")
        async def submit_dashboard():
            durable, inserted = await _persist_dashboard_user_message(
                env.admin,
                conversation_id=live_conversation,
                message="Synthetic admitted owner content",
                message_id=live_message,
            )
            assert inserted
            envelope = build_dashboard_envelope(
                conversation_id=live_conversation,
                message_id=live_message,
                message_text=durable["content"],
                conversation_context=[{"role": "assistant", "content": "Synthetic prior context"}],
                pinned_target="relationship",
            )
            return await _submit_to_switchboard(
                "relationship",
                envelope,
                mcp_mgr=SimpleNamespace(get_client=local_client),
                owner_source_pool=env.admin,
            )

        assert (await client.post("/api/test-dashboard-submit")).status_code == 401
        received = await client.post("/api/test-dashboard-submit", headers=headers)
        assert received.status_code == 200
    issuer = _source_issuer(env)
    accepted_id = uuid.UUID(received.json()["request_id"])
    device = await issuer.capture_accepted_report(accepted_id, None)
    assert device.authority == "owner_device" and device.original_entity_id == env.owner
    assert captured_envelopes[0]["control"]["pinned_target"] == "relationship"
    stored_origin = await env.sw.fetchrow(
        "SELECT raw_payload,request_context FROM switchboard.message_inbox WHERE id=$1", accepted_id
    )
    assert stored_origin["raw_payload"]["control"]["pinned_target"] == "relationship"
    # Capture/recovery does not upgrade an old unstamped row or a locator whose
    # actual text differs. Both companions are durably planted before reading.
    envelope = captured_envelopes[0]
    wrong_text = await _accepted(
        env,
        envelope["sender"]["identity"],
        channel="dashboard",
        metadata=envelope["payload"]["raw"] | {"message": "Different synthetic content"},
        text=envelope["payload"]["normalized_text"],
    )
    assert (await issuer.capture_accepted_report(wrong_text, None)).authority == "third_party"
    import copy

    for part, key, value in (
        ("event", "external_event_id", str(uuid.uuid4())),
        ("source", "endpoint_identity", "dashboard:web:" + str(uuid.uuid4())),
        ("event", "external_thread_id", str(uuid.uuid4())),
        ("control", "pinned_target", "finance"),
    ):
        copied = copy.deepcopy(envelope)
        copied[part][key] = value
        # Deliberately planted accepted-row conformance input keeps the exact
        # valid stamped locator/text while changing ONE canonical binding. It
        # is not external ingress authentication or a dedupe bypass claim.
        copied_id = await _accepted(
            env, "dashboard:operator", channel="dashboard", canonical=copied
        )
        captured = await issuer.capture_accepted_report(copied_id, None)
        assert captured.authority == "third_party" and captured.original_entity_id is None
    # The planted genuinely bound original still succeeds independently of the
    # copied-stamp negatives, and recovery freezes that first admitted report.
    assert (
        await _source_issuer(env).capture_accepted_report(accepted_id, None)
    ).authority == "owner_device"
    unstamped = uuid.uuid4()
    await env.admin.execute(
        "INSERT INTO public.dashboard_messages(id,conversation_id,role,content) "
        "VALUES($1,$2,'user','Synthetic unadmitted row')",
        unstamped,
        live_conversation,
    )
    legacy_id = await _accepted(
        env,
        "dashboard:operator",
        channel="dashboard",
        metadata={"message_id": str(unstamped), "message": "Synthetic unadmitted row"},
        text="Synthetic unadmitted row",
    )
    legacy_report = await issuer.capture_accepted_report(legacy_id, None)
    assert legacy_report.authority == "third_party" and legacy_report.original_entity_id is None
    written = await _assert(
        env, await _person(env), "has-email", "dashboard-owner@example.test", device
    )
    async with env.admin.acquire() as readback:
        actual = await readback.fetchrow(
            f"SELECT f.*, {attribution_select_sql()} FROM relationship.entity_facts f WHERE id=$1",
            written.fact_id,
        )
        assert actual["content_authority"] == "owner_device" and actual["verified"]
        assert actual["authority_original_entity_id"] == env.owner
        assert actual["validity"] == "active"
        assert actual["confirmed_at"] is not None
        assert actual["confirmed_by_original_entity_id"] == env.owner
        assert actual["confirmation_source"] == "owner_assertion"
        assert actual["confirmation_status"] == "owner_asserted"
    recovered = await _source_issuer(env).capture_accepted_report(accepted_id, None)
    assert recovered.to_record() == device.to_record()
    # Classified data makes downgrade refusal causal, not an empty-store test.
    migration = (
        Path(__file__).resolve().parents[2]
        / "roster/relationship/migrations/037_fact_content_authority.py"
    )
    spec = importlib.util.spec_from_file_location("_authority_owned_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Operation:
        def execute(self, statement):
            self.statement = statement

    operation = Operation()
    module.op = operation
    module.downgrade()
    with pytest.raises(asyncpg.RaiseError, match="authority data exists"):
        await env.admin.execute(operation.statement)
    assert await env.admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts WHERE content_authority IS NOT NULL)"
    )
