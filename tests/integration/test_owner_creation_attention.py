"""D7/Q3=A: actual cookie -> MCP -> owning transaction and current cause.

All credentials and messages are synthetic. Collection is not PostgreSQL proof;
these owners require the normal migrated hosted real-Postgres lane.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP

from butlers.api.deps import get_db, get_mcp_manager
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.owner_auth.service import AuthError, _digest
from butlers.api.routers.schedules import router
from butlers.config import ButlerType
from butlers.core.attention_urgency import AttentionOrigin, _current_origin, evaluate_interruption
from butlers.core.owner_creation import (
    META_KEY,
    CreationClaim,
    OwnerCreationMiddleware,
    _pending,
    argument_digest,
)
from butlers.core.scheduler import schedule_create, schedule_toggle, schedule_update, tick
from butlers.core_tools._base import ToolContext
from butlers.core_tools._scheduling import register_scheduling_tools
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.owner_auth_fixtures import ORIGIN, RP
from tests.integration.test_dashboard_stepup import authority as authority

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
        schemas={"core": "general", "approvals": "general"},
    )


@pytest.fixture
async def attention_env(authority, migrated_db_url):
    migration, service, key, handle, session, admin = authority

    async def initialize(connection):
        await register_jsonb_codec(connection)
        await connection.execute("SET ROLE butler_general_rw")

    application_name = "attention_owner_" + uuid4().hex
    domain = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=initialize,
        server_settings={"search_path": "general,public", "application_name": application_name},
    )
    try:
        assert await domain.fetchval("SELECT current_user") == "butler_general_rw"
        assert not await domain.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        for table in ("tickets", "stamps", "candidate_causes", "final_claims"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await domain.fetchval(f"SELECT count(*) FROM dashboard_attention.{table}")
        authority_value = await service.authorize(
            session.token,
            csrf_token=session.data["csrf_token"],
            unsafe=True,
        )
        yield SimpleNamespace(
            admin=admin,
            application_name=application_name,
            migration=migration,
            domain=domain,
            service=service,
            session=session,
            key=key,
            handle=handle,
            authority=authority_value,
        )
    finally:
        await domain.close()


async def issue(env, raw, *, operation=None, target="general"):
    nonce = secrets.token_urlsafe(32)
    operation = operation or str(uuid4())
    digest = argument_digest(raw)
    issued = await env.service.attention_call(
        "issue",
        env.authority,
        env.session.data["csrf_token"],
        operation=operation,
        target=target,
        request_digest=digest,
        nonce_digest=_digest(nonce),
    )
    return issued, CreationClaim(nonce, _digest(env.session.token), operation, digest, target)


async def create(env, claim, *, name=None):
    token = _pending.set(claim)
    try:
        return await schedule_create(
            env.domain,
            name or "synthetic_" + uuid4().hex,
            "0 9 * * *",
            "Synthetic reminder",
            stagger_key="general",
        )
    finally:
        _pending.reset(token)


@asynccontextmanager
async def browser(env):
    server = FastMCP("general")
    server.add_middleware(OwnerCreationMiddleware("general"))
    context = ToolContext(
        daemon=SimpleNamespace(config=SimpleNamespace(name="general")),
        pool=env.domain,
        spawner=None,
        butler_name="general",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )

    def core_tool(_group, **kwargs):
        return server.tool(**kwargs)

    register_scheduling_tools(context, server, core_tool)
    config = OwnerAuthConfig(ORIGIN, RP)
    app = FastAPI()
    app.state.owner_auth_service = env.service
    app.add_middleware(OwnerAuthMiddleware, config=config)
    app.include_router(router)
    # Actual authenticated audit writes use an owning database manager; this
    # wrapper does not mint authority or write schedule state through a read pool.
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(
        pool=lambda _name: env.admin,
        butler_names=["general"],
    )
    async with Client(server) as mcp:

        async def get_client(_name):
            return mcp

        app.dependency_overrides[get_mcp_manager] = lambda: SimpleNamespace(get_client=get_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=ORIGIN,
        ) as client:
            client.cookies.set(config.owner_cookie, env.session.token)
            yield client, mcp, {"Origin": ORIGIN, "X-CSRF-Token": env.session.data["csrf_token"]}


async def test_actual_cookie_mcp_commit_unknown_retry_and_deleted_task_not_recreated(attention_env):
    env = attention_env
    # Reach the fixed SQL boundary directly: middleware-only CSRF refusals
    # would not catch an absent, expired or cross-session csrf row here.
    context = await env.service.context()
    options = await env.service.login_options(context.token, context.data["csrf_token"])
    env.key.counter += 1
    other_session = await env.service.finish_login(
        context.token,
        context.data["csrf_token"],
        options["ceremony_id"],
        env.key.response(options, register=False, user_handle=env.handle),
    )
    assert _digest(other_session.token) != env.authority.session_digest
    expired = await env.service.csrf(env.session.token)
    await env.admin.execute(
        "UPDATE dashboard_auth.csrf SET expires_at=clock_timestamp()-interval '1 second' "
        "WHERE session_digest=$1 AND digest=$2",
        env.authority.session_digest,
        _digest(expired["csrf_token"]),
    )
    protected_clause = await env.service.attention_call(
        "clause_create",
        env.authority,
        env.session.data["csrf_token"],
        entity_id=str(uuid4()),
        scope="always",
    )
    populations = (
        "dashboard_attention.tickets",
        "dashboard_attention.stamps",
        "dashboard_attention.candidate_causes",
        "dashboard_attention.final_claims",
        "public.break_through_clauses",
        "general.scheduled_tasks",
    )
    before = {
        table: await env.admin.fetchval(f"SELECT count(*) FROM {table}") for table in populations
    }
    for csrf in ("", "synthetic-wrong", expired["csrf_token"], other_session.data["csrf_token"]):
        for action in ("issue", "clause_create", "clause_revoke"):
            with pytest.raises(AuthError) as denied_auth:
                await env.service.attention_call(
                    action,
                    env.authority,
                    csrf,
                    operation=str(uuid4()),
                    target="general",
                    request_digest="a" * 64,
                    nonce_digest="b" * 64,
                    entity_id=str(uuid4()),
                    scope="always",
                    id=protected_clause["id"],
                )
            assert denied_auth.value.code == "FORBIDDEN"
    assert before == {
        table: await env.admin.fetchval(f"SELECT count(*) FROM {table}") for table in populations
    }
    assert (
        await env.admin.fetchval(
            "SELECT revoked_at FROM public.break_through_clauses WHERE id=$1",
            UUID(protected_clause["id"]),
        )
        is None
    )
    # Rotation uses the canonical core240 writer; the new token remains valid
    # at the fixed attention interface without a synthetic sessions column.
    rotated = await env.service.csrf(env.session.token)
    assert rotated["csrf_token"] != env.session.data["csrf_token"]
    rotated_operation = uuid4()
    assert await env.service.attention_call(
        "issue",
        env.authority,
        rotated["csrf_token"],
        operation=str(rotated_operation),
        target="general",
        request_digest="c" * 64,
        nonce_digest="d" * 64,
    ) == {"state": "issued"}
    assert (
        await env.admin.fetchval(
            "SELECT session_digest FROM dashboard_attention.tickets WHERE operation=$1",
            rotated_operation,
        )
        == env.authority.session_digest
    )
    body = {
        "name": "http_" + uuid4().hex,
        "cron": "0 9 * * *",
        "prompt": "Synthetic reminder",
        "interrupt_owner": True,
    }
    operation = str(uuid4())
    async with browser(env) as (client, _, headers):
        headers = headers | {"X-Butlers-Creation-Operation": operation}
        denied = await client.post(
            "/api/butlers/general/schedules",
            json=body,
            headers=headers | {"X-CSRF-Token": "synthetic-wrong"},
        )
        assert denied.status_code in {401, 403}
        first = await client.post("/api/butlers/general/schedules", json=body, headers=headers)
        assert first.status_code == 201
        value = first.json()["data"]
        assert set(value) == {"id", "status"} and value["status"] == "created"
        task = UUID(value["id"])
        repeated = await client.post("/api/butlers/general/schedules", json=body, headers=headers)
        assert repeated.status_code == 201 and repeated.json() == first.json()
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM general.scheduled_tasks WHERE id=$1",
                task,
            )
            == 1
        )
        assert (
            await env.admin.fetchval(
                "SELECT count(*) FROM dashboard_attention.stamps WHERE operation=$1",
                UUID(operation),
            )
            == 1
        )
        changed = await client.post(
            "/api/butlers/general/schedules", json=body | {"prompt": "Edited"}, headers=headers
        )
        assert changed.status_code == 403
        await env.admin.execute("DELETE FROM general.scheduled_tasks WHERE id=$1", task)
        lost_ack = await client.post("/api/butlers/general/schedules", json=body, headers=headers)
        assert lost_ack.status_code == 201 and lost_ack.json() == first.json()
        assert not await env.admin.fetchval(
            "SELECT EXISTS(SELECT FROM general.scheduled_tasks WHERE id=$1)", task
        )
        retained = await env.admin.fetchrow(
            "SELECT to_jsonb(t) AS value FROM dashboard_attention.tickets t WHERE operation=$1",
            UUID(operation),
        )
        assert "Synthetic reminder" not in str(retained["value"])
        assert env.session.token not in str(retained["value"])


async def test_model_forgery_hash_raw_digest_and_wrong_domain_never_create_stamp(attention_env):
    env = attention_env
    raw = {"name": "forged_" + uuid4().hex, "cron": "0 9 * * *", "prompt": "Synthetic reminder"}
    _, claim = await issue(env, raw)
    async with browser(env) as (_, mcp, _):
        for wrong in (
            claim.__dict__ | {"nonce": _digest(claim.nonce)},
            claim.__dict__ | {"session_digest": "a" * 64},
            claim.__dict__ | {"target": "home"},
            claim.__dict__ | {"operation": str(uuid4())},
        ):
            result = await mcp.call_tool(
                "schedule_create", raw, meta={META_KEY: wrong}, raise_on_error=False
            )
            assert result.is_error or result.data.get("status") == "error"
        changed = await mcp.call_tool(
            "schedule_create",
            raw | {"prompt": "Edited"},
            meta={META_KEY: claim.__dict__},
            raise_on_error=False,
        )
        assert changed.is_error
        ordinary = await mcp.call_tool("schedule_create", raw)
        assert ordinary.data["status"] == "created"
        assert not await env.admin.fetchval(
            "SELECT EXISTS(SELECT FROM dashboard_attention.stamps WHERE task_id=$1)",
            UUID(ordinary.data["id"]),
        )
        assert _pending.get() is None


async def test_real_transaction_duplicate_race_and_write_failure_leave_one_receipt(attention_env):
    env = attention_env
    name = "race_" + uuid4().hex
    raw = {"name": name, "cron": "0 9 * * *", "prompt": "Synthetic reminder"}
    _, claim = await issue(env, raw)
    first, second = await asyncio.gather(
        create(env, claim, name=name), create(env, claim, name=name)
    )
    assert first == second
    assert (
        await env.admin.fetchval("SELECT count(*) FROM general.scheduled_tasks WHERE id=$1", first)
        == 1
    )
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM dashboard_attention.stamps WHERE task_id=$1", first
        )
        == 1
    )
    _, failing = await issue(env, raw | {"different": "raw shape"})
    with pytest.raises(ValueError, match="already exists"):
        await create(env, failing, name=name)  # duplicate unique definition rolls back consume
    row = await env.admin.fetchrow(
        "SELECT consumed_tx,task_id FROM dashboard_attention.tickets WHERE operation=$1",
        UUID(failing.operation),
    )
    assert row["consumed_tx"] is None and row["task_id"] is None
    assert (
        await env.admin.fetchval("SELECT count(*) FROM general.scheduled_tasks WHERE name=$1", name)
        == 1
    )


@pytest.mark.parametrize(
    "change", ["session_revoke", "credential_epoch", "expired", "concurrent_revoke"]
)
async def test_current_auth_fence_refuses_unconsumed_ticket_without_effects(attention_env, change):
    env = attention_env
    raw = {"name": "fence_" + uuid4().hex}
    _, claim = await issue(env, raw)
    pending = None
    original_epoch = None
    try:
        if change == "concurrent_revoke":
            async with env.admin.acquire() as holder, holder.transaction():
                await holder.execute(
                    "SELECT singleton FROM dashboard_auth.instance WHERE singleton FOR UPDATE"
                )
                pending = asyncio.create_task(create(env, claim, name=raw["name"]))
                try:

                    async def wait_for_real_auth_fence():
                        while not await env.admin.fetchval(
                            "SELECT EXISTS(SELECT FROM pg_stat_activity WHERE application_name=$1 "
                            "AND wait_event_type='Lock' AND query LIKE '%dashboard_attention.consume%')",
                            env.application_name,
                        ):
                            if pending.done():
                                raise AssertionError("Creation did not wait for the auth fence")
                            await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_for_real_auth_fence(), 5)
                    await holder.execute(
                        "UPDATE dashboard_auth.sessions SET revoked=true WHERE digest=$1",
                        claim.session_digest,
                    )
                except BaseException:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
        elif change == "session_revoke":
            await env.admin.execute(
                "UPDATE dashboard_auth.sessions SET revoked=true WHERE digest=$1",
                claim.session_digest,
            )
        elif change == "credential_epoch":
            original_epoch = await env.admin.fetchval(
                "SELECT credential_epoch FROM dashboard_auth.instance WHERE singleton"
            )
            await env.admin.execute(
                "UPDATE dashboard_auth.instance SET credential_epoch=credential_epoch+1 WHERE singleton"
            )
        else:
            await env.admin.execute(
                "UPDATE dashboard_attention.tickets SET expires_at=clock_timestamp()-interval '1 second' WHERE operation=$1",
                UUID(claim.operation),
            )
        with pytest.raises(ValueError, match="Owner creation refused"):
            if pending is None:
                await create(env, claim, name=raw["name"])
            else:
                await asyncio.wait_for(pending, 5)
        assert not await env.admin.fetchval(
            "SELECT EXISTS(SELECT FROM general.scheduled_tasks WHERE name=$1)", raw["name"]
        )
        assert not await env.admin.fetchval(
            "SELECT EXISTS(SELECT FROM dashboard_attention.stamps WHERE operation=$1)",
            UUID(claim.operation),
        )
    finally:
        if original_epoch is not None:
            # The negative must refuse before cleanup; never leave this
            # module-scoped auth database enrolled at an impossible epoch.
            await env.admin.execute(
                "UPDATE dashboard_auth.instance SET credential_epoch=$1 WHERE singleton",
                original_epoch,
            )
            assert (
                await env.admin.fetchval(
                    "SELECT credential_epoch FROM dashboard_auth.instance WHERE singleton"
                )
                == original_epoch
            )


async def test_actual_clause_authoring_scope_current_revoke_and_immutable_creation_definition(
    attention_env,
):
    env = attention_env
    entity = uuid4()
    async with browser(env) as (client, _, headers):
        path = "/api/butlers/attention/break-through-clauses"
        refused = await client.post(path, json={"scope": "always"}, headers=headers)
        assert refused.status_code == 422
        response = await client.post(
            path, json={"entity_id": str(entity), "scope": "quiet_hours_only"}, headers=headers
        )
        assert response.status_code == 201
        ref = response.json()["data"]["id"]
        origin = AttentionOrigin(entity_id=entity)
        assert not (
            await evaluate_interruption(env.domain, gate="context", origin=origin)
        ).interrupts
        decision = await evaluate_interruption(env.domain, gate="quiet_hours", origin=origin)
        assert decision.cause == "owner_clause" and decision.ref == ref
        assert not (
            await evaluate_interruption(
                env.domain, gate="quiet_hours", origin=AttentionOrigin(entity_id=uuid4())
            )
        ).interrupts
        assert (await client.delete(path + "/" + ref, headers=headers)).status_code == 200
        assert not (
            await evaluate_interruption(env.domain, gate="quiet_hours", origin=origin)
        ).interrupts
        expired = await client.post(
            path,
            json={
                "entity_id": str(entity),
                "scope": "always",
                "expires_at": (
                    await env.admin.fetchval("SELECT clock_timestamp() - interval '1 second'")
                ).isoformat(),
            },
            headers=headers,
        )
        assert expired.status_code == 201
        assert not (
            await evaluate_interruption(env.domain, gate="context", origin=origin)
        ).interrupts
    _, claim = await issue(env, {"name": "definition"})
    task = await create(env, claim)
    occurrence = AttentionOrigin(
        namespace="general", task_id=task, original_next_run_at=datetime.now(UTC)
    )
    assert (
        await evaluate_interruption(env.domain, gate="context", origin=occurrence)
    ).cause == "owner_creation"
    assert not (
        await evaluate_interruption(
            env.domain, gate="context", origin=AttentionOrigin(namespace="general", task_id=task)
        )
    ).interrupts
    await env.admin.execute("UPDATE general.scheduled_tasks SET prompt='Edited' WHERE id=$1", task)
    assert not (
        await evaluate_interruption(env.domain, gate="context", origin=occurrence)
    ).interrupts
    assert (
        await env.admin.fetchval(
            "SELECT count(*) FROM dashboard_attention.stamps WHERE task_id=$1", task
        )
        == 1
    )
    # Real cookie -> raw MCP -> owning create -> actual final tick. Unlike the
    # direct occurrence above, this exercises enabled=false BEFORE dispatch.
    now = datetime.now(UTC)
    due = now - timedelta(seconds=1)
    final_name = "final_" + uuid4().hex
    async with browser(env) as (client, _, headers):
        with patch("butlers.core.scheduler._next_run", return_value=due):
            response = await client.post(
                "/api/butlers/general/schedules",
                json={
                    "name": final_name,
                    "cron": "0 9 * * *",
                    "prompt": "Synthetic final",
                    "until_at": (now + timedelta(seconds=30)).isoformat(),
                    "interrupt_owner": True,
                },
                headers=headers | {"X-Butlers-Creation-Operation": str(uuid4())},
            )
    assert response.status_code == 201
    final_task = UUID(response.json()["data"]["id"])
    original_stamp = await env.migration.fetchval(
        "SELECT to_jsonb(s) FROM dashboard_attention.stamps s WHERE task_id=$1", final_task
    )
    seen = []
    # Execute the actual registered notify closure and its final current-cause
    # recheck. Only recipient/permission collaborators and external delivery are
    # synthetic here; creation, claim, digest, current policy and deferred writes
    # use the real migrated domain connection/roles.
    from butlers.core_tools._notifications import register_notification_tools

    deliver_client = AsyncMock()
    deliver_client.call_tool.return_value = SimpleNamespace(
        is_error=False, data={"status": "ok", "notification_id": "synthetic-final"}
    )
    tools = {}

    def core_tool(_group, **_kwargs):
        def register(fn):
            tools[fn.__name__] = fn
            return fn

        return register

    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=env.domain),
        switchboard_client=deliver_client,
        _resolve_default_notify_recipient=AsyncMock(return_value="synthetic-owner"),
        _approval_push_runtime=None,
    )
    register_notification_tools(
        ToolContext(
            daemon=daemon,
            pool=env.domain,
            spawner=None,
            butler_name="general",
            butler_type=ButlerType.BUTLER,
            is_switchboard=False,
            is_messenger=False,
            route_metrics=None,
        ),
        None,
        core_tool,
    )

    async def deliver_final():
        with (
            patch(
                "butlers.core_tools._notifications.check_permission",
                new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
            ),
            patch(
                "butlers.core.approvals_hooks.check_recipient",
                new=AsyncMock(
                    return_value=SimpleNamespace(allowed=True, reason="owner", dossier_error=None)
                ),
            ),
            patch(
                "butlers.core.temporal.delivery_db.get_delivery_preferences",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.core.approvals_policy.get_approvals_policy_quiet_hours",
                new=AsyncMock(
                    return_value={"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
                ),
            ),
            patch(
                "butlers.core.approvals_policy.policy_quiet_hours_deliver_at",
                return_value=datetime.now(UTC) + timedelta(hours=1),
            ),
            patch(
                "butlers.core_tools._notifications.get_suppressing_context",
                new=AsyncMock(return_value=None),
            ),
        ):
            return await tools["notify"](channel="telegram", message="Synthetic final")

    async def final_dispatch(**kwargs):
        assert kwargs["trigger_source"] == "schedule:" + final_name
        origin = _current_origin.get()
        assert origin is not None and origin.task_id == final_task
        row = await env.domain.fetchrow(
            "SELECT enabled,next_run_at FROM general.scheduled_tasks WHERE id=$1", final_task
        )
        assert not row["enabled"] and row["next_run_at"] is None
        # Both actual delivery consumers read this fixed current evaluator;
        # neither supplied urgency nor a locator is a grant.
        assert (await evaluate_interruption(env.domain, gate="quiet_hours")).interrupts
        assert (await evaluate_interruption(env.domain, gate="context")).interrupts
        before_delivery = deliver_client.call_tool.await_count
        assert (await deliver_final())["status"] == "ok"
        assert deliver_client.call_tool.await_count == before_delivery + 1
        seen.append(origin)
        return {"status": "ok"}

    with patch("butlers.core.scheduler._next_run", return_value=now + timedelta(days=1)):
        assert await tick(env.domain, final_dispatch, stagger_key="general") == 1
    assert len(seen) == 1 and _current_origin.get() is None
    cached = seen[0]
    assert (await evaluate_interruption(env.domain, gate="context", origin=cached)).interrupts
    assert (
        await env.migration.fetchval(
            "SELECT to_jsonb(s) FROM dashboard_attention.stamps s WHERE task_id=$1", final_task
        )
        == original_stamp
    )
    witness = await env.migration.fetchrow(
        "SELECT original_next_run_at,revoked FROM dashboard_attention.final_claims WHERE task_id=$1",
        final_task,
    )
    assert witness["original_next_run_at"] == due and not witness["revoked"]
    wrong = AttentionOrigin(
        namespace="general", task_id=final_task, original_next_run_at=due - timedelta(seconds=1)
    )
    assert not (await evaluate_interruption(env.domain, gate="context", origin=wrong)).interrupts
    # Even a domain caller cannot manufacture a final claim after the actual
    # claim transaction; xmin must be the UPDATE's current transaction.
    assert not await env.domain.fetchval(
        "SELECT dashboard_attention.final_claim('general',$1,$2)", final_task, due
    )
    assert not await env.domain.fetchval(
        "SELECT dashboard_attention.final_claim('home',$1,$2)", final_task, due
    )
    # The real edit path revokes the witness in the same UPDATE. A rolled-back
    # edit must not leave a revocation behind; neither does completion.
    async with env.domain.acquire() as conn:
        change = conn.transaction()
        await change.start()
        try:
            await schedule_update(conn, final_task, prompt="Edited final")
            assert not (await evaluate_interruption(conn, gate="context", origin=cached)).interrupts
        finally:
            await change.rollback()
    assert (await evaluate_interruption(env.domain, gate="context", origin=cached)).interrupts
    disabled = await schedule_toggle(env.domain, final_task, enabled=False)
    assert disabled["outcome"] == "already_requested"
    assert not (await evaluate_interruption(env.domain, gate="context", origin=cached)).interrupts
    calls_before = deliver_client.call_tool.await_count
    token = _current_origin.set(cached)
    try:
        assert (await deliver_final())["status"] == "deferred"
    finally:
        _current_origin.reset(token)
    assert deliver_client.call_tool.await_count == calls_before
    await schedule_toggle(env.domain, final_task, enabled=True, stagger_key="general")
    assert not (await evaluate_interruption(env.domain, gate="context", origin=cached)).interrupts


async def test_broker_uses_real_owner_clause_not_rank_and_rechecks_revoke_and_payload(
    attention_env,
):
    from unittest.mock import AsyncMock, patch

    from butlers.core.attention_urgency import _current_origin
    from butlers.tools.switchboard.insight.broker import (
        create_insight_tables,
        delivery_cycle,
        propose_insight_candidate,
    )

    env = attention_env
    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    # This is the actual recorded migration login, not privileged bootstrap.
    # The reusable authority fixture exposes the actual migrated owning pool.
    # It can read the private cause store only through its fixed functions.
    pool = env.migration
    await create_insight_tables(pool)
    await pool.execute("UPDATE insight_settings SET verbosity='normal' WHERE id=1")
    entity = uuid4()
    raw = await env.service.attention_call(
        "clause_create",
        env.authority,
        env.session.data["csrf_token"],
        entity_id=str(entity),
        scope="always",
    )
    ref = raw["id"]
    origin = AttentionOrigin(entity_id=entity)
    token = _current_origin.set(origin)
    try:
        for suffix, priority in (("low", 10), ("revoked", 20), ("edited", 30)):
            result = await propose_insight_candidate(
                pool,
                origin_butler="general",
                priority=priority,
                category="synthetic",
                dedup_key=f"general:attention:{uuid4().hex}:{suffix}",
                message="Synthetic private candidate",
                expires_at=now + timedelta(days=1),
                now=now,
            )
            assert result["status"] == "accepted"
    finally:
        _current_origin.reset(token)
    # High-ranked model work has no accepted private origin and stays pending.
    key = f"general:attention:{uuid4().hex}:forged"
    assert (
        await propose_insight_candidate(
            pool,
            origin_butler="general",
            priority=100,
            category="synthetic",
            dedup_key=key,
            message="Synthetic forged urgency",
            expires_at=now + timedelta(days=1),
            now=now,
            metadata={"attention_cause": "owner_creation", "task_id": str(uuid4())},
        )
    )["status"] == "accepted"
    rows = await pool.fetch(
        "SELECT id,message FROM insight_candidates WHERE category='synthetic' AND status='pending'"
    )
    ids = [r["id"] for r in rows if r["message"] == "Synthetic private candidate"]
    assert len(ids) == 3
    # The actual Switchboard runtime can use only the fixed private projection
    # functions. It cannot read the cause table; a domain runtime cannot mint
    # candidate provenance through that Switchboard-only producer interface.
    async with pool.acquire() as runtime, runtime.transaction():
        await runtime.execute("SET LOCAL ROLE butler_switchboard_rw")
        assert await runtime.fetchval("SELECT current_user") == "butler_switchboard_rw"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.fetchval("SELECT count(*) FROM dashboard_attention.candidate_causes")
        assert await runtime.fetchval(
            "SELECT dashboard_attention.remember($1,$2::jsonb)", ids[0], origin.record()
        )
        assert (
            await runtime.fetchval("SELECT dashboard_attention.candidate_origin($1)", ids[0])
            == origin.record()
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await env.domain.fetchval(
            "SELECT dashboard_attention.remember($1,$2::jsonb)", ids[0], origin.record()
        )
    await pool.execute(
        "UPDATE insight_candidates SET message='Changed payload' WHERE id=$1", ids[0]
    )
    sent = AsyncMock(return_value={"status": "ok"})
    with patch(
        "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
        AsyncMock(return_value="dnd"),
    ):
        first = await delivery_cycle(pool, notify_fn=sent, urgent_only=True, now=now)
        assert len(first["delivered"]) == 2
        assert str(ids[0]) not in first["delivered"]
        sent.assert_awaited_once()
        assert "Synthetic forged urgency" not in sent.await_args.args[0]
        assert (
            await pool.fetchval("SELECT status FROM insight_candidates WHERE dedup_key=$1", key)
            == "pending"
        )
        await env.service.attention_call(
            "clause_revoke", env.authority, env.session.data["csrf_token"], id=str(ref)
        )
        # A failed/changed attempt cannot inherit an old classification.
        await pool.execute(
            "UPDATE insight_candidates SET message='Synthetic private candidate' WHERE id=$1",
            ids[0],
        )
        assert await pool.fetchval("SELECT dashboard_attention.candidate_origin($1)", ids[0])
        sent.reset_mock()
        again = await delivery_cycle(pool, notify_fn=sent, urgent_only=True, now=now)
        assert again["delivered"] == []
        sent.assert_not_awaited()


async def test_core_only_migration_replay_rollback_and_hostile_catalogue_refusal(
    postgres_container,
):
    from urllib.parse import urlparse

    from psycopg2.errors import RaiseException

    from alembic import command
    from butlers.migrations import _build_alembic_config
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url,
    )

    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        chains=["core"],
        revisions={"core": "core_277"},
    )
    config = _build_alembic_config(url, chains=["core"])
    bootstrap_url = migration_bootstrap_db_url(postgres_container, name)
    admin = await asyncpg.connect(bootstrap_url.replace("postgresql+psycopg2", "postgresql"))
    migration_role = urlparse(url).username
    try:
        # Use the actual production core240 owner, never an elevated fixture login.
        assert (
            await admin.fetchval(
                "SELECT migration_role FROM dashboard_attention.bootstrap_configuration WHERE singleton"
            )
            == migration_role
        )
        assert (
            await admin.fetchval(
                "SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner "
                "WHERE n.nspname='dashboard_auth'"
            )
            == migration_role
        )
        assert not await admin.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname=$1", migration_role
        )
        # Each hostile catalogue is rolled back before the next real installer call.
        mutations = [
            "COMMENT ON SCHEMA dashboard_auth IS 'not-the-core240-marker'",
            "ALTER SCHEMA dashboard_auth OWNER TO butler_general_rw",
        ]
        for table in ("instance", "sessions", "csrf"):
            mutations.extend(
                [
                    f"ALTER TABLE dashboard_auth.{table} OWNER TO butler_general_rw",
                    f"DROP TABLE dashboard_auth.{table} CASCADE",
                ]
            )
        for mutation in mutations:
            with pytest.raises(asyncpg.RaiseError, match="attention requires trusted owner"):
                async with admin.transaction():
                    await admin.execute(mutation)
                    await admin.execute("SELECT dashboard_attention.install_interface()")
        for table in ("instance", "sessions", "csrf"):
            with pytest.raises(asyncpg.RaiseError, match="attention requires trusted owner"):
                async with admin.transaction():
                    await admin.execute(f"DROP TABLE dashboard_auth.{table} CASCADE")
                    await admin.execute(
                        f"CREATE VIEW dashboard_auth.{table} AS SELECT true AS singleton"
                    )
                    await admin.execute(
                        f'ALTER VIEW dashboard_auth.{table} OWNER TO "{migration_role}"'
                    )
                    await admin.execute("SELECT dashboard_attention.install_interface()")
        # The other supported bootstrap-owned topology must also really install.
        privileged = admin.transaction()
        await privileged.start()
        try:
            bootstrap_role = await admin.fetchval("SELECT current_user")
            quoted_bootstrap = '"' + bootstrap_role.replace('"', '""') + '"'
            assert await admin.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname=$1", bootstrap_role
            )
            await admin.execute(f"ALTER SCHEMA dashboard_auth OWNER TO {quoted_bootstrap}")
            for table in ("instance", "sessions", "csrf"):
                await admin.execute(
                    f"ALTER TABLE dashboard_auth.{table} OWNER TO {quoted_bootstrap}"
                )
            await admin.execute("SELECT dashboard_attention.install_interface()")
            for relation in (
                "dashboard_attention.tickets",
                "dashboard_attention.stamps",
                "dashboard_attention.candidate_causes",
                "dashboard_attention.final_claims",
                "public.break_through_clauses",
            ):
                assert await admin.fetchval("SELECT to_regclass($1)", relation) is not None
        finally:
            await privileged.rollback()
        assert await admin.fetchval("SELECT to_regclass('dashboard_attention.tickets')") is None
        # This is the genuine normal Alembic caller and ordinary owner positive.
        await asyncio.to_thread(command.upgrade, config, "core@core_278")
        assert await admin.fetchval("SELECT to_regclass('public.approval_rules')") is None
        assert await admin.fetchval("SELECT to_regclass('dashboard_attention.tickets')") is not None
        # Bootstrap-only synthetic historical row: retention/catalogue control,
        # never claimed as a genuine owner-authoring or sender positive.
        clause = await admin.fetchval(
            "INSERT INTO public.break_through_clauses(channel,scope) VALUES('email','always') RETURNING id"
        )
        await asyncio.to_thread(command.downgrade, config, "core@core_277")
        assert await admin.fetchval(
            "SELECT revoked_at IS NOT NULL FROM public.break_through_clauses WHERE id=$1", clause
        )
        assert not await admin.fetchval(
            "SELECT has_function_privilege($1,'dashboard_attention.evaluate(jsonb)','EXECUTE')",
            migration_role,
        )
        before = await admin.fetchval(
            "SELECT to_jsonb(c)::text FROM public.break_through_clauses c WHERE id=$1", clause
        )
        await asyncio.to_thread(command.upgrade, config, "core@core_278")
        assert before == await admin.fetchval(
            "SELECT to_jsonb(c)::text FROM public.break_through_clauses c WHERE id=$1", clause
        )
        await admin.execute(
            "GRANT SELECT(nonce_digest) ON dashboard_attention.tickets TO butler_general_rw"
        )
        await asyncio.to_thread(_bootstrap_migration_prerequisites, bootstrap_url, migration_role)
        assert not await admin.fetchval(
            "SELECT has_column_privilege('butler_general_rw','dashboard_attention.tickets','nonce_digest','SELECT')"
        )
        assert before == await admin.fetchval(
            "SELECT to_jsonb(c)::text FROM public.break_through_clauses c WHERE id=$1", clause
        )
        for mutation in (
            "ALTER TABLE dashboard_auth.csrf OWNER TO butler_general_rw",
            "DROP TABLE dashboard_auth.csrf",
        ):
            with pytest.raises(asyncpg.RaiseError, match="attention requires trusted owner"):
                async with admin.transaction():
                    await admin.execute(mutation)
                    await admin.execute("SELECT dashboard_attention.finalize_interface()")
        await admin.execute(
            f'ALTER FUNCTION dashboard_attention.evaluate(jsonb) OWNER TO "{migration_role}"'
        )
        with pytest.raises(RaiseException, match="untrusted attention function catalog") as refused:
            await asyncio.to_thread(
                _bootstrap_migration_prerequisites, bootstrap_url, migration_role
            )
        assert refused.value.pgcode == "P0001"
        assert refused.value.diag.message_primary == "untrusted attention function catalog"
    finally:
        await admin.close()
