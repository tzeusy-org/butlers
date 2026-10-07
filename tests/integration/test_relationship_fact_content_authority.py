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
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import asyncpg
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport

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
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.fact_authority import stored_gap_authority
from butlers.tools.relationship.fact_identity_decisions import (
    IdentityDecisionConflict,
    attribution_select_sql,
    decide_identity_fact,
)
from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    relationship_assert_fact,
)
from butlers.tools.switchboard.registry.registry import register_butler

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
BASE = "461e03b32ac3b88e2a92d487432f77a73aa2b829"


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
        yield SimpleNamespace(url=url, admin=admin, rel=rel, sw=switchboard, owner=owner)
    finally:
        await approvals.on_shutdown()
        await asyncio.gather(rel.close(), switchboard.close(), admin.close())


async def _person(env, *, name="Synthetic person", metadata=None):
    return await env.admin.fetchval(
        "INSERT INTO public.entities(canonical_name,entity_type,metadata) "
        "VALUES($1,'person',$2) RETURNING id",
        name,
        metadata or {},
    )


async def _report(env, entity, authority="third_party"):
    birth = await env.admin.fetchval("SELECT created_at FROM public.entities WHERE id=$1", entity)
    return FactWriteContext(authority, entity, birth, entity)


@asynccontextmanager
async def _context(report):
    token = admission._current_report.set(report)
    try:
        yield
    finally:
        admission._current_report.reset(token)


async def _assert(env, subject, predicate, value, report, **kwargs):
    async with _context(report):
        return await relationship_assert_fact(
            env.rel, subject, predicate, value, src="relationship", **kwargs
        )


@asynccontextmanager
async def _registered(env):
    module = RelationshipModule()
    mcp = FastMCP("relationship")
    await module.register_tools(mcp, None, SimpleNamespace(pool=env.rel), "relationship")
    app = ButlerDaemon._build_mcp_http_app(mcp, butler_name="relationship")
    async with _tcp(app) as endpoint:
        await register_butler(env.sw, "relationship", endpoint + "/mcp")
        yield endpoint
    await module.on_shutdown()


async def _accepted(env, sender, *, channel="email", metadata=None, text="Synthetic report"):
    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    # Production partition installer is outside the insert transaction.
    await env.sw.execute("SELECT switchboard_message_inbox_ensure_partition($1)", now)
    await env.sw.execute(
        "INSERT INTO switchboard.message_inbox "
        "(id,received_at,request_context,raw_payload,normalized_text) "
        "VALUES($1,$2,$3,$4,$5)",
        row_id,
        now,
        {"source_channel": channel, "source_sender_identity": sender},
        {"metadata": metadata or {}},
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
        issuer = FactSourceContextRegistry(env.sw)
        accepted = await _accepted(env, address)
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

        finally:
            admission.settle_invocation(invocation)
            admission._pipeline_source.reset(source_token)
            admission.register_source_registry(prior)


async def test_caller_verified_and_context_copy_cannot_mint_owner_report(env):
    """The original protected writer demonstrably accepted caller verified."""
    subject = await _person(env)
    source = subprocess.run(
        ["git", "show", BASE + ":roster/relationship/tools/relationship_assert_fact.py"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    old = SimpleNamespace()
    historical = ModuleType("_authority_baseline_writer")
    sys.modules[historical.__name__] = historical
    namespace = historical.__dict__
    exec(compile(source, "protected-baseline-relationship_assert_fact.py", "exec"), namespace)
    old.write = namespace["relationship_assert_fact"]
    before = await old.write(
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


async def test_protected_adoption_rejection_and_unknown_ack_readback(env):
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
        issuer = FactSourceContextRegistry(env.sw)
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
    assert (
        row["reported_by"]["availability"] == "unavailable" and row["reported_by"]["name"] is None
    )
    with pytest.raises(asyncpg.RaiseError):
        await env.rel.execute(
            "UPDATE relationship.entity_facts SET authority_original_entity_id=$2 WHERE id=$1",
            two.fact_id,
            first,
        )
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
        issuer = FactSourceContextRegistry(env.sw)
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


async def test_approval_replay_original_args_and_legacy_normalization(env):
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
        surviving = await _assert(env, subject, "has-email", f"{account.id}@example.test", report)
        attached = await _assert(
            env,
            account.entity_id,
            "knows",
            str(subject),
            FactWriteContext("system"),
            object_kind="entity",
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
        )
        object_new = await _assert(
            env, subject, "knows", str(account.entity_id), report, object_kind="entity"
        )
        assert object_old.fact_id != object_new.fact_id
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
